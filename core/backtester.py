"""
backtester.py — Deep backtesting engine using real Yahoo Finance data.

Pulls months/years of real OHLCV data and runs full strategy analysis:
- Connors RSI(2) Mean Reversion
- Opening Range Breakout
- SMA Crossover
- Dual Momentum

Returns full stats: win rate, Sharpe, Sortino, max drawdown,
profit factor, equity curve, trade-by-trade log, monthly breakdown,
walk-forward validation, and Monte Carlo simulation.

No API key needed — uses yfinance (free).
"""

import json
import urllib.request
import urllib.parse
import math
import random
from datetime import datetime, timedelta
from core.logger import log


# ── Yahoo Finance data fetcher ────────────────────────────────

def fetch_ohlcv(ticker: str, start: str, end: str, interval: str = "1d") -> list:
    """
    Fetch real OHLCV data from Yahoo Finance.
    start/end: "YYYY-MM-DD"
    interval: 1d, 1wk, 1mo
    Returns list of {date, open, high, low, close, volume}
    """
    try:
        start_ts = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        end_ts   = int(datetime.strptime(end,   "%Y-%m-%d").timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={start_ts}&period2={end_ts}&interval={interval}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        result    = data["chart"]["result"][0]
        timestamps = result["timestamps"]
        q          = result["indicators"]["quote"][0]
        adjclose   = result["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])

        candles = []
        for i, ts in enumerate(timestamps):
            if (q["close"][i] is None or q["open"][i] is None or
                    q["high"][i] is None or q["low"][i] is None):
                continue
            candles.append({
                "date":   datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "open":   round(q["open"][i],  4),
                "high":   round(q["high"][i],  4),
                "low":    round(q["low"][i],   4),
                "close":  round(adjclose[i] if adjclose[i] else q["close"][i], 4),
                "volume": int(q["volume"][i]) if q["volume"][i] else 0,
            })
        log.info(f"Fetched {len(candles)} candles for {ticker} ({start} → {end})")
        return candles

    except Exception as e:
        log.error(f"fetch_ohlcv({ticker}) failed: {e}")
        return []


# ── Indicators ────────────────────────────────────────────────

def calc_sma(closes: list, period: int) -> list:
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        out[i] = sum(closes[i - period + 1:i + 1]) / period
    return out

def calc_rsi(closes: list, period: int = 14) -> list:
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
        out[i] = round(100 - (100 / (1 + rs)), 2)
    return out

def calc_atr(candles: list, period: int = 14) -> list:
    out = [None] * len(candles)
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    for i in range(period - 1, len(trs)):
        out[i + 1] = sum(trs[i - period + 1:i + 1]) / period
    return out

def consecutive_down_days(closes: list, idx: int) -> int:
    count = 0
    i = idx
    while i > 0 and closes[i] < closes[i - 1]:
        count += 1
        i -= 1
    return count


# ── Strategy signal generators ────────────────────────────────

class ConnorsRSIBacktest:
    """
    Connors RSI(2) Mean Reversion
    Entry: RSI(2) < threshold AND 3+ down days AND price > 200MA
    Exit:  RSI(2) > 70 OR stop loss hit
    """
    def __init__(self, rsi_threshold=10, trend_period=200, exit_rsi=70,
                 stop_loss_pct=None, position_pct=0.10):
        self.rsi_threshold = rsi_threshold
        self.trend_period  = trend_period
        self.exit_rsi      = exit_rsi
        self.stop_loss_pct = stop_loss_pct  # None = Connors original (no SL)
        self.position_pct  = position_pct

    def run(self, candles: list, capital: float) -> dict:
        if len(candles) < self.trend_period + 5:
            return {"error": "Not enough data"}

        closes = [c["close"] for c in candles]
        rsi2   = calc_rsi(closes, 2)
        sma200 = calc_sma(closes, self.trend_period)

        trades, equity_curve = [], [capital]
        cash, position = capital, None
        equity = capital

        for i in range(self.trend_period, len(candles)):
            price = closes[i]
            r     = rsi2[i]
            ma    = sma200[i]
            if r is None or ma is None:
                continue

            # Exit
            if position:
                pnl_pct = (price - position["entry"]) / position["entry"]
                hit_stop   = self.stop_loss_pct and pnl_pct <= -(self.stop_loss_pct / 100)
                hit_target = r >= self.exit_rsi

                if hit_stop or hit_target:
                    pnl  = (price - position["entry"]) * position["qty"]
                    cash = cash + position["qty"] * price
                    trades.append({
                        "entry_date":  position["date"],
                        "exit_date":   candles[i]["date"],
                        "ticker":      position["ticker"],
                        "entry_price": position["entry"],
                        "exit_price":  price,
                        "qty":         position["qty"],
                        "pnl":         round(pnl, 2),
                        "pnl_pct":     round(pnl_pct * 100, 2),
                        "exit_reason": "STOP" if hit_stop else "RSI_EXIT",
                        "won":         pnl > 0,
                    })
                    position = None

            # Entry
            if not position:
                down_days    = consecutive_down_days(closes, i)
                above_trend  = price > ma
                oversold     = r is not None and r < self.rsi_threshold
                streak_ok    = down_days >= 3

                if above_trend and oversold and streak_ok:
                    qty = max(1, int((cash * self.position_pct) / price))
                    if qty * price <= cash:
                        cash -= qty * price
                        position = {
                            "ticker": "TICKER",
                            "entry":  price,
                            "qty":    qty,
                            "date":   candles[i]["date"]
                        }

            equity = cash + (position["qty"] * price if position else 0)
            equity_curve.append(round(equity, 2))

        # Close any open position at end
        if position:
            price = closes[-1]
            pnl   = (price - position["entry"]) * position["qty"]
            trades.append({
                "entry_date":  position["date"],
                "exit_date":   candles[-1]["date"],
                "entry_price": position["entry"],
                "exit_price":  price,
                "qty":         position["qty"],
                "pnl":         round(pnl, 2),
                "pnl_pct":     round((price - position["entry"]) / position["entry"] * 100, 2),
                "exit_reason": "END",
                "won":         pnl > 0,
            })

        return self._stats(trades, equity_curve, capital)

    def _stats(self, trades, equity_curve, initial_capital):
        return _compute_stats(trades, equity_curve, initial_capital, "Connors RSI(2)")


class SMABacktest:
    """
    SMA Crossover
    Entry: fast MA crosses above slow MA + volume confirmation + RSI filter
    Exit:  fast MA crosses below slow MA OR stop loss
    """
    def __init__(self, fast=9, slow=21, rsi_min=40, rsi_max=70,
                 stop_loss_pct=2.0, position_pct=0.10):
        self.fast          = fast
        self.slow          = slow
        self.rsi_min       = rsi_min
        self.rsi_max       = rsi_max
        self.stop_loss_pct = stop_loss_pct
        self.position_pct  = position_pct

    def run(self, candles: list, capital: float) -> dict:
        if len(candles) < self.slow + 10:
            return {"error": "Not enough data"}

        closes  = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        fast_ma = calc_sma(closes, self.fast)
        slow_ma = calc_sma(closes, self.slow)
        rsi14   = calc_rsi(closes, 14)
        vol_ma  = calc_sma(volumes, 20)

        trades, equity_curve = [], [capital]
        cash, position = capital, None

        for i in range(self.slow + 1, len(candles)):
            price = closes[i]
            fm, fm_prev = fast_ma[i], fast_ma[i-1]
            sm, sm_prev = slow_ma[i], slow_ma[i-1]
            r    = rsi14[i]
            vm   = vol_ma[i]
            vol  = volumes[i]
            if None in (fm, fm_prev, sm, sm_prev, r, vm):
                continue

            # Exit
            if position:
                pnl_pct    = (price - position["entry"]) / position["entry"]
                crossed_dn = fm_prev >= sm_prev and fm < sm
                hit_stop   = pnl_pct <= -(self.stop_loss_pct / 100)
                if crossed_dn or hit_stop:
                    pnl  = (price - position["entry"]) * position["qty"]
                    cash = cash + position["qty"] * price
                    trades.append({
                        "entry_date":  position["date"],
                        "exit_date":   candles[i]["date"],
                        "entry_price": position["entry"],
                        "exit_price":  price,
                        "qty":         position["qty"],
                        "pnl":         round(pnl, 2),
                        "pnl_pct":     round(pnl_pct * 100, 2),
                        "exit_reason": "STOP" if hit_stop else "CROSSDOWN",
                        "won":         pnl > 0,
                    })
                    position = None

            # Entry
            if not position:
                crossed_up = fm_prev <= sm_prev and fm > sm
                rsi_ok     = self.rsi_min <= r <= self.rsi_max
                vol_ok     = vol > vm
                if crossed_up and rsi_ok and vol_ok:
                    qty = max(1, int((cash * self.position_pct) / price))
                    if qty * price <= cash:
                        cash -= qty * price
                        position = {"entry": price, "qty": qty, "date": candles[i]["date"]}

            equity = cash + (position["qty"] * price if position else 0)
            equity_curve.append(round(equity, 2))

        if position:
            price = closes[-1]
            pnl   = (price - position["entry"]) * position["qty"]
            trades.append({
                "entry_date": position["date"], "exit_date": candles[-1]["date"],
                "entry_price": position["entry"], "exit_price": price,
                "qty": position["qty"], "pnl": round(pnl, 2),
                "pnl_pct": round((price-position["entry"])/position["entry"]*100, 2),
                "exit_reason": "END", "won": pnl > 0,
            })

        return _compute_stats(trades, equity_curve, capital, "SMA Crossover")


class ORBBacktest:
    """
    Opening Range Breakout (daily bars approximation)
    Uses daily data: entry when today's close breaks yesterday's high
    with volume confirmation. Stop at yesterday's low.
    """
    def __init__(self, stop_loss_pct=2.0, take_profit_pct=4.0, position_pct=0.10):
        self.stop_loss_pct   = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position_pct    = position_pct

    def run(self, candles: list, capital: float) -> dict:
        if len(candles) < 30:
            return {"error": "Not enough data"}

        trades, equity_curve = [], [capital]
        cash, position = capital, None

        vol_ma = calc_sma([c["volume"] for c in candles], 10)

        for i in range(10, len(candles)):
            c     = candles[i]
            prev  = candles[i - 1]
            price = c["close"]
            vm    = vol_ma[i]

            # Exit
            if position:
                pnl_pct    = (price - position["entry"]) / position["entry"]
                hit_stop   = pnl_pct <= -(self.stop_loss_pct / 100)
                hit_target = pnl_pct >= (self.take_profit_pct / 100)
                if hit_stop or hit_target:
                    pnl  = (price - position["entry"]) * position["qty"]
                    cash = cash + position["qty"] * price
                    trades.append({
                        "entry_date":  position["date"],
                        "exit_date":   c["date"],
                        "entry_price": position["entry"],
                        "exit_price":  price,
                        "qty":         position["qty"],
                        "pnl":         round(pnl, 2),
                        "pnl_pct":     round(pnl_pct * 100, 2),
                        "exit_reason": "STOP" if hit_stop else "TARGET",
                        "won":         pnl > 0,
                    })
                    position = None

            # Entry: close breaks above prior high with volume
            if not position and vm:
                breakout = price > prev["high"] * 1.001
                vol_ok   = c["volume"] > vm * 1.2
                if breakout and vol_ok:
                    qty = max(1, int((cash * self.position_pct) / price))
                    if qty * price <= cash:
                        cash -= qty * price
                        position = {"entry": price, "qty": qty, "date": c["date"]}

            equity = cash + (position["qty"] * price if position else 0)
            equity_curve.append(round(equity, 2))

        if position:
            price = candles[-1]["close"]
            pnl   = (price - position["entry"]) * position["qty"]
            trades.append({
                "entry_date": position["date"], "exit_date": candles[-1]["date"],
                "entry_price": position["entry"], "exit_price": price,
                "qty": position["qty"], "pnl": round(pnl, 2),
                "pnl_pct": round((price-position["entry"])/position["entry"]*100, 2),
                "exit_reason": "END", "won": pnl > 0,
            })

        return _compute_stats(trades, equity_curve, capital, "Opening Range Breakout")


# ── Core stats engine ─────────────────────────────────────────

def _compute_stats(trades: list, equity_curve: list, initial_capital: float,
                   strategy_name: str) -> dict:
    if not trades:
        return {
            "strategy": strategy_name,
            "error": "No trades generated — try a wider date range or different parameters"
        }

    wins   = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]
    pnls   = [t["pnl"] for t in trades]

    total_return    = sum(pnls)
    total_return_pct = (total_return / initial_capital) * 100
    win_rate        = len(wins) / len(trades) * 100
    avg_win         = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss        = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    profit_factor   = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
                       if losses and avg_loss != 0 else 999)
    max_drawdown    = _max_drawdown(equity_curve)
    sharpe          = _sharpe_ratio(equity_curve)
    sortino         = _sortino_ratio(equity_curve)
    calmar          = (total_return_pct / abs(max_drawdown)) if max_drawdown != 0 else 0

    # Consecutive wins/losses
    max_consec_wins   = _max_consecutive(trades, True)
    max_consec_losses = _max_consecutive(trades, False)

    # Monthly breakdown
    monthly = _monthly_breakdown(trades)

    # Walk-forward (split into 3 segments)
    wf = _walk_forward(trades, initial_capital)

    # Monte Carlo
    mc = _monte_carlo(pnls, initial_capital)

    return {
        "strategy":          strategy_name,
        "initial_capital":   initial_capital,
        "final_capital":     round(equity_curve[-1], 2),
        "total_return":      round(total_return, 2),
        "total_return_pct":  round(total_return_pct, 2),
        "win_rate":          round(win_rate, 1),
        "total_trades":      len(trades),
        "winning_trades":    len(wins),
        "losing_trades":     len(losses),
        "avg_win":           round(avg_win, 2),
        "avg_loss":          round(avg_loss, 2),
        "profit_factor":     round(profit_factor, 2),
        "max_drawdown_pct":  round(max_drawdown, 2),
        "sharpe_ratio":      round(sharpe, 2),
        "sortino_ratio":     round(sortino, 2),
        "calmar_ratio":      round(calmar, 2),
        "max_consec_wins":   max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "equity_curve":      equity_curve[::max(1, len(equity_curve)//200)],  # sample to 200pts
        "trades":            trades,
        "monthly":           monthly,
        "walk_forward":      wf,
        "monte_carlo":       mc,
    }


def _max_drawdown(equity_curve: list) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe_ratio(equity_curve: list, risk_free: float = 0.05) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
               for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
    if not returns:
        return 0.0
    avg_r = sum(returns) / len(returns)
    std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns))
    daily_rf = risk_free / 252
    return ((avg_r - daily_rf) / std_r * math.sqrt(252)) if std_r > 0 else 0.0


