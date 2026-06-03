"""
agent.py — Autonomous trading agent.

Primary strategy: Connors RSI(2) Mean Reversion (best performer)
- Scans watchlist every 5 minutes during market hours
- Caches price data to avoid rate limiting
- Runs 9:30 AM - 4:00 PM ET with 3:45 PM cutoff
- End-of-day learning via Gemini
"""
import asyncio
import json
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

from core.api import TradeZeroAPI
from core.memory import Memory
from core.learning import LearningEngine
from core.logger import log

# Timezone
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
CUTOFF = dtime(15, 45)  # No new entries after this


class Agent:
    def __init__(self, api: TradeZeroAPI, memory: Memory):
        self.api = api
        self.memory = memory
        self.learner = LearningEngine(memory)
        self.killed = False
        self.price_cache = {}  # {ticker: {"time": datetime, "candles": [...]}}
        self.last_signal = {}  # Track last signal per ticker to avoid repeats
        self.instruction = None

    async def run(self):
        """Main bot loop — runs continuously."""
        while True:
            try:
                if self.killed:
                    log.error("Kill switch active — waiting for release")
                    await asyncio.sleep(60)
                    continue

                now = datetime.now(ET)
                now_time = now.time()

                # Market closed?
                if now_time < MARKET_OPEN or now_time >= MARKET_CLOSE:
                    if now_time >= MARKET_CLOSE:
                        await self._end_of_day()
                    log.info(f"Market closed. Next open: tomorrow 9:30 AM ET. Sleeping 60min...")
                    await asyncio.sleep(3600)
                    continue

                # Market open — scan for signals
                await self._scan_signals(now, now_time)
                await asyncio.sleep(300)  # Scan every 5 minutes, not 60 seconds

            except Exception as e:
                log.error(f"Agent loop error: {e}")
                await asyncio.sleep(60)

    async def _scan_signals(self, now: datetime, now_time: dtime):
        """Scan watchlist for Connors RSI(2) mean reversion signals."""
        watchlist = self.memory.get("watchlist", ["SPY", "AAPL", "TSLA", "QQQ", "NVDA"])
        log.info(f"Scanning {len(watchlist)} tickers for Connors RSI(2) signals...")

        signals = []
        for ticker in watchlist:
            try:
                # Get cached or fresh candles
                candles = await self._get_candles_cached(ticker)
                if not candles or len(candles) < 205:
                    log.warning(f"Not enough candles for {ticker}")
                    continue

                # Connors RSI(2) analysis
                signal = self._connors_rsi_signal(ticker, candles)
                if signal and signal["action"] != "HOLD":
                    signals.append(signal)
                    log.info(f"Signal: {signal['action']} {ticker} (confidence: {signal['confidence']}%)")
            except Exception as e:
                log.error(f"Signal scan error for {ticker}: {e}")
                continue

        # Execute top signals
        if signals:
            signals.sort(key=lambda s: s["confidence"], reverse=True)
            max_trades = self.memory.get("max_trades_per_day", 3)
            active = len(self.memory.get("trade_history", []))
            can_trade = max(0, max_trades - active)

            for sig in signals[:can_trade]:
                if now_time >= CUTOFF:
                    log.warning("Past 3:45 PM — no new entries")
                    break
                await self._execute_signal(sig)

    def _connors_rsi_signal(self, ticker: str, candles: list) -> dict:
        """
        Connors RSI(2) mean reversion.
        Entry: RSI(2) < 10 AND 3+ consecutive down days AND price > 200MA
        Exit: RSI(2) > 70 (handled in exit logic)
        """
        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]

        # Indicators
        rsi2 = self._calc_rsi(closes, 2)
        ma200 = self._calc_sma(closes, 200)
        vol_ma = self._calc_sma(volumes, 20)

        if None in (rsi2[-1], ma200[-1], vol_ma[-1]):
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

        price = closes[-1]
        r = rsi2[-1]
        ma = ma200[-1]
        vol = volumes[-1]
        vm = vol_ma[-1]

        # Connors conditions
        down_days = self._consecutive_down_days(closes)
        above_trend = price > ma
        oversold = r < 10
        volume_ok = vol > vm
        streak_ok = down_days >= 3

        if above_trend and oversold and volume_ok and streak_ok:
            return {
                "action": "BUY",
                "ticker": ticker,
                "confidence": min(99, int(100 - r)),  # Lower RSI = higher confidence
                "reason": f"RSI(2)={r:.1f} + {down_days}d down + vol",
                "strategy": "Connors RSI(2)",
                "stop_hint": f"${price * 0.98:.2f}",
            }
        elif r > 70:
            return {
                "action": "SELL",
                "ticker": ticker,
                "confidence": min(99, int(r - 50)),
                "reason": f"RSI(2) overbought {r:.1f}",
                "strategy": "Connors RSI(2)",
            }
        else:
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

    async def _execute_signal(self, signal: dict):
        """Execute a buy/sell signal."""
        if self.killed:
            log.error("ORDER BLOCKED — kill switch active")
            return

        ticker = signal["ticker"]
        qty = 10  # Simple fixed quantity for now
        stop_loss_pct = self.memory.get("default_stop_loss_pct", 2.0)
        entry_price = None

        try:
            if signal["action"] == "BUY":
                log.info(f"Executing BUY {qty} {ticker} — {signal['reason']}")
                result = await self.api.place_order(
                    ticker, "BUY", qty, order_type="MARKET"
                )
                if "error" not in result:
                    entry_price = result.get("price", 0)
                    # Log trade
                    self.memory.log_trade({
                        "ticker": ticker,
                        "action": "BUY",
                        "qty": qty,
                        "entry_price": entry_price,
                        "exit_price": None,
                        "pnl": 0,
                        "pnl_pct": 0,
                        "strategy": signal["strategy"],
                        "env": self.memory.get("tz_env", "paper"),
                        "exit_reason": None,
                    })
                else:
                    log.error(f"Order failed: {result}")
        except Exception as e:
            log.error(f"Execute signal error: {e}")

    async def _end_of_day(self):
        """After market close — run Gemini learning."""
        history = self.memory.get("trade_history", [])
        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        today = [t for t in history if t.get("logged_at", "").startswith(today_str)]

        await self.learner.learn_from_session(today)

        wins = [t for t in today if t.get("pnl", 0) > 0]
        pnl = sum(t.get("pnl", 0) for t in today)
        log.info(f"End of day: {len(today)} trades, {len(wins)} wins, P&L ${pnl:+.2f}")

    async def _get_candles_cached(self, ticker: str) -> list:
        """Fetch candles with caching to avoid rate limits."""
        now = datetime.now(ET)

        # Check cache (5 min TTL)
        if ticker in self.price_cache:
            cached = self.price_cache[ticker]
            if now - cached["time"] < timedelta(minutes=5):
                return cached["candles"]

        # Fetch fresh
        try:
            candles = await self.api.get_candles(ticker, interval="5min", limit=200)
            self.price_cache[ticker] = {"time": now, "candles": candles}
            return candles
        except Exception as e:
            log.error(f"Candle fetch error for {ticker}: {e}")
            return self.price_cache.get(ticker, {}).get("candles", [])

    def _calc_rsi(self, closes: list, period: int = 2) -> list:
        """Calculate RSI."""
        out = [None] * len(closes)
        if len(closes) < period + 1:
            return out
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(closes)):
            if i > period:
                avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
                avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
            rs = avg_g / avg_l if avg_l != 0 else 100
            out[i] = round(100 - (100 / (1 + rs)), 1)
        return out

    def _calc_sma(self, closes: list, period: int) -> list:
        """Calculate SMA."""
        out = [None] * len(closes)
        for i in range(period - 1, len(closes)):
            out[i] = sum(closes[i - period + 1 : i + 1]) / period
        return out

    def _consecutive_down_days(self, closes: list) -> int:
        """Count consecutive down days from end."""
        count = 0
        i = len(closes) - 1
        while i > 0 and closes[i] < closes[i - 1]:
            count += 1
            i -= 1
        return count

    async def release_kill(self):
        self.killed = False
        log.info("Kill switch released")
