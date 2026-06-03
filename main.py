"""
main.py — Entry point for TZ autonomous trading bot.
Runs on Railway 24/7.

Setup: TradeZero API → Supabase memory → Agent loop
"""
import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Import core modules
try:
    from core.api import TradeZeroAPI
    from core.memory import Memory
    from core.agent import Agent
    from core.logger import log
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure all core modules are present")
    sys.exit(1)

ET = ZoneInfo("America/New_York")


async def main():
    """Main entry point."""
    log.info("=" * 70)
    log.info("TZ AUTONOMOUS TRADING BOT — STARTING UP")
    log.info("=" * 70)
    log.info(f"Start time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Check environment
    tz_env = os.getenv("TZ_ENV", "paper").lower()
    if tz_env not in ("paper", "live"):
        log.error(f"Invalid TZ_ENV: {tz_env} (use 'paper' or 'live')")
        sys.exit(1)
    
    log.info(f"Environment: {tz_env.upper()}")

    # Check credentials
    api_key = os.getenv("TZ_API_KEY", "").strip()
    api_secret = os.getenv("TZ_API_SECRET", "").strip()
    account_id = os.getenv("TZ_ACCOUNT_ID", "").strip()

    if not all([api_key, api_secret, account_id]):
        log.error("Missing TradeZero credentials")
        log.error("Required Railway variables:")
        log.error("  - TZ_API_KEY")
        log.error("  - TZ_API_SECRET")
        log.error("  - TZ_ACCOUNT_ID")
        sys.exit(1)

    # Initialize memory
    try:
        memory = Memory()
        log.info("✓ Memory layer initialized (env vars + local JSON + Supabase)")
    except Exception as e:
        log.error(f"Memory init failed: {e}")
        sys.exit(1)

    # Initialize API
    try:
        api = TradeZeroAPI(
            api_key=api_key,
            api_secret=api_secret,
            account_id=account_id,
            env=tz_env,
        )
        log.info(f"✓ TradeZero API connected ({tz_env.upper()} | account: {account_id})")
    except Exception as e:
        log.error(f"API init failed: {e}")
        sys.exit(1)

    # Initialize agent
    try:
        agent = Agent(api, memory)
        log.info("✓ Agent initialized (Connors RSI(2) strategy)")
    except Exception as e:
        log.error(f"Agent init failed: {e}")
        sys.exit(1)

    log.info("")
    log.info("AGENT LOOP STARTING")
    log.info("-" * 70)
    log.info("Trading hours: 9:30 AM - 4:00 PM ET")
    log.info("No new entries after: 3:45 PM ET")
    log.info("Scan interval: 5 minutes")
    log.info("Strategy: Connors RSI(2) mean reversion")
    log.info("Watchlist: SPY, AAPL, TSLA, QQQ, NVDA")
    log.info("-" * 70)
    log.info("")

    # Run agent loop
    try:
        await agent.run()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        log.error(f"Agent loop crashed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown")
        sys.exit(0)
