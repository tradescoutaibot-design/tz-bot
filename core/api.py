"""
TradeZero API wrapper — corrected authentication.
TradeZero uses header-based auth on every request (no login endpoint).
Headers: TZ-API-KEY-ID and TZ-API-SECRET-KEY
Base URL: https://webapi.tradezero.com/v1/api
"""
import aiohttp
import asyncio
from core.logger import log

BASE = "https://webapi.tradezero.com/v1/api"


class TradeZeroAPI:
    def __init__(self, key: str, secret: str, account_id: str, env: str = "paper"):
        self.key        = key
        self.secret     = secret
        self.account_id = account_id
        self.env        = env
        self.session    = None
        self.killed     = False

    def _headers(self) -> dict:
        return {
            "Accept":           "application/json",
            "Content-Type":     "application/json",
            "TZ-API-KEY-ID":    self.key,
            "TZ-API-SECRET-KEY": self.secret,
        }

    async def connect(self) -> bool:
        if not self.key or not self.secret:
            log.error("No API keys configured.")
            return False
        self.session = aiohttp.ClientSession()
        # Test connection by fetching accounts
        try:
            async with self.session.get(
                f"{BASE}/accounts",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    log.info(f"Connected to TradeZero ({self.env.upper()})")
                    return True
                else:
                    body = await resp.text()
                    log.error(f"TradeZero connection failed ({resp.status}): {body}")
                    return False
        except Exception as e:
            log.error(f"TradeZero connection error: {e}")
            return False

    async def get_account(self) -> dict:
        try:
            async with self.session.get(
                f"{BASE}/account/{self.account_id}",
                headers=self._headers()
            ) as resp:
                return await resp.json() if resp.status == 200 else {}
        except Exception as e:
            log.error(f"get_account error: {e}")
            return {}

    async def get_positions(self) -> list:
        try:
            async with self.session.get(
                f"{BASE}/accounts/{self.account_id}/positions",
                headers=self._headers()
            ) as resp:
                return await resp.json() if resp.status == 200 else []
        except Exception as e:
            log.error(f"get_positions error: {e}")
            return []

    async def get_orders(self) -> list:
        try:
            async with self.session.get(
                f"{BASE}/accounts/{self.account_id}/orders",
                headers=self._headers()
            ) as resp:
                return await resp.json() if resp.status == 200 else []
        except Exception as e:
            log.error(f"get_orders error: {e}")
            return []

    async def get_quote(self, ticker: str) -> dict:
        # TradeZero API does not provide market data quotes
        # Use a free data source instead
        try:
            async with self.session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    return {"last": price, "ticker": ticker}
                return {}
        except Exception as e:
            log.error(f"get_quote({ticker}) error: {e}")
            return {}

    async def get_candles(self, ticker: str, interval: str = "5min", limit: int = 50) -> list:
        # Use Yahoo Finance for market data
        interval_map = {"1min": "1m", "5min": "5m", "15min": "15m", "1day": "1d"}
        yf_interval = interval_map.get(interval, "5m")
        try:
            async with self.session.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?interval={yf_interval}&range=1d",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return []
                data  = await resp.json()
                result = data["chart"]["result"][0]
                closes  = result["indicators"]["quote"][0].get("close", [])
                highs   = result["indicators"]["quote"][0].get("high", [])
                lows    = result["indicators"]["quote"][0].get("low", [])
                volumes = result["indicators"]["quote"][0].get("volume", [])
                candles = []
                for i in range(len(closes)):
                    if closes[i] is not None:
                        candles.append({
                            "close":  closes[i],
                            "high":   highs[i]   if highs[i]   else closes[i],
                            "low":    lows[i]    if lows[i]    else closes[i],
                            "volume": volumes[i] if volumes[i] else 0,
                        })
                return candles[-limit:]
        except Exception as e:
            log.error(f"get_candles({ticker}) error: {e}")
            return []

    async def place_order(self, ticker: str, action: str, qty: int,
                          order_type: str = "MARKET", price: float = None,
                          stop_price: float = None) -> dict:
        if self.killed:
            log.error("ORDER BLOCKED — kill switch active")
            return {"error": "kill_switch_active"}

        side_map = {"BUY": "Buy", "SELL": "Sell", "SHORT": "Sell", "COVER": "Buy"}
        open_close = "Open" if action in ("BUY", "SHORT") else "Close"

        payload = {
            "securityType":  "Stock",
            "symbol":        ticker.upper(),
            "side":          side_map.get(action.upper(), "Buy"),
            "openClose":     open_close,
            "orderType":     order_type.capitalize(),
            "orderQuantity": qty,
            "timeInForce":   "Day",
        }
        if price and order_type.upper() == "LIMIT":
            payload["limitPrice"] = round(price, 2)
        if stop_price:
            payload["stopPrice"] = round(stop_price, 2)

        try:
            async with self.session.post(
                f"{BASE}/accounts/{self.account_id}/order",
                headers=self._headers(),
                json=payload
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    log.info(f"ORDER: {action} {qty} {ticker} [{self.env.upper()}]")
                    return data
                log.error(f"Order failed ({resp.status}): {data}")
                return {"error": str(data)}
        except Exception as e:
            log.error(f"place_order error: {e}")
            return {"error": str(e)}

    async def cancel_all_orders(self) -> int:
        try:
            async with self.session.delete(
                f"{BASE}/accounts/orders",
                headers=self._headers()
            ) as resp:
                if resp.status in (200, 204):
                    log.info("All orders cancelled")
                    return 1
                return 0
        except Exception as e:
            log.error(f"cancel_all_orders error: {e}")
            return 0

    async def kill(self):
        self.killed = True
        log.error("=== KILL SWITCH ACTIVATED ===")
        await self.cancel_all_orders()

    def release_kill(self):
        self.killed = False
        log.info("Kill switch released")

    async def close(self):
        if self.session:
            await self.session.close()