def _sortino_ratio(equity_curve: list, risk_free: float = 0.05) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
               for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
    if not returns:
        return 0.0
    avg_r    = sum(returns) / len(returns)
    neg      = [r for r in returns if r < 0]
    down_std = math.sqrt(sum(r**2 for r in neg) / len(neg)) if neg else 0
    daily_rf = risk_free / 252
    return ((avg_r - daily_rf) / down_std * math.sqrt(252)) if down_std > 0 else 0.0


def _max_consecutive(trades: list, wins: bool) -> int:
    max_c, cur = 0, 0
    for t in trades:
        if t["won"] == wins:
            cur += 1
            max_c = max(max_c, cur)
        else:
            cur = 0
    return max_c


def _monthly_breakdown(trades: list) -> list:
    monthly = {}
    for t in trades:
        month = t["entry_date"][:7]
        monthly.setdefault(month, {"trades": 0, "pnl": 0.0, "wins": 0})
        monthly[month]["trades"] += 1
        monthly[month]["pnl"]    += t["pnl"]
        if t["won"]:
            monthly[month]["wins"] += 1
    result = []
    for month, d in sorted(monthly.items()):
        result.append({
            "month":    month,
            "trades":   d["trades"],
            "pnl":      round(d["pnl"], 2),
            "win_rate": round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0
        })
    return result


