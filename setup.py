"""
setup.py — run this once to configure your bot.
python setup.py
"""
import json, os

MEMORY_FILE = os.path.join("data", "memory.json")

def setup():
    print("\n" + "="*50)
    print("  TZ BOT SETUP")
    print("="*50)
    print("Keys are saved to data/memory.json on your device.\n")

    key    = input("TradeZero API key:    ").strip()
    secret = input("TradeZero API secret: ").strip()
    acct   = input("Account ID:           ").strip()
    env    = input("Environment [paper/live] (default: paper): ").strip() or "paper"

    print("\n--- Gemini AI learning (free, no credit card needed) ---")
    print("Get key at: https://aistudio.google.com/app/apikey")
    print("Leave blank to use rule-based learning instead.\n")
    gemini_key = input("Gemini API key (or Enter to skip): ").strip()

    print("\n--- Supabase cloud sync (free tier, optional) ---")
    sb_url = input("Supabase URL (or Enter to skip): ").strip()
    sb_key = input("Supabase anon key (or Enter to skip): ").strip()

    os.makedirs("data", exist_ok=True)
    existing = {}
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update({
        "tz_api_key":    key,
        "tz_api_secret": secret,
        "tz_account_id": acct,
        "tz_env":        env,
        "gemini_api_key": gemini_key,
        "supabase_url":  sb_url,
        "supabase_key":  sb_key,
    })

    with open(MEMORY_FILE, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"\n✓ Keys saved to {MEMORY_FILE}")
    print(f"✓ Environment: {env.upper()}")
    if gemini_key:
        print("✓ Gemini AI learning enabled (free tier)")
    else:
        print("✓ Rule-based learning enabled (no API key needed)")
    if sb_url:
        print("✓ Supabase sync configured")
    print("\nRun the bot:  python3 main.py\n")

if __name__ == "__main__":
    setup()
