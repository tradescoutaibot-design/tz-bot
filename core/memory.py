"""
Memory — persistent storage for the bot.

THREE layers, in priority order:
  1. Environment variables  — keys/secrets (Railway, never wiped)
  2. data/memory.json       — all learned state, trade history, journal (local PC)
  3. Supabase               — free cloud backup so nothing is ever lost

Keys are ALWAYS read from env vars first so they survive Railway redeployments.
Everything else (learning, trades, journal) is saved to memory.json AND
synced to Supabase after every write so it's never lost.
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from collections import Counter
from core.logger import log

# On local PC: data/memory.json
# On Railway:  /data/memory.json (persistent volume mounted at /data)
_DATA_DIR   = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "../data"))
MEMORY_FILE = os.path.join(_DATA_DIR, "memory.json")

# Keys that should ALWAYS come from environment variables first
ENV_KEY_MAP = {
    "tz_api_key":     "TZ_API_KEY",
    "tz_api_secret":  "TZ_API_SECRET",
    "tz_account_id":  "TZ_ACCOUNT_ID",
    "tz_env":         "TZ_ENV",
    "gemini_api_key": "GEMINI_API_KEY",
    "supabase_url":   "SUPABASE_URL",
    "supabase_key":   "SUPABASE_KEY",
}

DEFAULT = {
    "tz_api_key": "",
    "tz_api_secret": "",
    "tz_account_id": "",
    "tz_env": "paper",
    "gemini_api_key": "",
    "supabase_url": "",
    "supabase_key": "",

    "max_trades_per_day": 3,
    "max_daily_loss_pct": 3.0,
    "default_stop_loss_pct": 2.0,
    "default_position_size_pct": 10.0,
    "active_strategy": "SMA Crossover",
    "watchlist": ["SPY", "AAPL", "TSLA", "QQQ", "NVDA"],

    "learned": {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "best_ticker": None,
        "worst_ticker": None,
        "best_time_of_day": None,
        "strategy_notes": [],
        "adjusted_thresholds": {},
        "last_session_summary": "",
        "what_worked": [],
        "what_failed": [],
    },

    "trade_history": [],
    "journal": [],
    "backtests": [],
}


class Memory:
    def __init__(self):
        self._data = {}
        self._supabase_ok = False

    # ── Load ─────────────────────────────────────────────────

    def load(self):
        """
        Load state in this order:
          1. Start with defaults
          2. Overlay with memory.json (local learned state)
          3. If memory.json missing, try to restore from Supabase
          4. Always overlay keys from environment variables (highest priority)
        """
        os.makedirs(_DATA_DIR, exist_ok=True)
        self._data = dict(DEFAULT)
        self._data["learned"] = dict(DEFAULT["learned"])

        # Step 1 — load from disk
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
                self._data["learned"] = {
                    **DEFAULT["learned"],
                    **saved.get("learned", {})
                }
                log.info(
                    f"Memory loaded — {self._data['learned']['total_trades']} trades, "
                    f"win rate {self._data['learned']['win_rate']}%"
                )
            except Exception as e:
                log.error(f"memory.json load failed: {e} — will try Supabase restore")
                self._restore_from_supabase()
        else:
            log.info("No memory.json found — checking Supabase for restore...")
            self._restore_from_supabase()
            if not os.path.exists(MEMORY_FILE):
                log.info("Starting fresh — memory.json will be created on first save")

        # Step 2 — ALWAYS override keys from env vars (highest priority)
        for mem_key, env_key in ENV_KEY_MAP.items():
            val = os.environ.get(env_key, "").strip()
            if val:
                self._data[mem_key] = val

        # Step 3 — test Supabase connection
        if self._data.get("supabase_url") and self._data.get("supabase_key"):
            self._supabase_ok = self._test_supabase()
            if self._supabase_ok:
                log.info("Supabase cloud sync active")

    # ── Save ─────────────────────────────────────────────────

    def save(self):
        """Save to disk and sync to Supabase."""
        # Don't persist raw keys to disk if they came from env vars
        # (they'll be re-loaded from env on next startup anyway)
        safe = {k: v for k, v in self._data.items()}

        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(MEMORY_FILE, "w") as f:
                json.dump(safe, f, indent=2, default=str)
        except Exception as e:
            log.error(f"memory.json save failed: {e}")

        # Cloud backup
        if self._supabase_ok:
            self._sync_to_supabase()

    # ── Get / Set ─────────────────────────────────────────────

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    # ── Trade logging ─────────────────────────────────────────

    def log_trade(self, trade: dict):
        """Append a completed trade and update all learned stats."""
        trade["logged_at"] = datetime.now().isoformat()
        self._data["trade_history"].append(trade)

        L = self._data["learned"]
        L["total_trades"] += 1
        won = trade.get("pnl", 0) > 0
        if won:
            L["wins"] += 1
        else:
            L["losses"] += 1
        L["win_rate"] = round(L["wins"] / L["total_trades"] * 100, 1)

        wins_pnl = [t["pnl_pct"] for t in self._data["trade_history"] if t.get("pnl", 0) > 0]
        loss_pnl = [t["pnl_pct"] for t in self._data["trade_history"] if t.get("pnl", 0) <= 0]
        if wins_pnl:
            L["avg_win_pct"] = round(sum(wins_pnl) / len(wins_pnl), 2)
        if loss_pnl:
            L["avg_loss_pct"] = round(sum(loss_pnl) / len(loss_pnl), 2)

        # Best/worst ticker by win rate (min 3 trades)
        ticker_wins  = Counter()
        ticker_total = Counter()
        for t in self._data["trade_history"]:
            tk = t.get("ticker", "?")
            ticker_total[tk] += 1
            if t.get("pnl", 0) > 0:
                ticker_wins[tk] += 1
        qualified = {tk: ticker_wins[tk] / ticker_total[tk]
                     for tk in ticker_total if ticker_total[tk] >= 3}
        if qualified:
            L["best_ticker"]  = max(qualified, key=qualified.get)
            L["worst_ticker"] = min(qualified, key=qualified.get)

        # Best time of day (by hour)
        hour_wins  = Counter()
        hour_total = Counter()
        for t in self._data["trade_history"]:
            try:
                hr = datetime.fromisoformat(t.get("entry_time", "")).hour
                hour_total[hr] += 1
                if t.get("pnl", 0) > 0:
                    hour_wins[hr] += 1
            except Exception:
                pass
        hour_wr = {hr: hour_wins[hr] / hour_total[hr]
                   for hr in hour_total if hour_total[hr] >= 3}
        if hour_wr:
            best_hr = max(hour_wr, key=hour_wr.get)
            L["best_time_of_day"] = f"{best_hr:02d}:00"

        note = (
            f"Trade #{L['total_trades']}: {trade.get('action')} {trade.get('ticker')} "
            f"{'WIN' if won else 'LOSS'} {trade.get('pnl_pct', 0):+.1f}% | "
            f"Win rate: {L['win_rate']}%"
        )
        L["strategy_notes"].append(note)
        if len(L["strategy_notes"]) > 100:
            L["strategy_notes"] = L["strategy_notes"][-100:]

        self.save()
        log.info(f"Trade logged + memory saved: {note}")
        return note

    def add_journal(self, text: str):
        self._data["journal"].append({
            "text": text,
            "date": datetime.now().isoformat()
        })
        self.save()
        log.info("Journal entry saved")

    # ── Context summary for agent ─────────────────────────────

    def get_context_summary(self) -> str:
        """Plain-text summary the agent reads at session start."""
        L   = self._data["learned"]
        adj = L.get("adjusted_thresholds", {})
        recent = self._data["trade_history"][-5:]
        recent_str = "\n".join([
            f"  {t.get('action')} {t.get('ticker')} "
            f"{t.get('pnl_pct', 0):+.1f}% ({t.get('strategy', '?')})"
            for t in recent
        ]) or "  No trades yet"

        thresholds_str = "\n".join(
            f"  {k}: {v}" for k, v in adj.items()
        ) or "  Using defaults"

        what_worked = "\n".join(f"  • {w}" for w in L.get("what_worked", [])[:3]) or "  None recorded yet"
        what_failed = "\n".join(f"  • {f}" for f in L.get("what_failed", [])[:3]) or "  None recorded yet"

        return f"""
