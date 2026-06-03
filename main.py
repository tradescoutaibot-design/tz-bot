import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core.api import TradeZeroAPI
from core.memory import Memory
from core.agent import Agent
from core.logger import log

ET = ZoneInfo("America/New_York")

async def main():
    log.info("=" * 70)
    log.info("TZ AUTONOMOUS TRADING BOT — STARTING UP")
    log.info("=" * 70)
    log.info(f"Start time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    tz_env = os.getenv("TZ_ENV", "paper").lower()
    log.info(f"Environment: {tz_env.upper()}")

    key = os.getenv("TZ_API_KEY", "").strip()
    secret = os.getenv("TZ_API_SECRET", "").strip()
    account = os.getenv("TZ_ACCOUNT_ID", "").strip()

    if not all([key, secret, account]):
        log.error("Missing credentials: TZ_API_KEY, TZ_API_SECRET, TZ_ACCOUNT_ID")
        sys.exit(1)

    try:
        memory = Memory()
        log.info("✓ Memory initialized")
    except Exception as e:
        log.error(f"Memory init failed: {e}")
        sys.exit(1)

    try:
        api = TradeZeroAPI(key=key, secret=secret, account=account, env=tz_env)
        log.info(f"✓ TradeZero API connected")
    except Exception as e:
        log.error(f"API init failed: {e}")
        sys.exit(1)

    try:
        agent = Agent(api, memory)
        log.info("✓ Agent initialized")
    except Exception as e:
        log.error(f"Agent init failed: {e}")
        sys.exit(1)

    log.info("")
    log.info("STARTING AGENT LOOP")
    log.info("-" * 70)

    try:
        await agent.run()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.error(f"Agent crashed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
