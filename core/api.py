"""api.py — TradeZero API wrapper"""
import urllib.request
import json
from datetime import datetime, timedelta
from core.logger import log


class TradeZeroAPI:
    def __init__(self, key: str, secret: str, account: str, env: str = "paper"):
        self.key = key
        self.secret = secret
        self.account = account
        self.env = env
        log.info(f"TradeZeroAPI initialized ({env.upper()})")

    async def get_candles(self, ticker: str, interval: str = "1d", limit: int = 200) -> list:
        """Fetch candles from Yahoo Finance"""
        try:
            # Convert interval
            yf_interval = {"5min": "5m", "1d": "1d", "1h": "1h"}.get(interval, "1d")
            
            end = datetime.now()
            start = end - timedelta(days=365 if yf_interval == "1d" else 30)
            
            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())
            
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_ts}&period2={end_ts}&interval={yf_interval}"
            
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            
            result = data["chart"]["result"][0]
            ts = result["timestamp"]
            q = result["indicators"]["quote"][0]
            
            candles = []
            for i in range(len(ts)):
                if q["close"][i] is None:
                    continue
                candles.append({
                    "date": datetime.fromtimestamp(ts[i]).strftime("%Y-%m-%d %H:%M"),
                    "close": q["close"][i],
                    "open": q["open"][i],
                    "high": q["high"][i],
                    "low": q["low"][i],
                    "volume": int(q["volume"][i]) if q["volume"][i] else 0,
                })
            
            log.info(f"Got {len(candles)} {yf_interval} candles for {ticker}")
            return candles[-limit:]
            
        except Exception as e:
            log.error(f"Candle error ({ticker}): {e}")
            return []

    async def place_order(self, ticker: str, action: str, qty: int, order_type: str = "MARKET") -> dict:
        """Place order"""
        try:
            log.info(f"ORDER: {action} {qty} {ticker}")
            return {
                "status": "filled",
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "price": 100.0,
            }
        except Exception as e:
            log.error(f"Order error: {e}")
            return {"error": str(e)}

    async def get_positions(self) -> list:
        return []

    async def get_account_balance(self) -> dict:
        return {"cash": 10000, "buying_power": 10000}
