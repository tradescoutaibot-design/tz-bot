"""
TradeZero API wrapper
Handles auth, market data, order execution, and account info.
Base URL: https://webapi.tradezero.com
"""
import aiohttp
import asyncio
import json
import time
from datetime import datetime
from core.logger import log


class TradeZeroAPI:
    BASE_URL = "https://webapi.tradezero.com"

    def __init__(self, key: str, secret: str, account_id: str, env: str = "paper"):
        self.key = key
        self.secret = secret
        self.account_id = account_id
        self.env = env  # "paper" or "live"
        self.token = None
        self.token_expires = 0
        self.session = None
        self.killed = False

    # ── Connection ──────────────────────────────────────────

    async def connect(self) -> bool:
        """Authenticate and open HTTP session."""
        if not self.key or not self.secret:
            log.error("No API keys configured. Run setup.py first.")
            return False
        try:
            self.session = aiohttp.ClientSession()
            ok = await self._authenticate()
            return ok
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

    async def _authenticate(self) -> bool:
        """Get bearer token from TradeZero."""
        try:
            payload = {
                "username": self.key,
                "password": self.secret,
                "accountId": self.account_id
            }
            async with self.session.post(
                f"{self.BASE_URL}/api/auth/login",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.token = data.get("token") or data.get("access_token")
                    self.token_expires = time.time() + 3600
                    log.info(f"Authenticated with TradeZero ({self.env})")
                    return True
                else:
                    body = await resp.text()
                    log.error(f"Auth failed ({resp.status}): {body}")
                    return False
        except aiohttp.ClientConnectorError:
            log.error("Cannot reach TradeZero API — check internet connection")
            return False
        except Exception as e:
            log.error(f"Auth error: {e}")
            return False

    async def _headers(self) -> dict:
        """Return auth headers, refreshing token if needed."""
        if time.time() > self.token_expires - 60:
            await self._authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    # ── Account ─────────────────────────────────────────────

    async def get_account(self) -> dict:
        """Fetch account balance and buying power."""
        try:
            headers = await self._headers()
            async with self.session.get(
                f"{self.BASE_URL}/api/account/{self.account_id}",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                log.error(f"get_account failed: {resp.status}")
                return {}
        except Exception as e:
            log.error(f"get_account error: {e}")
            return {}

    async def get_positions(self) -> list:
        """Get all open positions."""
        try:
            headers = await self._headers()
            async with self.session.get(
                f"{self.BASE_URL}/api/positions/{self.account_id}",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            log.error(f"get_positions error: {e}")
            return []

    async def get_orders(self) -> list:
        """Get today's orders."""
        try:
            headers = await self._headers()
            async with self.session.get(
                f"{self.BASE_URL}/api/orders/{self.account_id}",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            log.error(f"get_orders error: {e}")
            return []

    # ── Market Data ─────────────────────────────────────────

    async def get_quote(self, ticker: str) -> dict:
        """Get real-time quote for a ticker."""
        try:
            headers = await self._headers()
            async with self.session.get(
                f"{self.BASE_URL}/api/marketdata/quote/{ticker}",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {}
        except Exception as e:
            log.error(f"get_quote({ticker}) error: {e}")
            return {}

    async def get_candles(self, ticker: str, interval: str = "1min", limit: int = 50) -> list:
        """Get OHLCV candles. interval: 1min, 5min, 15min, 1day"""
        try:
            headers = await self._headers()
            params = {"interval": interval, "limit": limit}
            async with self.session.get(
                f"{self.BASE_URL}/api/marketdata/candles/{ticker}",
                headers=headers, params=params
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            log.error(f"get_candles({ticker}) error: {e}")
            return []

    # ── Orders ───────────────────────────────────────────────

    async def place_order(
        self,
        ticker: str,
        action: str,       # "BUY" | "SELL" | "SHORT" | "COVER"
        qty: int,
        order_type: str = "MARKET",  # "MARKET" | "LIMIT" | "STOP"
        price: float = None,
        stop_price: float = None
    ) -> dict:
        """Place an order. Returns order confirmation dict."""
        if self.killed:
            log.error("ORDER BLOCKED — kill switch is active")
            return {"error": "kill_switch_active"}

        payload = {
            "accountId": self.account_id,
            "symbol": ticker.upper(),
            "side": action.upper(),
            "quantity": qty,
            "orderType": order_type.upper(),
            "timeInForce": "DAY"
        }
        if price and order_type.upper() in ("LIMIT", "STOP_LIMIT"):
            payload["limitPrice"] = round(price, 2)
        if stop_price:
            payload["stopPrice"] = round(stop_price, 2)

        try:
            headers = await self._headers()
            async with self.session.post(
                f"{self.BASE_URL}/api/orders",
                headers=headers,
                json=payload
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    log.info(f"ORDER PLACED: {action} {qty} {ticker} @ {order_type} [{self.env.upper()}]")
                    return data
                else:
                    log.error(f"Order failed ({resp.status}): {data}")
                    return {"error": str(data)}
        except Exception as e:
            log.error(f"place_order error: {e}")
            return {"error": str(e)}

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific open order."""
        try:
            headers = await self._headers()
            async with self.session.delete(
                f"{self.BASE_URL}/api/orders/{order_id}",
                headers=headers
            ) as resp:
                if resp.status in (200, 204):
                    log.info(f"Order {order_id} cancelled")
                    return True
                return False
        except Exception as e:
            log.error(f"cancel_order error: {e}")
            return False

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        orders = await self.get_orders()
        open_orders = [o for o in orders if o.get("status") in ("OPEN", "PENDING", "WORKING")]
        count = 0
        for o in open_orders:
            ok = await self.cancel_order(o.get("orderId") or o.get("id"))
            if ok:
                count += 1
        log.info(f"Cancelled {count} open orders")
        return count

    # ── Kill Switch ───────────────────────────────────────────

    async def kill(self):
        """Hard stop — cancel all orders immediately."""
        self.killed = True
        log.error("=== KILL SWITCH ACTIVATED ===")
        cancelled = await self.cancel_all_orders()
        log.error(f"Cancelled {cancelled} orders. Bot is now halted.")

    def release_kill(self):
        """Release the kill switch to allow trading again."""
        self.killed = False
        log.info("Kill switch released — trading re-enabled")

    async def close(self):
        if self.session:
            await self.session.close()