╔══════════════════════════════════════════╗
║         AGENT MEMORY — SESSION START     ║
╚══════════════════════════════════════════╝
Strategy:       {self._data.get('active_strategy')}
Environment:    {self._data.get('tz_env', 'paper').upper()}
Watchlist:      {', '.join(self._data.get('watchlist', []))}
Max trades/day: {self._data.get('max_trades_per_day')}
Stop loss:      {self._data.get('default_stop_loss_pct')}%
Position size:  {self._data.get('default_position_size_pct')}% of account

LEARNED STATS (all time):
  Trades:    {L['total_trades']}
  Win rate:  {L['win_rate']}%
  Avg win:   {L['avg_win_pct']:+.2f}%
  Avg loss:  {L['avg_loss_pct']:+.2f}%
  Best ticker:     {L['best_ticker'] or '—'}
  Worst ticker:    {L['worst_ticker'] or '—'}
  Best time of day:{L.get('best_time_of_day') or '—'}

GEMINI-ADJUSTED THRESHOLDS:
{thresholds_str}

WHAT WORKED LAST SESSION:
{what_worked}

WHAT FAILED LAST SESSION:
{what_failed}

LAST 5 TRADES:
{recent_str}

LAST SESSION SUMMARY:
  {L.get('last_session_summary') or 'No previous session'}

