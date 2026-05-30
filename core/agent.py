"""
TZ Trading Agent
The brain of the bot. Reads memory, generates signals, executes trades,
and learns from every outcome to improve the next session.
"""
import asyncio
from datetime import datetime, time as dtime
from core.logger import log
from core.memory import Memory
from core.api import TradeZeroAPI
from core.strategies import SMAStrategy, ConnorsRSIStrategy, ORBStrategy
from core.learning import LearningEngine


MARKET_OPEN  = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
CUTOFF       = dtime(15, 45)   # No new entries after this


class TradingAgent:
    def __init__(self, api: TradeZeroAPI, memory: Memory):
        self.api = api
        self.memory = memory
        self.killed = False
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.open_positions = {}   # ticker -> {entry_price, qty, stop, target}
        self.instruction = None    # set by run_from_prompt()
        self.learner = LearningEngine(memory=memory)

    # ── Main Loop ────────────────────────────────────────────

    async def run_loop(self):
        """Main loop — runs continuously during market hours."""
        log.info("Agent loop started")
        log.info(self.memory.get_context_summary())

        while not self.killed:
            now = datetime.now().time()

            if MARKET_OPEN <= now <= MARKET_CLOSE:
                await self._session_tick()
            else:
                if now < MARKET_OPEN:
                    wait = (
                        datetime.combine(datetime.today(), MARKET_OPEN)
                        - datetime.now()
                    ).seconds
                    log.info(f"Market opens in {wait//60}m {wait%60}s — waiting...")
                else:
                    log.info("Market closed. Running end-of-day learning...")
                    await self._end_of_day()
                    log.info("Waiting for tomorrow. Sleeping 60 min...")
                    await asyncio.sleep(3600)
                    continue

            await asyncio.sleep(60)   # tick every 60 seconds

    async def _session_tick(self):
        """Called every minute during market hours."""
        if self.killed:
            return

        # Check daily loss limit
        max_loss = self.memory.get("max_daily_loss_pct", 3.0)
        account = await self.api.get_account()
        balance = float(account.get("equity", account.get("balance", 1000)))
        daily_loss_pct = (self.daily_pnl / balance) * 100 if balance else 0

        if daily_loss_pct < -max_loss:
            log.error(f"Daily loss limit hit ({daily_loss_pct:.1f}%) — stopping for today")
            await self.emergency_stop()
            return

        # Check trade count
        max_trades = self.memory.get("max_trades_per_day", 3)
        if self.trades_today >= max_trades:
            return   # silent — just monitor positions

        # No new entries after cutoff
        if datetime.now().time() > CUTOFF:
            await self._monitor_positions()
            return

        # Generate signals
        strategy_name = self.memory.get("active_strategy", "SMA Crossover")
        watchlist = self.memory.get("watchlist", ["SPY", "AAPL", "TSLA"])
        signals = await self._generate_signals(strategy_name, watchlist)

        for signal in signals:
            if self.killed:
                break
            if self.trades_today >= max_trades:
                break
            if signal["action"] in ("BUY", "SHORT"):
                await self._execute_signal(signal, balance)

        await self._monitor_positions()

    # ── Signal Generation ─────────────────────────────────────

    async def _generate_signals(self, strategy_name: str, watchlist: list) -> list:
        """Run the selected strategy across the watchlist."""
        signals = []
        strategy_map = {
            "SMA Crossover":             SMAStrategy,
            "Connors RSI Mean Reversion": ConnorsRSIStrategy,
            "Opening Range Breakout":    ORBStrategy,
        }
        StratClass = strategy_map.get(strategy_name, SMAStrategy)

        learned = self.memory.get("learned", {})
        adjusted = learned.get("adjusted_thresholds", {})

        for ticker in watchlist:
            if ticker in self.open_positions:
                continue
            candles = await self.api.get_candles(ticker, interval="5min", limit=50)
            if not candles:
                continue
            strat = StratClass(ticker=ticker, candles=candles, overrides=adjusted)
            sig = strat.evaluate()
            if sig:
                signals.append(sig)
                log.info(f"Signal: {sig['action']} {ticker} | {sig['reason']} | confidence {sig['confidence']:.0%}")

        # Sort by confidence — take strongest first
        signals.sort(key=lambda s: s["confidence"], reverse=True)
        return signals

    # ── Execution ─────────────────────────────────────────────

    async def _execute_signal(self, signal: dict, account_balance: float):
        """Execute a signal via TradeZero API."""
        ticker = signal["ticker"]
        action = signal["action"]

        pos_pct = self.memory.get("default_position_size_pct", 10.0) / 100
        stop_pct = self.memory.get("default_stop_loss_pct", 2.0) / 100

        quote = await self.api.get_quote(ticker)
        price = float(quote.get("ask") or quote.get("last", 100))
        if price == 0:
            return

        qty = max(1, int((account_balance * pos_pct) / price))
        stop_price = round(price * (1 - stop_pct), 2)
        target_price = round(price * (1 + stop_pct * 2), 2)   # 2:1 R:R

        result = await self.api.place_order(
            ticker=ticker,
            action=action,
            qty=qty,
            order_type="MARKET"
        )

        if "error" not in result:
            self.trades_today += 1
            self.open_positions[ticker] = {
                "entry_price": price,
                "qty": qty,
                "stop": stop_price,
                "target": target_price,
                "action": action,
                "strategy": self.memory.get("active_strategy"),
                "entry_time": datetime.now().isoformat(),
                "order_id": result.get("orderId") or result.get("id")
            }
            log.info(
                f"EXECUTED: {action} {qty} {ticker} @ ${price:.2f} | "
                f"stop ${stop_price:.2f} | target ${target_price:.2f}"
            )

    # ── Position Monitor ──────────────────────────────────────

    async def _monitor_positions(self):
        """Check stops and targets on open positions."""
        for ticker, pos in list(self.open_positions.items()):
            if self.killed:
                break
            quote = await self.api.get_quote(ticker)
            price = float(quote.get("last") or quote.get("bid", 0))
            if not price:
                continue

            hit_stop   = price <= pos["stop"]
            hit_target = price >= pos["target"]

            if hit_stop or hit_target:
                reason = "STOP HIT" if hit_stop else "TARGET HIT"
                exit_action = "SELL" if pos["action"] == "BUY" else "COVER"
                await self.api.place_order(ticker, exit_action, pos["qty"])

                pnl = (price - pos["entry_price"]) * pos["qty"]
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
                self.daily_pnl += pnl

                trade_record = {
                    "ticker": ticker,
                    "action": pos["action"],
                    "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "exit_price": price,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "strategy": pos.get("strategy"),
                    "env": self.api.env,
                    "entry_time": pos.get("entry_time"),
                    "exit_time": datetime.now().isoformat(),
                    "exit_reason": reason
                }
                note = self.memory.log_trade(trade_record)
                log.info(f"{reason}: {ticker} @ ${price:.2f} | P&L ${pnl:+.2f} ({pnl_pct:+.1f}%)")
                del self.open_positions[ticker]

    # ── End-of-Day Learning ───────────────────────────────────

    async def _end_of_day(self):
        """
        After market close: analyze today's trades, update strategy
        thresholds, and write a summary to memory.
        """
        learned = self.memory.get("learned", {})
        history = self.memory.get("trade_history", [])
        today_str = datetime.now().strftime("%Y-%m-%d")
        today = [t for t in history if t.get("logged_at", "").startswith(today_str)]

        if not today:
            log.info("No trades today — nothing to learn from")
            return

        wins = [t for t in today if t.get("pnl", 0) > 0]
        losses = [t for t in today if t.get("pnl", 0) <= 0]
        win_rate = len(wins) / len(today) * 100

        summary = (
            f"{today_str}: {len(today)} trades | "
            f"Win rate {win_rate:.0f}% | "
            f"Daily P&L ${self.daily_pnl:+.2f}"
        )
        learned["last_session_summary"] = summary
        self.memory.set("learned", learned)

        # Auto-adjust: if win rate < 40% tighten RSI threshold
        if win_rate < 40 and learned.get("adjusted_thresholds") is not None:
            adj = learned.get("adjusted_thresholds", {})
            current_rsi = adj.get("rsi_entry", 10)
            adj["rsi_entry"] = max(5, current_rsi - 2)
            learned["adjusted_thresholds"] = adj
            log.info(f"Low win rate — tightened RSI entry to {adj['rsi_entry']}")
        elif win_rate > 70:
            adj = learned.get("adjusted_thresholds", {})
            current_rsi = adj.get("rsi_entry", 10)
            adj["rsi_entry"] = min(15, current_rsi + 1)
            learned["adjusted_thresholds"] = adj
            log.info(f"High win rate — relaxed RSI entry to {adj['rsi_entry']}")

        self.memory.set("learned", learned)
        log.info(f"End-of-day learning complete: {summary}")

        # Reset daily counters
        self.trades_today = 0
        self.daily_pnl = 0.0

    # ── Kill Switch ────────────────────────────────────────────

    async def emergency_stop(self):
        """Immediately halt all activity."""
        self.killed = True
        log.error("=== EMERGENCY STOP ===")
        await self.api.kill()
        self.open_positions.clear()
        log.error("All positions cleared from tracking. Bot halted.")

    # ── Prompt Interface (for dashboard) ──────────────────────

    async def run_from_prompt(self, instruction: str):
        """
        Execute a natural language instruction from the dashboard.
        e.g. "Trade the next 3 days using ORB, max 2 trades/day"
        """
        instruction = instruction.lower()
        log.info(f"Instruction received: {instruction}")

        if "connors" in instruction:
            self.memory.set("active_strategy", "Connors RSI Mean Reversion")
        elif "orb" in instruction or "opening range" in instruction:
            self.memory.set("active_strategy", "Opening Range Breakout")
        elif "dual" in instruction or "momentum" in instruction:
            self.memory.set("active_strategy", "Dual Momentum")
        else:
            self.memory.set("active_strategy", "SMA Crossover")

        import re
        m = re.search(r"(\d+)\s*trades?\s*(?:per|a)?\s*day", instruction)
        if m:
            self.memory.set("max_trades_per_day", int(m.group(1)))

        m2 = re.search(r"stop\s*(?:loss)?\s*(\d+(?:\.\d+)?)\s*%", instruction)
        if m2:
            self.memory.set("default_stop_loss_pct", float(m2.group(1)))

        log.info(
            f"Strategy set to: {self.memory.get('active_strategy')} | "
            f"Max trades: {self.memory.get('max_trades_per_day')}/day"
        )
