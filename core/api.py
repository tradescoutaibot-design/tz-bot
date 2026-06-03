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
        self.base_url = "https://webapi.tradezero.com/v1/api"
        log.info(f"TradeZeroAPI initialized ({env.upper()})")

    async def get_candles(self, ticker: str, interval: str = "5min", limit: int = 200) -> list:
        try:
            end = datetime.now()
            start = end - timedelta(days=60)
            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_ts}&period2={end_ts}&interval=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            
            result = data["chart"]["result"][0]
            ts = result["timestamps"]
            q = result["indicators"]["quote"][0]
            adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
            
            candles = []
            for i, t in enumerate(ts):
                if None in (q["close"][i], q["open"][i]):
                    continue
                candles.append({
                    "date": datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                    "open": q["open"][i],
                    "high": q["high"][i],
                    "low": q["low"][i],
                    "close": adj[i] if adj[i] else q["close"][i],
                    "volume": int(q["volume"][i]) if q["volume"][i] else 0,
                })
            
            return candles[-limit:]
        except Exception as e:
            log.error(f"Candle fetch error ({ticker}): {e}")
            return []

    async def place_order(self, ticker: str, action: str, qty: int, order_type: str = "MARKET") -> dict:
        try:
            log.info(f"Order: {action} {qty} {ticker} ({self.env.upper()})")
            if self.env == "paper":
                return {"status": "filled", "ticker": ticker, "action": action, "qty": qty, "price": 150.0}
            else:
                log.error("Live trading not yet implemented")
                return {"error": "Live trading disabled"}
        except Exception as e:
            log.error(f"Order error: {e}")
            return {"error": str(e)}

    async def get_positions(self) -> list:
        return []

    async def get_account_balance(self) -> dict:
        return {"cash": 10000, "buying_power": 10000}