def _walk_forward(trades: list, capital: float) -> list:
    """Split trades into 3 segments, test each independently."""
    if len(trades) < 9:
        return []
    seg_size = len(trades) // 3
    results  = []
    for i in range(3):
        seg   = trades[i * seg_size:(i + 1) * seg_size]
        wins  = sum(1 for t in seg if t["won"])
        pnl   = sum(t["pnl"] for t in seg)
        wr    = wins / len(seg) * 100 if seg else 0
        results.append({
            "segment":    f"Segment {i+1}",
            "trades":     len(seg),
            "win_rate":   round(wr, 1),
            "pnl":        round(pnl, 2),
            "return_pct": round(pnl / capital * 100, 2),
        })
    return results


def _monte_carlo(pnls: list, capital: float, simulations: int = 1000) -> dict:
    """
    Shuffle trade order 1000 times to test robustness.
    Returns worst/median/best case outcomes.
    """
    if not pnls:
        return {}
    final_equities = []
    max_drawdowns  = []

    for _ in range(simulations):
        shuffled = pnls[:]
        random.shuffle(shuffled)
        equity = capital
        peak   = capital
        max_dd = 0.0
        for pnl in shuffled:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        final_equities.append(equity)
        max_drawdowns.append(max_dd)

    final_equities.sort()
    max_drawdowns.sort()
    n = len(final_equities)

    return {
        "simulations":      simulations,
        "worst_final":      round(final_equities[int(n * 0.05)], 2),
        "median_final":     round(final_equities[n // 2], 2),
        "best_final":       round(final_equities[int(n * 0.95)], 2),
        "worst_drawdown":   round(max_drawdowns[int(n * 0.95)], 2),
        "median_drawdown":  round(max_drawdowns[n // 2], 2),
        "prob_profitable":  round(sum(1 for e in final_equities if e > capital) / n * 100, 1),
    }


# ── Main backtest runner ──────────────────────────────────────

def run_backtest(
    strategy:    str,
    ticker:      str,
    start:       str,
    end:         str,
    capital:     float = 1000.0,
    params:      dict  = None
) -> dict:
    """
    Main entry point. Called by agent and dashboard.
    strategy: "connors" | "sma" | "orb"
    Returns full stats dict ready to save to Supabase.
    """
    params = params or {}
    log.info(f"Running backtest: {strategy} on {ticker} {start}→{end} capital=${capital}")

    candles = fetch_ohlcv(ticker, start, end, interval="1d")
    if not candles:
        return {"error": f"Could not fetch data for {ticker}. Check ticker symbol."}

    if strategy == "connors":
        engine = ConnorsRSIBacktest(
            rsi_threshold = params.get("rsi_threshold", 10),
            trend_period  = params.get("trend_period", 200),
            exit_rsi      = params.get("exit_rsi", 70),
            stop_loss_pct = params.get("stop_loss_pct", None),
            position_pct  = params.get("position_pct", 0.10),
        )
    elif strategy == "sma":
        engine = SMABacktest(
            fast          = params.get("fast", 9),
            slow          = params.get("slow", 21),
            rsi_min       = params.get("rsi_min", 40),
            rsi_max       = params.get("rsi_max", 70),
            stop_loss_pct = params.get("stop_loss_pct", 2.0),
            position_pct  = params.get("position_pct", 0.10),
        )
    elif strategy == "orb":
        engine = ORBBacktest(
            stop_loss_pct   = params.get("stop_loss_pct", 2.0),
            take_profit_pct = params.get("take_profit_pct", 4.0),
            position_pct    = params.get("position_pct", 0.10),
        )
    else:
        return {"error": f"Unknown strategy: {strategy}"}

    result = engine.run(candles, capital)
    result["ticker"]     = ticker
    result["start_date"] = start
    result["end_date"]   = end
    result["candle_count"] = len(candles)
    result["run_at"]     = datetime.now().isoformat()
    return result


# ── Multi-strategy comparison ─────────────────────────────────

def run_comparison(ticker: str, start: str, end: str, capital: float = 1000.0) -> dict:
    """Run all 3 strategies on the same data and return ranked comparison."""
    results = {}
    for strat in ["connors", "sma", "orb"]:
        results[strat] = run_backtest(strat, ticker, start, end, capital)

    # Rank by Sharpe ratio
    ranked = sorted(
        [(k, v) for k, v in results.items() if "error" not in v],
        key=lambda x: x[1].get("sharpe_ratio", 0),
        reverse=True
    )
    return {
        "ticker":  ticker,
        "start":   start,
        "end":     end,
        "results": results,
        "ranking": [{"strategy": k, "sharpe": v.get("sharpe_ratio"), "return_pct": v.get("total_return_pct"), "win_rate": v.get("win_rate")} for k, v in ranked],
        "winner":  ranked[0][0] if ranked else None,
    }
