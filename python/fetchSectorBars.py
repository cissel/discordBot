#!/usr/bin/env python3
"""
fetchSectorBars.py
==================
Fetches historical daily bars for the 11 SPDR sector ETFs via Alpaca.
Computes daily returns and saves a wide-format CSV for use in SPY model.

Sectors: XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY

Output:
  outputs/markets/cache/sector_bars_historical.csv
  columns: date, XLK_ret, XLF_ret, XLE_ret, ... (one col per sector)

Run once to backfill, then included in 5am daily cron.
"""

import os, sys, time, datetime, requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
BASE_URL   = "https://data.alpaca.markets/v2/stocks/bars"

OUT_PATH = os.path.expanduser("~/discordBot/outputs/markets/cache/sector_bars_historical.csv")

SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]

START_DATE = "2016-01-01"


def fetch_bars(symbol, start, end):
    params = {
        "symbols":    symbol,
        "timeframe":  "1Day",
        "start":      start,
        "end":        end,
        "limit":      10000,
        "adjustment": "all",
        "feed":       "iex",
    }
    all_bars = []
    url = BASE_URL
    while url:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        bars = data.get("bars", {}).get(symbol, [])
        all_bars.extend(bars)
        next_token = data.get("next_page_token")
        if next_token:
            params = {"page_token": next_token}
        else:
            url = None
    return all_bars


def main():
    end = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    # Load existing to avoid re-fetching
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH, parse_dates=["date"])
        last_date = existing["date"].max().date()
        start = (last_date + datetime.timedelta(days=1)).isoformat()
        print(f"[fetchSectorBars] existing: {len(existing)} rows to {last_date}, fetching {start} to {end}")
    else:
        existing = pd.DataFrame()
        start = START_DATE
        print(f"[fetchSectorBars] backfill from {start} to {end}")

    if start > end:
        print("  already up to date")
        return

    frames = {}
    for sym in SECTORS:
        print(f"  fetching {sym}...", end=" ", flush=True)
        try:
            bars = fetch_bars(sym, start, end)
            if bars:
                df = pd.DataFrame(bars)
                df["date"] = pd.to_datetime(df["t"]).dt.date.astype(str)
                df["close"] = df["c"].astype(float)
                df = df[["date","close"]].sort_values("date")
                df[f"{sym}_ret"] = df["close"].pct_change()
                frames[sym] = df.set_index("date")[f"{sym}_ret"]
                print(f"{len(df)} bars")
            else:
                print("no data")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.3)

    if not frames:
        print("  nothing fetched")
        return

    new_df = pd.DataFrame(frames).reset_index().rename(columns={"index":"date"})
    new_df["date"] = pd.to_datetime(new_df["date"])

    if not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates("date").sort_values("date")
    else:
        combined = new_df.sort_values("date")

    combined.to_csv(OUT_PATH, index=False)
    print(f"[fetchSectorBars] wrote {len(combined)} rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
