"""
pull_tape.py
------------
Pulls the full trade tape for SPY one trading day at a time going back 1 month.
For each day, identifies block prints defined as trades that are BOTH:
  - Large in size  (>= MIN_SIZE shares)
  - Far from the rolling median price (>= MIN_DEVIATION_PCT %)
Appends flagged events to a running CSV. Safe to re-run — skips days already processed.
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import pandas_market_calendars as mcal
import os
import time

# ── Configuration ─────────────────────────────────────────────────────────────
TICKER          = "SPY"
MIN_SIZE        = 500000       # minimum shares to qualify as a block
MIN_DEV_PCT     = 0.005       # minimum price deviation from rolling median (0.5%)
ROLLING_WINDOW  = 51          # rolling median window (number of trades)
LOOKBACK_DAYS   = 300          # how many calendar days back to start

API_KEY         = "AKGNBG6FMQEWRBELM45U"
API_SECRET      = "86eND4Pe8NJp4wNoBzkFGrS2PAvHo3UhOy4xAIlL"
BASE_URL        = "https://data.alpaca.markets/v2"

OUT_DIR         = "~/discordBot/outputs/research"
EVENTS_CSV      = os.path.join(OUT_DIR, "block_events.csv")
PROGRESS_CSV    = os.path.join(OUT_DIR, "processed_days.csv")

os.makedirs(OUT_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
et = pytz.timezone("America/New_York")

def get_trading_days(n_days_back):
    nyse  = mcal.get_calendar("NYSE")
    end   = datetime.now(et).date()
    start = end - timedelta(days=n_days_back + 14)  # pad for weekends/holidays
    sched = nyse.schedule(start_date=str(start), end_date=str(end))
    return [d.date() for d in sched.index][-n_days_back:]

def already_processed(day):
    if not os.path.exists(PROGRESS_CSV):
        return False
    done = pd.read_csv(PROGRESS_CSV)
    return str(day) in done["date"].astype(str).values

def mark_processed(day):
    row = pd.DataFrame([{"date": str(day)}])
    if os.path.exists(PROGRESS_CSV):
        row.to_csv(PROGRESS_CSV, mode="a", header=False, index=False)
    else:
        row.to_csv(PROGRESS_CSV, index=False)

def pull_trades_for_day(ticker, day):
    """Pull all trades for a single trading day (4 AM - 8 PM ET)."""
    start = et.localize(datetime(day.year, day.month, day.day, 4, 0, 0))
    end   = et.localize(datetime(day.year, day.month, day.day, 20, 0, 0))

    start_utc = start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
    all_trades = []
    page_token = None

    while True:
        params = {"start": start_utc, "end": end_utc, "limit": 10000, "feed": "sip"}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{BASE_URL}/stocks/{ticker}/trades",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        trades = data.get("trades", [])
        all_trades.extend(trades)

        page_token = data.get("next_page_token")
        if not page_token:
            break

        time.sleep(0.1)  # be polite to the API

    if not all_trades:
        return None

    df = pd.DataFrame(all_trades).rename(columns={
        "t": "time", "p": "price", "s": "size",
        "x": "exchange", "c": "conditions", "z": "tape"
    })
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("America/New_York")
    df = df.sort_values("time").reset_index(drop=True)
    return df

def flag_blocks(df):
    df = df.copy()
    df["rolling_med"] = df["price"].rolling(
        window=ROLLING_WINDOW, center=True, min_periods=10
    ).median()
    df["deviation"]    = (df["price"] - df["rolling_med"]).abs() / df["rolling_med"]
    df["dollar_value"] = df["price"] * df["size"]

    blocks = df[
        (df["size"] >= MIN_SIZE) &
        (df["deviation"] >= MIN_DEV_PCT)
    ].copy()

    # Collapse events within 60 seconds of each other into one
    # keeping the largest by dollar value
    if blocks.empty:
        return blocks
    blocks = blocks.sort_values("time")
    blocks["group"] = (blocks["time"].diff().dt.total_seconds() > 60).cumsum()
    blocks = blocks.loc[blocks.groupby("group")["dollar_value"].idxmax()]
    blocks = blocks.drop(columns="group").reset_index(drop=True)

    return blocks

def append_events(blocks, day, ticker):
    if blocks.empty:
        return

    out = blocks[[
        "time", "price", "size", "deviation", "dollar_value", "exchange"
    ]].copy()
    out.insert(0, "ticker", ticker)
    out.insert(1, "trade_date", str(day))
    out["forward_bars_pulled"] = False  # flag for script 2

    if os.path.exists(EVENTS_CSV):
        out.to_csv(EVENTS_CSV, mode="a", header=False, index=False)
    else:
        out.to_csv(EVENTS_CSV, index=False)

    print(f"  Appended {len(out)} block events.")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    trading_days = get_trading_days(LOOKBACK_DAYS)
    print(f"Processing {len(trading_days)} trading days for {TICKER}...")
    print(f"Block criteria: size >= {MIN_SIZE:,} shares AND deviation >= {MIN_DEV_PCT*100:.1f}%\n")

    for day in trading_days:
        if already_processed(day):
            print(f"[SKIP] {day} already processed.")
            continue

        print(f"[PULL] {day}...", end=" ", flush=True)

        try:
            df = pull_trades_for_day(TICKER, day)

            if df is None or df.empty:
                print("no trades returned.")
                mark_processed(day)
                continue

            print(f"{len(df):,} trades fetched.", end=" ", flush=True)

            blocks = flag_blocks(df)
            append_events(blocks, day, TICKER)
            mark_processed(day)

        except Exception as e:
            print(f"\n  ERROR on {day}: {e}")
            continue

    print("\nDone.")
    if os.path.exists(EVENTS_CSV):
        events = pd.read_csv(EVENTS_CSV)
        print(f"Total block events collected: {len(events):,}")
        print(f"Saved to: {EVENTS_CSV}")