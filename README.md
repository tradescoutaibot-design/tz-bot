# TZ Autonomous Trading Bot

Autonomous equity trading bot connecting to TradeZero API.
Runs SMA Crossover, Connors RSI, and Opening Range Breakout strategies.
Learns from every trade and adjusts thresholds automatically.

---

## File structure

```
tz_bot/
├── main.py              ← run this to start the bot
├── setup.py             ← run this once to enter your keys
├── requirements.txt
├── core/
│   ├── agent.py         ← AI agent loop + learning engine
│   ├── api.py           ← TradeZero API wrapper
│   ├── memory.py        ← persistent memory (keys, history, learned rules)
│   ├── strategies.py    ← SMA, Connors RSI, ORB signal logic
│   └── logger.py        ← logging to console + logs/bot.log
├── data/
│   └── memory.json      ← created on first run — stores everything
└── logs/
    └── bot.log          ← full activity log
```

---

## Quick start (Option 3 — your own PC)

### Step 1 — Install Python
Download Python 3.11+ from https://python.org/downloads
During install: check "Add Python to PATH"

### Step 2 — Download this folder
Save the tz_bot/ folder anywhere on your PC, e.g.:
  C:\Users\YourName\tz_bot\      (Windows)
  ~/tz_bot/                       (Mac/Linux)

### Step 3 — Install dependencies
Open Terminal (Mac) or Command Prompt (Windows), then:

  cd path/to/tz_bot
  pip install -r requirements.txt

### Step 4 — Enter your TradeZero keys
  python setup.py

This saves your keys to data/memory.json — done once, remembered forever.

### Step 5 — Run the bot
  python main.py

The bot will:
- Connect to TradeZero (paper by default)
- Wait for market open (9:30 AM ET)
- Scan your watchlist for signals every 60 seconds
- Execute trades automatically
- Monitor stops and targets
- Learn from outcomes at end of day

Press Ctrl+C at any time to trigger the kill switch.

---

## Keeping the bot running all day (Windows)

Option A — Task Scheduler (built-in, free):
1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily at 9:00 AM
3. Action: Start a program
   Program: python
   Arguments: C:\path\to\tz_bot\main.py
4. Done — runs automatically on weekdays

Option B — Startup script (simpler):
Create a file called run_bot.bat:

  @echo off
  cd C:\Users\YourName\tz_bot
  python main.py
  pause

Double-click it to start. Keep the window open while trading.

## Keeping the bot running all day (Mac/Linux)

Option A — cron job:
  crontab -e
  # Add this line (runs at 9:00 AM Mon-Fri):
  0 9 * * 1-5 cd ~/tz_bot && python main.py >> logs/cron.log 2>&1

Option B — terminal:
  cd ~/tz_bot && python main.py

Keep Terminal open while market is open.

---

## Moving to Railway.app later (Option 2)

When you outgrow your PC (or want it running 24/7 in the cloud):

1. Push tz_bot/ to a GitHub repo (private)
2. Sign up at railway.app — free $5/mo credit
3. New Project → Deploy from GitHub → select your repo
4. Add environment variables (your TZ keys) in Railway dashboard
5. Railway builds and runs automatically — zero DevOps

Cost: ~$3-5/mo for a lightweight Python process.

---

## Memory & learning

Everything is saved in data/memory.json:
- Your TradeZero API keys (enter once via setup.py)
- Every trade with full details (entry, exit, P&L, strategy)
- Learned win rates, avg win/loss, best/worst tickers
- Auto-adjusted strategy thresholds (e.g. RSI entry tightens if win rate drops)
- Journal entries
- Backtest results

The agent reads this file at startup every day, so it carries all learning
forward into each new session automatically.

To back up your memory: copy data/memory.json anywhere safe.

---

## Kill switch

Ctrl+C in the terminal → immediate stop.
Cancels all open orders, clears position tracking, halts loop.

Or from the dashboard: click the red KILL SWITCH button.

---

## Supabase cloud sync (free)

To access your data from anywhere (phone, another PC):
1. Sign up at supabase.com — free tier (500MB)
2. Create a new project
3. Copy your Project URL and anon key
4. Run setup.py and enter them when prompted
   OR enter them in the dashboard → Settings → Supabase

Your trade history and memory then sync to the cloud automatically.

---

## Changing strategy

Edit data/memory.json and set "active_strategy" to one of:
- "SMA Crossover"
- "Connors RSI Mean Reversion"
- "Opening Range Breakout"

Or type a natural language instruction in the dashboard Agent panel:
  "Trade the next 3 days using Connors RSI, max 2 trades per day, stop loss 2%"

The agent parses the instruction, updates memory, and starts executing.

---

## Memory persistence — how nothing is ever lost

The bot uses 3 layers so your keys and learning always survive:

LAYER 1 — Environment variables (keys only)
  Your TradeZero, Gemini, and Supabase keys live here.
  On Railway: set them in the Variables tab — they survive every redeploy.
  On local PC: setup.py saves them to memory.json (fine for home use).

LAYER 2 — data/memory.json (all learned state)
  Every trade, journal entry, win rate, adjusted threshold saved here.
  On Railway: mount a persistent Volume at /data so it survives redeploys.
    Railway dashboard → your service → Volumes → Add → mount path: /data
  On local PC: just sits in the data/ folder, never wiped.

LAYER 3 — Supabase cloud backup (free, automatic)
  After every save, the full memory is synced to Supabase.
  If memory.json is ever wiped (Railway redeploy without volume),
  it restores automatically from Supabase on next startup.
  Zero data loss.

To set up the Supabase table (one time, 30 seconds):
  1. Go to supabase.com → your project → SQL Editor
  2. Paste and run this:

     create table bot_memory (
       id text primary key default 'main',
       data jsonb,
       updated_at timestamptz default now()
     );

  3. Done — the bot handles everything else automatically.

Railway environment variables to set:
  TZ_API_KEY       — your TradeZero API key
  TZ_API_SECRET    — your TradeZero API secret
  TZ_ACCOUNT_ID    — your TradeZero account ID
  TZ_ENV           — paper or live
  GEMINI_API_KEY   — your free Gemini key
  SUPABASE_URL     — your Supabase project URL
  SUPABASE_KEY     — your Supabase anon key
  DATA_DIR         — /data (where Railway mounts the volume)
