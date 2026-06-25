"""
recomputeBlockOutcomes.py
-------------------------
One-shot script: wipes block_outcomes.csv and recomputes all outcomes
fresh from block_events.csv using a single bar fetch covering the full
date range. Fixes dirty data from iterative bug-fix sessions.

Usage:
  python recomputeBlockOutcomes.py
"""

import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))
API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
BASE_URL   = "https://data.alpaca.markets/v2"

EVENTS_CSV   = os.path.expanduser("~/discordBot/outputs/research/block_events.csv")
OUTCOMES_CSV = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")

HORIZONS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1mo": 21}

OUTCOMES_COLS = [
    "block_price", "block_time", "market_price", "direction",
    "reached_1d",  "pct_toward_1d",  "close_1d",
    "reached_3d",  "pct_toward_3d",  "close_3d",
    "reached_1w",  "pct_toward_1w",  "close_1w",
    "reached_2w",  "pct_toward_2w",  "close_2w",
    "reached_1mo", "pct_toward_1mo", "close_1mo",
    "event_idx", "ticker", "trade_date", "size", "deviation", "dollar_value", "exchange",
]


def fetch_daily_bars(ticker: str, start: date, end: date) -> pd.DataFrame:
    url    = f"{BASE_URL}/stocks/{ticker}/bars"
    params = {
        "timeframe":  "1Day",
        "start":      str(start),
        "end":        str(end),
        "adjustment": "all",
        "feed":       "sip",
        "limit":      10000,
    }
    bars, page_token = [], None
    while True:
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        bars.extend(data.get("bars", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break
        time.sleep(0.1)

    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame(bars).rename(columns={
        "t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def compute_outcome(event_row: pd.Series, all_bars: pd.DataFrame) -> dict | None:
    trade_date  = pd.to_datetime(event_row["trade_date"]).date()
    block_price = float(event_row["price"])
    block_time  = str(event_row["time"])
    ticker      = str(event_row["ticker"])
    size        = int(event_row["size"])
    deviation   = float(event_row["deviation"])
    dollar_val  = float(event_row["dollar_value"])
    exchange    = str(event_row["exchange"])

    # Prior close = last bar BEFORE trade_date
    prior = all_bars[all_bars["date"] < trade_date]
    if prior.empty:
        print(f"    no prior bars for {trade_date}")
        return None
    market_price = float(prior["close"].iloc[-1])

    direction = "above_market" if block_price > market_price else "below_market"

    # Forward bars: STRICTLY AFTER trade_date
    forward = all_bars[all_bars["date"] > trade_date].reset_index(drop=True)

    out = {
        "block_price":  block_price,
        "block_time":   block_time,
        "market_price": market_price,
        "direction":    direction,
    }

    for label, n_days in HORIZONS.items():
        window = forward.iloc[:n_days]

        if len(window) < n_days:
            # Horizon hasn't elapsed yet
            out[f"reached_{label}"]    = False
            out[f"pct_toward_{label}"] = 0.0
            out[f"close_{label}"]      = float("nan")
            continue

        close_n = float(window["close"].iloc[-1])

        if direction == "above_market":
            reached  = float(window["high"].max()) >= block_price
            gap      = block_price - market_price
            best_up  = float(window["high"].max()) - market_price
            pct      = min(best_up / gap, 1.0) * 100.0 if gap != 0 else 0.0
        else:
            reached  = float(window["low"].min()) <= block_price
            gap      = market_price - block_price
            best_dn  = market_price - float(window["low"].min())
            pct      = min(best_dn / gap, 1.0) * 100.0 if gap != 0 else 0.0

        out[f"reached_{label}"]    = reached
        out[f"pct_toward_{label}"] = pct
        out[f"close_{label}"]      = close_n

    out.update({
        "event_idx":   int(event_row.name),
        "ticker":      ticker,
        "trade_date":  str(trade_date),
        "size":        size,
        "deviation":   deviation,
        "dollar_value": dollar_val,
        "exchange":    exchange,
    })

    return out


def main():
    if not os.path.exists(EVENTS_CSV):
        print("ERROR: block_events.csv not found")
        sys.exit(1)

    events = pd.read_csv(EVENTS_CSV)
    print(f"Loaded {len(events)} block events from {EVENTS_CSV}")

    # Fetch all bars in one shot: from 5 days before earliest event to today
    all_dates = pd.to_datetime(events["trade_date"]).dt.date
    fetch_start = min(all_dates) - timedelta(days=10)
    fetch_end   = date.today()

    print(f"Fetching SPY daily bars {fetch_start} -> {fetch_end}...")
    all_bars = fetch_daily_bars("SPY", fetch_start, fetch_end)
    print(f"  Got {len(all_bars)} bars")

    if all_bars.empty:
        print("ERROR: no bars returned")
        sys.exit(1)

    print(f"\nRecomputing outcomes for all {len(events)} events...")
    results = []
    for idx, row in events.iterrows():
        trade_date  = row["trade_date"]
        block_price = row["price"]
        print(f"  [{idx:>3}] {trade_date}  ${block_price:.4f}", end=" ... ", flush=True)

        outcome = compute_outcome(row, all_bars)
        if outcome is None:
            print("SKIP")
            continue

        results.append(outcome)
        reached_flags = [outcome.get(f"reached_{h}", False) for h in HORIZONS]
        any_reached   = any(reached_flags)
        n_filled      = sum(1 for h in HORIZONS if not np.isnan(outcome.get(f"close_{h}", float("nan"))))
        print(f"reached={any_reached}  horizons_filled={n_filled}/5")

    print(f"\nWriting {len(results)} outcome rows to {OUTCOMES_CSV}...")
    out_df = pd.DataFrame(results)[OUTCOMES_COLS]
    out_df.to_csv(OUTCOMES_CSV, index=False)
    print("Done.")

    # Summary
    reached_any = sum(
        any(str(r.get(f"reached_{h}", "")).lower() == "true" for h in HORIZONS)
        for r in results
    )
    print(f"\nSummary: {len(results)} total | {reached_any} reached gap at some horizon | {len(results)-reached_any} unfilled")


if __name__ == "__main__":
    main()
