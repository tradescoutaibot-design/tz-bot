"""
agent.py — Autonomous trading agent
Strategy: Connors RSI(2) + SMA Crossover combo
Scans 5-min candles every 5 minutes during market hours
Learns from trades via Gemini
"""
import asyncio
import json
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from core.api import TradeZeroAPI
from core.memory import Memory
from core.learning import LearningEngine
from core.logger import log

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
CUTOFF = dtime(15, 45)


class Agent:
    def __init__(self, api: TradeZeroAPI, memory: Memory):
        self.api = api
        self.memory = memory
        self.learner = LearningEngine(memory)
        self.killed = False
        self.price_cache = {}

    async def run(self):
        """Main bot loop"""
        while True:
            try:
                if self.killed:
                    log.error("Kill switch active")
                    await asyncio.sleep(60)
                    continue

                now = datetime.now(ET)
                now_time = now.time()

                # Check market hours
                if now_time < MARKET_OPEN or now_time >= MARKET_CLOSE:
                    if now_time >= MARKET_CLOSE:
                        await self._end_of_day()
                    log.info("Market closed. Sleeping...")
                    await asyncio.sleep(3600)
                    continue

                # Scan for signals
                await self._scan_and_trade(now, now_time)
                await asyncio.sleep(300)  # Scan every 5 min

            except Exception as e:
                log.error(f"Agent loop error: {e}")
                await asyncio.sleep(60)

    async def _scan_and_trade(self, now: datetime, now_time: dtime):
        """Scan tickers and execute signals"""
        watchlist = self.memory.get("watchlist", ["SPY", "AAPL", "TSLA", "QQQ", "NVDA"])
        log.info(f"Scanning {len(watchlist)} tickers for signals...")

        signals = []
        for ticker in watchlist:
            try:
                candles = await self._get_candles_cached(ticker)
                if not candles or len(candles) < 50:
                    log.warning(f"Not enough candles for {ticker}")
                    continue

                # Both strategies
                sig_connors = self._connors_signal(ticker, candles)
                sig_sma = self._sma_signal(ticker, candles)

                # Use highest confidence
                if sig_connors["action"] != "HOLD":
                    signals.append(sig_connors)
                if sig_sma["action"] != "HOLD" and sig_sma["confidence"] > sig_connors.get("confidence", 0):
                    signals.append(sig_sma)

            except Exception as e:
                log.error(f"Scan error {ticker}: {e}")
                continue

        # Execute top signals
        if signals:
            signals.sort(key=lambda s: s["confidence"], reverse=True)
            max_trades = self.memory.get("max_trades_per_day", 3)
            active = len([t for t in self.memory.get("trade_history", []) if t.get("exit_price") is None])
            
            for sig in signals[:max(0, max_trades - active)]:
                if now_time >= CUTOFF:
                    log.warning("Past 3:45 PM cutoff")
                    break
                await self._execute(sig)

    def _connors_signal(self, ticker: str, candles: list) -> dict:
        """Connors RSI(2)"""
        closes = [c["close"] for c in candles]
        rsi2 = self._calc_rsi(closes, 2)
        ma200 = self._calc_sma(closes, 200)

        price = closes[-1]
        r = rsi2[-1]
        ma = ma200[-1]

        if r is None or ma is None:
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

        down_days = sum(1 for j in range(len(closes)-2, max(0, len(closes)-5), -1) if closes[j] < closes[j-1])

        if price > ma and r < 10 and down_days >= 3:
            return {
                "action": "BUY",
                "ticker": ticker,
                "confidence": min(99, int(100 - r)),
                "strategy": "Connors RSI(2)",
                "reason": f"RSI={r:.1f} + {down_days}d down",
            }
        else:
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

    def _sma_signal(self, ticker: str, candles: list) -> dict:
        """SMA Crossover 9/21"""
        closes = [c["close"] for c in candles]
        fast = self._calc_sma(closes, 9)
        slow = self._calc_sma(closes, 21)
        rsi14 = self._calc_rsi(closes, 14)

        if fast[-1] is None or slow[-1] is None or rsi14[-1] is None:
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

        # Crossover
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1] and 40 <= rsi14[-1] <= 70:
            return {
                "action": "BUY",
                "ticker": ticker,
                "confidence": min(99, int(rsi14[-1] - 30)),
                "strategy": "SMA Crossover",
                "reason": f"9MA cross 21MA + RSI={rsi14[-1]:.1f}",
            }
        else:
            return {"action": "HOLD", "ticker": ticker, "confidence": 0}

    async def _execute(self, signal: dict):
        """Execute signal"""
        if self.killed:
            return

        ticker = signal["ticker"]
        qty = 10
        
        try:
            log.info(f"BUY {qty} {ticker} ({signal['strategy']}) - {signal['reason']}")
            result = await self.api.place_order(ticker, "BUY", qty, "MARKET")
            
            if "error" not in result:
                self.memory.log_trade({
                    "ticker": ticker,
                    "action": "BUY",
                    "qty": qty,
                    "entry_price": result.get("price", 0),
                    "exit_price": None,
                    "strategy": signal["strategy"],
                    "env": self.memory.get("tz_env", "paper"),
                })
        except Exception as e:
            log.error(f"Execute error: {e}")

    async def _end_of_day(self):
        """Run learning at market close"""
        history = self.memory.get("trade_history", [])
        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        today = [t for t in history if t.get("logged_at", "").startswith(today_str)]

        await self.learner.learn_from_session(today)
        log.info(f"End of day: {len(today)} trades")

    async def _get_candles_cached(self, ticker: str) -> list:
        """Get 5-min candles with caching"""
        now = datetime.now(ET)
        
        if ticker in self.price_cache:
            cached = self.price_cache[ticker]
            if (now - cached["time"]).total_seconds() < 300:  # 5 min cache
                return cached["candles"]

        try:
            candles = await self.api.get_candles(ticker, interval="5min", limit=200)
            self.price_cache[ticker] = {"time": now, "candles": candles}
            return candles
        except Exception as e:
            log.error(f"Candle fetch error {ticker}: {e}")
            return []

    def _calc_rsi(self, closes: list, period: int) -> list:
        out = [None] * len(closes)
        if len(closes) < period + 1:
            return out
        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(closes)):
            if i > period:
                avg_g = (avg_g * (period - 1) + gains[i-1]) / period
                avg_l = (avg_l * (period - 1) + losses[i-1]) / period
            rs = avg_g / avg_l if avg_l != 0 else 100
            out[i] = round(100 - (100 / (1 + rs)), 1)
        return out

    def _calc_sma(self, closes: list, period: int) -> list:
        out = [None] * len(closes)
        for i in range(period - 1, len(closes)):
            out[i] = sum(closes[i - period + 1:i + 1]) / period
        return out
