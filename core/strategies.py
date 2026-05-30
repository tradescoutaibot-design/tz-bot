"""
Strategy implementations.
Each strategy takes candle data and returns a signal dict or None.
The agent reads learned threshold overrides from memory before evaluating.
"""
from typing import Optional
import statistics


def _closes(candles: list) -> list:
    """Extract closing prices from candle list."""
    return [float(c.get("close") or c.get("c") or 0) for c in candles]

def _volumes(candles: list) -> list:
    return [float(c.get("volume") or c.get("v") or 0) for c in candles]

def _highs(candles: list) -> list:
    return [float(c.get("high") or c.get("h") or 0) for c in candles]

def _lows(candles: list) -> list:
    return [float(c.get("low") or c.get("l") or 0) for c in candles]

def sma(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def rsi(prices: list, period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── SMA Crossover ─────────────────────────────────────────────────────────────

class SMAStrategy:
    """
    Enter long when fast SMA crosses above slow SMA with volume confirmation.
    Default: 9-period fast, 21-period slow.
    Learned overrides can adjust these.
    """
    def __init__(self, ticker: str, candles: list, overrides: dict = None):
        self.ticker = ticker
        self.candles = candles
        self.overrides = overrides or {}
        self.fast = int(self.overrides.get("sma_fast", 9))
        self.slow = int(self.overrides.get("sma_slow", 21))
        self.rsi_min = float(self.overrides.get("rsi_min", 40))
        self.rsi_max = float(self.overrides.get("rsi_max", 70))

    def evaluate(self) -> Optional[dict]:
        closes = _closes(self.candles)
        volumes = _volumes(self.candles)
        if len(closes) < self.slow + 2:
            return None

        fast_now  = sma(closes, self.fast)
        fast_prev = sma(closes[:-1], self.fast)
        slow_now  = sma(closes, self.slow)
        slow_prev = sma(closes[:-1], self.slow)
        rsi_val   = rsi(closes)
        avg_vol   = sma(volumes, 20)
        curr_vol  = volumes[-1]

        if None in (fast_now, fast_prev, slow_now, slow_prev, rsi_val, avg_vol):
            return None

        # Bullish crossover
        crossed_up   = fast_prev <= slow_prev and fast_now > slow_now
        rsi_ok       = self.rsi_min <= rsi_val <= self.rsi_max
        volume_ok    = curr_vol > avg_vol

        if crossed_up and rsi_ok and volume_ok:
            confidence = 0.5
            if rsi_val < 60:      confidence += 0.15
            if curr_vol > avg_vol * 1.5: confidence += 0.15
            if fast_now > slow_now * 1.002: confidence += 0.10
            return {
                "ticker": self.ticker,
                "action": "BUY",
                "confidence": min(confidence, 0.95),
                "reason": f"SMA{self.fast} crossed SMA{self.slow} | RSI {rsi_val:.0f} | vol {curr_vol/avg_vol:.1f}x avg",
                "strategy": "SMA Crossover"
            }

        # Bearish crossover → exit signal
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        if crossed_down:
            return {
                "ticker": self.ticker,
                "action": "SELL",
                "confidence": 0.80,
                "reason": f"SMA{self.fast} crossed below SMA{self.slow}",
                "strategy": "SMA Crossover"
            }

        return None


# ── Connors RSI Mean Reversion ────────────────────────────────────────────────

class ConnorsRSIStrategy:
    """
    Based on the Connors RSI white paper.
    Buy when: RSI(2) < threshold AND stock down 3+ consecutive days AND
              price above 200-day SMA (trend filter).
    Historical win rate: ~65-70% on liquid stocks.
    Learned threshold: rsi_entry (default 10, agent adjusts based on outcomes).
    """
    def __init__(self, ticker: str, candles: list, overrides: dict = None):
        self.ticker = ticker
        self.candles = candles
        self.overrides = overrides or {}
        self.rsi_threshold  = float(self.overrides.get("rsi_entry", 10))
        self.streak_days    = int(self.overrides.get("streak_days", 3))
        self.trend_sma      = int(self.overrides.get("trend_sma", 50))   # use 50 instead of 200 for small timeframes

    def evaluate(self) -> Optional[dict]:
        closes = _closes(self.candles)
        if len(closes) < max(self.trend_sma + 1, 20):
            return None

        rsi2      = rsi(closes, period=2)
        trend_avg = sma(closes, self.trend_sma)
        price     = closes[-1]

        if None in (rsi2, trend_avg):
            return None

        # Count consecutive down days
        streak = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                streak += 1
            else:
                break

        above_trend = price > trend_avg
        oversold    = rsi2 < self.rsi_threshold
        long_streak = streak >= self.streak_days

        if above_trend and oversold and long_streak:
            confidence = 0.60
            if rsi2 < self.rsi_threshold * 0.5: confidence += 0.15
            if streak >= self.streak_days + 1:  confidence += 0.10
            return {
                "ticker": self.ticker,
                "action": "BUY",
                "confidence": min(confidence, 0.90),
                "reason": (
                    f"Connors RSI({rsi2:.1f}) < {self.rsi_threshold} | "
                    f"{streak}-day down streak | above {self.trend_sma}MA"
                ),
                "strategy": "Connors RSI Mean Reversion"
            }

        # Overbought exit
        if rsi2 > 70:
            return {
                "ticker": self.ticker,
                "action": "SELL",
                "confidence": 0.75,
                "reason": f"Connors RSI overbought ({rsi2:.1f} > 70) — take profit",
                "strategy": "Connors RSI Mean Reversion"
            }

        return None


# ── Opening Range Breakout ────────────────────────────────────────────────────

class ORBStrategy:
    """
    Most-studied intraday strategy.
    Define the range as the high/low of the first N candles (default 3 x 5min = 15min).
    Enter long on breakout above range high with volume confirmation.
    Place stop at range low.
    """
    def __init__(self, ticker: str, candles: list, overrides: dict = None):
        self.ticker = ticker
        self.candles = candles
        self.overrides = overrides or {}
        self.range_candles = int(self.overrides.get("orb_candles", 3))  # 3 x 5min = 15min range

    def evaluate(self) -> Optional[dict]:
        if len(self.candles) < self.range_candles + 2:
            return None

        highs   = _highs(self.candles)
        lows    = _lows(self.candles)
        closes  = _closes(self.candles)
        volumes = _volumes(self.candles)

        range_high = max(highs[:self.range_candles])
        range_low  = min(lows[:self.range_candles])
        curr_close = closes[-1]
        curr_vol   = volumes[-1]
        avg_vol    = sma(volumes, 10) or 1

        range_size_pct = (range_high - range_low) / range_low * 100

        # Skip if range is too wide (volatile) or too narrow (no move)
        if range_size_pct > 3.0 or range_size_pct < 0.1:
            return None

        breakout_up   = curr_close > range_high * 1.001
        breakout_down = curr_close < range_low * 0.999
        vol_confirm   = curr_vol > avg_vol * 1.2

        if breakout_up and vol_confirm:
            confidence = 0.60
            if curr_vol > avg_vol * 2: confidence += 0.15
            if range_size_pct < 1.5:   confidence += 0.10
            return {
                "ticker": self.ticker,
                "action": "BUY",
                "confidence": min(confidence, 0.90),
                "reason": (
                    f"ORB breakout above ${range_high:.2f} | "
                    f"range {range_size_pct:.1f}% | vol {curr_vol/avg_vol:.1f}x"
                ),
                "strategy": "Opening Range Breakout",
                "stop_hint": range_low
            }

        if breakout_down and vol_confirm:
            return {
                "ticker": self.ticker,
                "action": "SHORT",
                "confidence": 0.65,
                "reason": (
                    f"ORB breakdown below ${range_low:.2f} | "
                    f"range {range_size_pct:.1f}% | vol {curr_vol/avg_vol:.1f}x"
                ),
                "strategy": "Opening Range Breakout",
                "stop_hint": range_high
            }

        return None
