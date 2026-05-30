"""
learning.py — Gemini-powered end-of-day learning engine.

After market close the agent calls learn_from_session().
Gemini analyzes the day's trades + journal entries and writes
specific rule updates back into memory for tomorrow.

Free tier: gemini-1.5-flash, 1500 requests/day, no credit card needed.
Get your key at: https://aistudio.google.com/app/apikey
"""

import json
import urllib.request
import urllib.error
from datetime import datetime
from core.logger import log

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key={api_key}"
)


class LearningEngine:
    def __init__(self, memory):
        self.memory = memory

    # ── Main entry point ─────────────────────────────────────

    async def learn_from_session(self, trades_today: list):
        """
        Called once per day after market close.
        Sends trade log + journal to Gemini, gets back rule updates,
        saves everything to memory so tomorrow's session is smarter.
        """
        api_key = self.memory.get("gemini_api_key", "")

        if not api_key:
            log.info("No Gemini key — running rule-based learning only")
            self._rule_based_learn(trades_today)
            return

        if not trades_today:
            log.info("No trades today — skipping learning")
            return

        log.info(f"Learning from {len(trades_today)} trades with Gemini...")

        prompt = self._build_prompt(trades_today)
        response = self._call_gemini(api_key, prompt)

        if response:
            updates = self._parse_response(response)
            self._apply_updates(updates, trades_today)
            log.info("Gemini learning complete — memory updated for tomorrow")
        else:
            log.info("Gemini unavailable — falling back to rule-based learning")
            self._rule_based_learn(trades_today)

    # ── Prompt builder ────────────────────────────────────────

    def _build_prompt(self, trades_today: list) -> str:
        """Build the prompt Gemini receives each evening."""
        learned = self.memory.get("learned", {})
        history = self.memory.get("trade_history", [])
        journal = self.memory.get("journal", [])
        settings = {
            "active_strategy":        self.memory.get("active_strategy"),
            "default_stop_loss_pct":  self.memory.get("default_stop_loss_pct"),
            "default_position_pct":   self.memory.get("default_position_size_pct"),
            "max_trades_per_day":     self.memory.get("max_trades_per_day"),
            "watchlist":              self.memory.get("watchlist"),
        }

        # Last 3 journal entries
        recent_journal = "\n".join([
            f"  [{j['date']}]: {j['text']}"
            for j in journal[-3:]
        ]) or "  No journal entries yet."

        # All-time stats
        total = learned.get("total_trades", 0)
        wins  = learned.get("wins", 0)
        wr    = learned.get("win_rate", 0)

        trades_str = json.dumps(trades_today, indent=2)
        history_summary = self._summarize_history(history)

        return f"""You are an autonomous trading bot's learning engine.
Your job is to analyze today's trades and output SPECIFIC, ACTIONABLE rule updates.
You must respond ONLY with a valid JSON object — no markdown, no explanation, just JSON.

=== CURRENT SETTINGS ===
{json.dumps(settings, indent=2)}

=== ALL-TIME PERFORMANCE ===
Total trades: {total}
Wins: {wins}
Win rate: {wr}%
Avg win: {learned.get('avg_win_pct', 0):+.2f}%
Avg loss: {learned.get('avg_loss_pct', 0):+.2f}%
Best ticker: {learned.get('best_ticker', 'unknown')}
Worst ticker: {learned.get('worst_ticker', 'unknown')}

=== HISTORICAL PATTERN SUMMARY ===
{history_summary}

=== TODAY'S TRADES ===
{trades_str}

=== RECENT JOURNAL ENTRIES ===
{recent_journal}

=== YOUR TASK ===
Analyze today's trades vs historical patterns and return a JSON object with these exact fields:

{{
  "session_summary": "2-3 sentence plain English summary of today",
  "what_worked": ["list", "of", "specific", "observations"],
  "what_failed": ["list", "of", "specific", "observations"],
  "rule_updates": {{
    "rsi_entry": <number or null — Connors RSI entry threshold, lower = more selective>,
    "sma_fast": <number or null — fast SMA period>,
    "sma_slow": <number or null — slow SMA period>,
    "stop_loss_pct": <number or null — new stop loss %>,
    "position_size_pct": <number or null — new position size % of account>,
    "max_trades_per_day": <number or null>,
    "avoid_tickers": ["tickers to remove from watchlist temporarily"],
    "focus_tickers": ["tickers showing strong edge"],
    "best_entry_time": "<time range like 9:45-10:30 or null>",
    "avoid_after": "<time like 15:00 or null>"
  }},
  "tomorrow_instruction": "One specific thing to do differently tomorrow"
}}

Only change a value if today's data gives a clear reason to. Use null for fields with no new insight.
Be specific with numbers. Do not invent patterns not supported by the data."""

    def _summarize_history(self, history: list) -> str:
        """Summarize all-time trade history by ticker and time of day."""
        if not history:
            return "No historical trades yet."

        from collections import defaultdict
        by_ticker = defaultdict(lambda: {"wins": 0, "total": 0})
        by_hour   = defaultdict(lambda: {"wins": 0, "total": 0})

        for t in history:
            tk = t.get("ticker", "?")
            by_ticker[tk]["total"] += 1
            if t.get("pnl", 0) > 0:
                by_ticker[tk]["wins"] += 1

            entry_time = t.get("entry_time", "")
            if entry_time:
                try:
                    hour = datetime.fromisoformat(entry_time).hour
                    by_hour[hour]["total"] += 1
                    if t.get("pnl", 0) > 0:
                        by_hour[hour]["wins"] += 1
                except Exception:
                    pass

        ticker_lines = []
        for tk, d in sorted(by_ticker.items()):
            wr = d["wins"] / d["total"] * 100 if d["total"] else 0
            ticker_lines.append(f"  {tk}: {d['total']} trades, {wr:.0f}% win rate")

        hour_lines = []
        for hr in sorted(by_hour.keys()):
            d = by_hour[hr]
            wr = d["wins"] / d["total"] * 100 if d["total"] else 0
            hour_lines.append(f"  {hr}:00 — {d['total']} trades, {wr:.0f}% win rate")

        return (
            "By ticker:\n" + "\n".join(ticker_lines) +
            "\n\nBy hour:\n" + "\n".join(hour_lines)
        )

    # ── Gemini API call ───────────────────────────────────────

    def _call_gemini(self, api_key: str, prompt: str) -> str | None:
        """Call Gemini Flash API (free tier, no external libraries needed)."""
        url = GEMINI_URL.format(api_key=api_key)
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
                return (
                    body["candidates"][0]["content"]["parts"][0]["text"]
                )
        except urllib.error.HTTPError as e:
            log.error(f"Gemini API error {e.code}: {e.read().decode()}")
            return None
        except Exception as e:
            log.error(f"Gemini call failed: {e}")
            return None

    # ── Parse & apply ─────────────────────────────────────────

    def _parse_response(self, text: str) -> dict:
        """Parse Gemini JSON response safely."""
        try:
            # Strip any accidental markdown fences
            clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(clean)
        except Exception as e:
            log.error(f"Could not parse Gemini response: {e}\nRaw: {text[:300]}")
            return {}

    def _apply_updates(self, updates: dict, trades_today: list):
        """Write Gemini's suggestions back into persistent memory."""
        if not updates:
            return

        learned = self.memory.get("learned", {})
        adj     = learned.get("adjusted_thresholds", {})

        # Session summary
        summary = updates.get("session_summary", "")
        if summary:
            learned["last_session_summary"] = summary
            log.info(f"Session summary: {summary}")

        # Tomorrow's instruction
        tip = updates.get("tomorrow_instruction", "")
        if tip:
            learned.setdefault("strategy_notes", []).append(
                f"[{datetime.now().strftime('%Y-%m-%d')}] {tip}"
            )
            log.info(f"Tomorrow's instruction: {tip}")

        # Rule updates
        rules = updates.get("rule_updates", {})

        def apply(key, mem_key, label, threshold_key=None):
            val = rules.get(key)
            if val is not None:
                if threshold_key:
                    adj[threshold_key] = val
                else:
                    self.memory.set(mem_key, val)
                log.info(f"Updated {label}: {val}")

        apply("rsi_entry",         None,                    "RSI entry",       "rsi_entry")
        apply("sma_fast",          None,                    "SMA fast",        "sma_fast")
        apply("sma_slow",          None,                    "SMA slow",        "sma_slow")
        apply("stop_loss_pct",     "default_stop_loss_pct", "stop loss %")
        apply("position_size_pct", "default_position_size_pct", "position size %")
        apply("max_trades_per_day","max_trades_per_day",    "max trades/day")

        # Watchlist adjustments
        watchlist = list(self.memory.get("watchlist", []))
        avoid = rules.get("avoid_tickers", [])
        focus = rules.get("focus_tickers", [])
        if avoid:
            watchlist = [t for t in watchlist if t not in avoid]
            log.info(f"Removed from watchlist: {avoid}")
        if focus:
            for t in focus:
                if t not in watchlist:
                    watchlist.append(t)
            log.info(f"Added to watchlist: {focus}")
        if avoid or focus:
            self.memory.set("watchlist", watchlist)

        # Time filters
        if rules.get("best_entry_time"):
            adj["best_entry_time"] = rules["best_entry_time"]
        if rules.get("avoid_after"):
            adj["avoid_after"] = rules["avoid_after"]

        learned["adjusted_thresholds"] = adj
        learned["what_worked"] = updates.get("what_worked", [])
        learned["what_failed"] = updates.get("what_failed", [])
        self.memory.set("learned", learned)

    # ── Rule-based fallback (no API key needed) ───────────────

    def _rule_based_learn(self, trades_today: list):
        """
        Free learning with no API key.
        Analyzes trade history and adjusts thresholds automatically.
        You can also teach it manually by writing in the journal.
        """
        if not trades_today:
            return

        learned = self.memory.get("learned", {})
        adj     = learned.get("adjusted_thresholds", {})
        history = self.memory.get("trade_history", [])

        wins   = [t for t in trades_today if t.get("pnl", 0) > 0]
        losses = [t for t in trades_today if t.get("pnl", 0) <= 0]
        wr     = len(wins) / len(trades_today) * 100 if trades_today else 0

        # RSI threshold adjustment
        current_rsi = adj.get("rsi_entry", 10)
        if wr < 40:
            adj["rsi_entry"] = max(4, current_rsi - 2)
            log.info(f"Win rate {wr:.0f}% — tightened RSI entry to {adj['rsi_entry']}")
        elif wr > 70:
            adj["rsi_entry"] = min(15, current_rsi + 1)
            log.info(f"Win rate {wr:.0f}% — relaxed RSI entry to {adj['rsi_entry']}")

        # Stop loss adjustment
        if losses:
            avg_loss = abs(sum(t.get("pnl_pct", 0) for t in losses) / len(losses))
            current_stop = self.memory.get("default_stop_loss_pct", 2.0)
            if avg_loss > current_stop * 1.5:
                new_stop = round(current_stop * 0.9, 1)
                self.memory.set("default_stop_loss_pct", new_stop)
                log.info(f"Losses exceeding stop — tightened stop to {new_stop}%")

        # Ticker performance — drop consistently losing tickers
        from collections import defaultdict
        ticker_stats = defaultdict(lambda: {"wins": 0, "total": 0})
        for t in history[-30:]:   # last 30 trades
            tk = t.get("ticker", "?")
            ticker_stats[tk]["total"] += 1
            if t.get("pnl", 0) > 0:
                ticker_stats[tk]["wins"] += 1

        watchlist = list(self.memory.get("watchlist", []))
        for tk, stats in ticker_stats.items():
            if stats["total"] >= 5:
                tk_wr = stats["wins"] / stats["total"] * 100
                if tk_wr < 25 and tk in watchlist:
                    watchlist.remove(tk)
                    log.info(f"Dropped {tk} from watchlist ({tk_wr:.0f}% win rate over 5+ trades)")

        self.memory.set("watchlist", watchlist)

        # Write session summary
        pnl_total = sum(t.get("pnl", 0) for t in trades_today)
        summary = (
            f"{datetime.now().strftime('%Y-%m-%d')}: "
            f"{len(trades_today)} trades | {wr:.0f}% win rate | "
            f"P&L ${pnl_total:+.2f}"
        )
        learned["last_session_summary"] = summary
        learned["adjusted_thresholds"] = adj
        self.memory.set("learned", learned)
        log.info(f"Rule-based learning complete: {summary}")

    # ── Manual teaching ───────────────────────────────────────

    def apply_manual_instruction(self, instruction: str):
        """
        Parse a plain English instruction from the journal or dashboard
        and apply it directly to memory.

        Examples:
          "Focus only on SPY and QQQ"
          "Tighten stop loss to 1.5%"
          "Max 2 trades per day"
          "Avoid trading after 2pm"
          "Remove TSLA from watchlist"
        """
        import re
        instruction = instruction.lower().strip()
        log.info(f"Manual instruction: {instruction}")

        adj = self.memory.get("learned", {}).get("adjusted_thresholds", {})

        # Stop loss
        m = re.search(r"stop\s*(?:loss)?\s*(?:to\s*)?(\d+(?:\.\d+)?)\s*%", instruction)
        if m:
            self.memory.set("default_stop_loss_pct", float(m.group(1)))
            log.info(f"Stop loss set to {m.group(1)}%")

        # Max trades
        m = re.search(r"(?:max|maximum)\s*(\d+)\s*trades?", instruction)
        if m:
            self.memory.set("max_trades_per_day", int(m.group(1)))
            log.info(f"Max trades/day set to {m.group(1)}")

        # Avoid after time
        m = re.search(r"avoid\s*(?:trading\s*)?after\s*(\d+)(?::(\d+))?\s*(am|pm)?", instruction)
        if m:
            hr = int(m.group(1))
            if m.group(3) == "pm" and hr < 12:
                hr += 12
            adj["avoid_after"] = f"{hr:02d}:00"
            log.info(f"Set avoid_after to {hr:02d}:00")

        # Focus tickers
        m = re.search(r"focus\s*(?:only\s*)?on\s+([\w\s,]+)", instruction)
        if m:
            tickers = [t.strip().upper() for t in re.split(r"[,\s]+", m.group(1)) if t.strip()]
            if tickers:
                self.memory.set("watchlist", tickers)
                log.info(f"Watchlist set to: {tickers}")

        # Remove ticker
        m = re.search(r"remove\s+([A-Z]+)\s*from\s*watchlist", instruction.upper())
        if m:
            wl = list(self.memory.get("watchlist", []))
            ticker = m.group(1)
            if ticker in wl:
                wl.remove(ticker)
                self.memory.set("watchlist", wl)
                log.info(f"Removed {ticker} from watchlist")

        # Save threshold updates
        learned = self.memory.get("learned", {})
        learned["adjusted_thresholds"] = adj
        self.memory.set("learned", learned)
