"""
TZ Trading Bot — main entry point
Run: python main.py
"""
import asyncio
import signal
import sys
from core.agent import TradingAgent
from core.memory import Memory
from core.api import TradeZeroAPI

async def main():
    print("=" * 50)
    print("  TZ AUTONOMOUS TRADING BOT")
    print("  Starting up...")
    print("=" * 50)

    # Load memory (keys, learned rules, history)
    memory = Memory()
    memory.load()

    # Connect to TradeZero
    api = TradeZeroAPI(
        key=memory.get("tz_api_key"),
        secret=memory.get("tz_api_secret"),
        account_id=memory.get("tz_account_id"),
        env=memory.get("tz_env", "paper")
    )

    connected = await api.connect()
    if not connected:
        print("[ERROR] Could not connect to TradeZero.")
        print("  → Check your keys in data/config.json")
        print("  → Or run: python setup.py to configure")
        sys.exit(1)

    print(f"[OK] Connected to TradeZero ({api.env.upper()})")
    print(f"[OK] Account: {api.account_id}")

    # Start agent
    agent = TradingAgent(api=api, memory=memory)

    # Graceful kill switch (Ctrl+C or SIGTERM)
    def kill_handler(sig, frame):
        print("\n[KILL] Kill switch triggered — shutting down safely...")
        asyncio.create_task(agent.emergency_stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, kill_handler)
    signal.signal(signal.SIGTERM, kill_handler)

    print("[OK] Kill switch armed (Ctrl+C to trigger)")
    print("[OK] Agent ready. Listening for instructions...\n")

    await agent.run_loop()

if __name__ == "__main__":
    asyncio.run(main())
