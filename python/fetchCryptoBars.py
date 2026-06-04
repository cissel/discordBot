#!/usr/bin/env python3
# fetchCryptoBars.py - fetch crypto bars from Alpaca API
# Usage: python3 fetchCryptoBars.py <SYMBOL> <TIMEFRAME>
# Symbols: BTC, ETH, DOGE
# Timeframes: intraday, 1w, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max

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

SYMBOL_MAP = {
    "BTC":  "BTC/USD",
    "ETH":  "ETH/USD",
    "SOL":  "SOL/USD",
    "DOGE": "DOGE/USD",
}

symbol_arg = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
timeframe  = sys.argv[2].lower() if len(sys.argv) > 2 else "6mo"

if symbol_arg not in SYMBOL_MAP:
    print(f"ERROR: Unknown symbol '{symbol_arg}'. Choose from: {', '.join(SYMBOL_MAP)}", file=sys.stderr)
    sys.exit(1)

symbol = SYMBOL_MAP[symbol_arg]
today  = date.today()

headers = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

TIMEFRAME_MAP = {
    "intraday": (today,                         today,  "1Min"),
    "1w":       (today - timedelta(weeks=1),    today,  "1Hour"),
    "1mo":      (today - timedelta(days=30),    today,  "1Day"),
    "3mo":      (today - timedelta(days=90),    today,  "1Day"),
    "6mo":      (today - timedelta(days=180),   today,  "1Day"),
    "1y":       (today - timedelta(days=365),   today,  "1Day"),
    "2y":       (today - timedelta(days=730),   today,  "1Day"),
    "5y":       (today - timedelta(days=1825),  today,  "1Day"),
    "10y":      (today - timedelta(days=3650),  today,  "1Day"),
    "max":      (date(2018, 1, 1),              today,  "1Day"),
}

# ── for intraday, crypto trades 24/7 so just use today ───────────────────────
# but if no data yet (very early UTC), fall back to yesterday
if timeframe == "intraday":
    test_resp = requests.get(
        "https://data.alpaca.markets/v1beta3/crypto/us/bars",
        headers=headers,
        params={"symbols": symbol, "timeframe": "1Min",
                "start": today.isoformat(), "limit": 1}
    )
    if test_resp.status_code == 200 and test_resp.json().get("bars", {}).get(symbol):
        TIMEFRAME_MAP["intraday"] = (today, today, "1Min")
    else:
        yesterday = today - timedelta(days=1)
        TIMEFRAME_MAP["intraday"] = (yesterday, yesterday, "1Min")

if timeframe not in TIMEFRAME_MAP:
    print(f"ERROR: Unknown timeframe '{timeframe}'. Choose from: {', '.join(TIMEFRAME_MAP)}", file=sys.stderr)
    sys.exit(1)

start_date, end_date, bar_tf = TIMEFRAME_MAP[timeframe]

# ── fetch bars ────────────────────────────────────────────────────────────────
url = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
params = {
    "symbols":   symbol,
    "timeframe": bar_tf,
    "start":     start_date.isoformat(),
    "end":       end_date.isoformat(),
    "limit":     10000,
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
    bars = data.get("bars", {}).get(symbol, [])
    all_bars.extend(bars)
    next_page = data.get("next_page_token")
    if not next_page:
        break

if not all_bars:
    print(f"ERROR: No bar data returned for {symbol} ({timeframe})", file=sys.stderr)
    sys.exit(1)

df = pd.DataFrame(all_bars)
df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
df["date"] = pd.to_datetime(df["date"])

if bar_tf == "1Min":
    df["date"] = df["date"].dt.tz_convert("America/New_York")
else:
    df["date"] = df["date"].dt.date

df = df.sort_values("date").reset_index(drop=True)

out_path = os.path.expanduser(f"~/discordBot/outputs/markets/{symbol_arg}_{timeframe}_bars.csv")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
df.to_csv(out_path, index=False)
print(f"Saved {len(df)} bars to {out_path}")