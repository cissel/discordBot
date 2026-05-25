#!/usr/bin/env python3
# fetchStockBars.py — fetch stock bars from Alpaca API
# Usage: python3 fetchStockBars.py <TICKER> <TIMEFRAME>
# Timeframes: 1d, 1w, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max, intraday

import os
import sys
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

if not API_KEY or not API_SECRET:
    print("ERROR: Alpaca API keys not found in .env", file=sys.stderr)
    sys.exit(1)

ticker    = sys.argv[1].upper() if len(sys.argv) > 1 else "SPY"
timeframe = sys.argv[2].lower() if len(sys.argv) > 2 else "6mo"

today = date.today()

headers = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# ── resolve date range and bar timeframe ──────────────────────────────────────
TIMEFRAME_MAP = {
    "intraday": (today,             today,              "1Min"),  # placeholder, fixed below
    "1w":       (today - timedelta(weeks=1),   today,  "1Hour"),
    "1mo":      (today - timedelta(days=30),   today,  "1Day"),
    "3mo":      (today - timedelta(days=90),   today,  "1Day"),
    "6mo":      (today - timedelta(days=180),  today,  "1Day"),
    "1y":       (today - timedelta(days=365),  today,  "1Day"),
    "2y":       (today - timedelta(days=730),  today,  "1Day"),
    "5y":       (today - timedelta(days=1825), today,  "1Day"),
    "10y":      (today - timedelta(days=3650), today,  "1Day"),
    "max":      (date(2000, 1, 1),             today,  "1Day"),
}

# ── for intraday, find the last market day if today is closed ─────────────────
if timeframe == "intraday":
    # check if today is a weekday and market is open
    # step back up to 7 days to find last trading day
    check_date = today
    for _ in range(7):
        if check_date.weekday() < 5:  # Mon-Fri
            # verify there's actual data for this day
            test_url = f"https://data.alpaca.markets/v2/stocks/SPY/bars"
            test_resp = requests.get(test_url, headers=headers, params={
                "timeframe": "1Min",
                "start": check_date.isoformat(),
                "end": check_date.isoformat(),
                "feed": "sip",
                "limit": 1,
            })
            if test_resp.status_code == 200 and test_resp.json().get("bars"):
                break
        check_date -= timedelta(days=1)
    TIMEFRAME_MAP["intraday"] = (check_date, check_date, "1Min")

if timeframe not in TIMEFRAME_MAP:
    print(f"ERROR: Unknown timeframe '{timeframe}'. Choose from: {', '.join(TIMEFRAME_MAP)}", file=sys.stderr)
    sys.exit(1)

start_date, end_date, bar_tf = TIMEFRAME_MAP[timeframe]

# ── fetch bars ────────────────────────────────────────────────────────────────
url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"

params = {
    "timeframe":  bar_tf,
    "start":      start_date.isoformat(),
    "end":        end_date.isoformat(),
    "adjustment": "all",
    "feed":       "sip",
    "limit":      10000,
}

all_bars = []
next_page = None

while True:
    if next_page:
        params["page_token"] = next_page
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        print(f"ERROR: Alpaca API returned {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    bars = data.get("bars", [])
    all_bars.extend(bars)
    next_page = data.get("next_page_token")
    if not next_page:
        break

if not all_bars:
    print(f"ERROR: No bar data returned for {ticker} ({timeframe})", file=sys.stderr)
    sys.exit(1)

df = pd.DataFrame(all_bars)
df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
df["date"] = pd.to_datetime(df["date"])

# for daily/weekly/monthly keep just the date, for intraday keep full timestamp
if bar_tf == "1Min":
    df["date"] = df["date"].dt.tz_convert("America/New_York")
else:
    df["date"] = df["date"].dt.date

df = df.sort_values("date").reset_index(drop=True)

out_path = os.path.expanduser(f"~/discordBot/outputs/markets/{ticker}_{timeframe}_bars.csv")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
df.to_csv(out_path, index=False)
print(f"Saved {len(df)} bars to {out_path}")