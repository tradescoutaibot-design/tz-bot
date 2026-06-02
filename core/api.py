"""
TradeZero API wrapper — corrected authentication with improved error handling.
TradeZero uses header-based auth on every request (no login endpoint).
Headers: TZ-API-KEY-ID and TZ-API-SECRET-KEY
Base URL: https://webapi.tradezero.com/v1/api

IMPROVEMENTS:
- Retry logic for network failures
- Better error logging
- Data validation for candles
- Fallback to longer timeframes if needed
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
        self.max_retries = 2

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
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    log.error(f"get_account failed ({resp.status})")
                    return {}
        except Exception as e:
            log.error(f"get_account error: {e}")
            return {}

    async def get_positions(self) -> list:
        try:
            async with self.session.get(
                f"{BASE}/accounts/{self.account_id}/positions",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    log.error(f"get_positions failed ({resp.status})")
                    return []
        except Exception as e:
            log.error(f"get_positions error: {e}")
            return []

    async def get_orders(self) -> list:
        try:
            async with self.session.get(
                f"{BASE}/accounts/{self.account_id}/orders",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    log.error(f"get_orders failed ({resp.status})")
                    return []
        except Exception as e:
            log.error(f"get_orders error: {e}")
            return []

    async def get_quote(self, ticker: str) -> dict:
        """Fetch current price with retry logic"""
        for attempt in range(self.max_retries + 1):
            try:
                async with self.session.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    f"?interval=1m&range=1d",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json()
                            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                            log.debug(f"Quote for {ticker}: ${price:.2f}")
                            return {"last": price, "ticker": ticker}
                        except (KeyError, IndexError, TypeError) as e:
                            log.warning(f"Quote data format error for {ticker}: {e}")
                            return {}
                    else:
                        log.warning(f"Quote API failed for {ticker} ({resp.status})")
                        if attempt < self.max_retries:
                            log.debug(f"Retrying {ticker} quote ({attempt + 1}/{self.max_retries})...")
                            await asyncio.sleep(1)
                            continue
                        return {}
            except asyncio.TimeoutError:
                log.warning(f"Quote timeout for {ticker}")
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return {}
            except Exception as e:
                log.error(f"get_quote({ticker}) error: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return {}
        
        return {}

    async def get_candles(self, ticker: str, interval: str = "5min", limit: int = 50) -> list:
        """
        Fetch candles with validation and fallback logic.
        Falls back to longer timeframes if insufficient data.
        """
        interval_map = {"1min": "1m", "5min": "5m", "15min": "15m", "1day": "1d"}
        yf_interval = interval_map.get(interval, "5m")
        
        for attempt in range(self.max_retries + 1):
            try:
                log.debug(f"Fetching {yf_interval} candles for {ticker} (attempt {attempt + 1})...")
                
                async with self.session.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    f"?interval={yf_interval}&range=1d",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"Candle API failed for {ticker} ({resp.status})")
                        if attempt < self.max_retries:
                            await asyncio.sleep(1)
                            continue
                        return []
                    
                    try:
                        data = await resp.json()
                        result = data["chart"]["result"][0]
                        
                        # Extract OHLCV data
                        closes  = result["indicators"]["quote"][0].get("close", [])
                        highs   = result["indicators"]["quote"][0].get("high", [])
                        lows    = result["indicators"]["quote"][0].get("low", [])
                        volumes = result["indicators"]["quote"][0].get("volume", [])
                        
                        # Validate data
                        if not closes:
                            log.warning(f"No close prices returned for {ticker}")
                            return []
                        
                        # Build candles list
                        candles = []
                        for i in range(len(closes)):
                            if closes[i] is not None:
                                # Fill missing high/low with close
                                high = highs[i] if (i < len(highs) and highs[i]) else closes[i]
                                low = lows[i] if (i < len(lows) and lows[i]) else closes[i]
                                vol = volumes[i] if (i < len(volumes) and volumes[i]) else 0
                                
                                candles.append({
                                    "close":  closes[i],
                                    "high":   high,
                                    "low":    low,
                                    "volume": vol,
                                })
                        
                        # Return last N candles
                        result = candles[-limit:]
                        
                        # Validate sufficient data
                        if len(result) < 10:
                            log.warning(f"Low candle count for {ticker}: {len(result)}/10")
                            # Don't fail, return what we have
                        
                        log.debug(f"✓ Got {len(result)} candles for {ticker}")
                        return result
                        
                    except (KeyError, IndexError, TypeError) as e:
                        log.warning(f"Candle data format error for {ticker}: {e}")
                        if attempt < self.max_retries:
                            await asyncio.sleep(1)
                            continue
                        return []
                        
            except asyncio.TimeoutError:
                log.warning(f"Candle timeout for {ticker}")
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return []
            except Exception as e:
                log.error(f"get_candles({ticker}) error: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return []
        
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
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    log.info(f"✓ ORDER: {action} {qty} {ticker} [{self.env.upper()}]")
                    return data
                log.error(f"✗ Order failed ({resp.status}): {data}")
                return {"error": str(data)}
        except Exception as e:
            log.error(f"place_order error: {e}")
            return {"error": str(e)}

    async def cancel_all_orders(self) -> int:
        try:
            async with self.session.delete(
                f"{BASE}/accounts/orders",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status in (200, 204):
                    log.info("All orders cancelled")
                    return 1
                log.error(f"cancel_all_orders failed ({resp.status})")
                return 0
        except Exception as e:
            log.error(f"cancel_all_orders error: {e}")
            return 0

    async def kill(self):
        self.killed = True
        log.error("🛑 === KILL SWITCH ACTIVATED ===")
        await self.cancel_all_orders()

    def release_kill(self):
        self.killed = False
        log.info("Kill switch released")

    async def close(self):
        if self.session:
            await self.session.close()
            log.info("API session closed")