KEYS:
  TradeZero:  {'✓ loaded' if self._data.get('tz_api_key') else '✗ MISSING — run setup.py'}
  Gemini:     {'✓ loaded' if self._data.get('gemini_api_key') else '✗ not set (rule-based learning active)'}
  Supabase:   {'✓ syncing' if self._supabase_ok else '✗ not configured'}
══════════════════════════════════════════
"""

    # ── Supabase sync ─────────────────────────────────────────

    def _test_supabase(self) -> bool:
        try:
            url  = self._data.get("supabase_url", "").rstrip("/")
            key  = self._data.get("supabase_key", "")
            req  = urllib.request.Request(
                f"{url}/rest/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False

    def _sync_to_supabase(self):
        """
        Upsert the full memory blob to Supabase table: bot_memory
        Table schema (create once in Supabase SQL editor):
            create table bot_memory (
                id text primary key default 'main',
                data jsonb,
                updated_at timestamptz default now()
            );
        """
        try:
            url  = self._data.get("supabase_url", "").rstrip("/")
            key  = self._data.get("supabase_key", "")
            payload = json.dumps({
                "id": "main",
                "data": self._data,
                "updated_at": datetime.now().isoformat()
            }).encode()
            req = urllib.request.Request(
                f"{url}/rest/v1/bot_memory",
                data=payload,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass   # success
        except Exception as e:
            log.error(f"Supabase sync failed: {e}")

    def _restore_from_supabase(self):
        """Pull latest memory from Supabase on startup if local file is missing."""
        url = (os.environ.get("SUPABASE_URL") or self._data.get("supabase_url", "")).rstrip("/")
        key = os.environ.get("SUPABASE_KEY") or self._data.get("supabase_key", "")
        if not url or not key:
            return
        try:
            req = urllib.request.Request(
                f"{url}/rest/v1/bot_memory?id=eq.main&select=data",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                rows = json.loads(resp.read())
                if rows and rows[0].get("data"):
                    restored = rows[0]["data"]
                    self._data.update(restored)
                    self._data["learned"] = {
                        **DEFAULT["learned"],
                        **restored.get("learned", {})
                    }
                    self.save()
                    log.info(
                        f"Memory restored from Supabase — "
                        f"{self._data['learned']['total_trades']} trades recovered"
                    )
        except Exception as e:
            log.error(f"Supabase restore failed: {e}")
