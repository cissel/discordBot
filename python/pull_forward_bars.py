"""
pull_forward_bars.py
--------------------
For each block event in block_events.csv, pulls 30 days of forward
5-minute OHLCV bar data and computes outcome variables:
  - Did price reach the block level within 1d, 3d, 1w, 2w, 1mo?
  - How far did price move toward/away from the block level?
  - Max drawdown toward and away from block price in the forward window

Saves results to block_outcomes.csv. Safe to re-run — only processes
events where forward_bars_pulled == False.
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
API_KEY    = "AKGNBG6FMQEWRBELM45U"
API_SECRET = "86eND4Pe8NJp4wNoBzkFGrS2PAvHo3UhOy4xAIlL"
BASE_URL   = "https://data.alpaca.markets/v2"

OUT_DIR      = "~/discordBot/outputs/research"
EVENTS_CSV   = os.path.join(OUT_DIR, "block_events.csv")
OUTCOMES_CSV = os.path.join(OUT_DIR, "block_outcomes.csv")

FORWARD_DAYS = 30   # how many calendar days forward to pull bars
BAR_TIMEFRAME = "5Min"

et = pytz.timezone("America/New_York")

# ── Helpers ───────────────────────────────────────────────────────────────────
def pull_bars(ticker, start_dt, end_dt):
    """Pull 5-minute bars between two datetimes."""
    start_utc = start_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = end_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
    all_bars   = []
    page_token = None

    while True:
        params = {
            "start":     start_utc,
            "end":       end_utc,
            "timeframe": BAR_TIMEFRAME,
            "limit":     10000,
            "feed":      "sip",
        }
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{BASE_URL}/stocks/{ticker}/bars",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        bars = data.get("bars", [])
        all_bars.extend(bars)

        page_token = data.get("next_page_token")
        if not page_token:
            break

        time.sleep(0.1)

    if not all_bars:
        return None

    df = pd.DataFrame(all_bars).rename(columns={
        "t": "time", "o": "open", "h": "high",
        "l": "low",  "c": "close", "v": "volume"
    })
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("America/New_York")
    df = df.sort_values("time").reset_index(drop=True)
    return df

def compute_outcomes(bars, block_price, block_time):
    """
    Given forward bars and the block print price/time, compute outcome variables.
    """
    if bars is None or bars.empty:
        return {}

    # Only look at bars AFTER the block print
    bars = bars[bars["time"] > block_time].copy()
    if bars.empty:
        return {}

    direction = 1 if block_price > bars["close"].iloc[0] else -1
    # direction > 0 means block was ABOVE market (bullish pull hypothesis)
    # direction < 0 means block was BELOW market (bearish pull hypothesis)

    def bars_within(days):
        cutoff = block_time + timedelta(days=days)
        # If cutoff is in the future, this window isn't available yet
        if cutoff > datetime.now(et):
            return pd.DataFrame()  # empty = not available
        return bars[bars["time"] <= cutoff]

    def reached_level(window_bars, target_price, direction):
        """Did price touch the block price level within this window?"""
        if window_bars.empty:
            return False
        if direction > 0:
            return window_bars["high"].max() >= target_price
        else:
            return window_bars["low"].min() <= target_price

    def pct_move_toward(window_bars, block_price, direction):
        """
        What % of the distance from entry price to block price
        did price travel in the right direction?
        Capped at 100% (reached or exceeded block level).
        """
        if window_bars.empty:
            return np.nan
        entry   = window_bars["close"].iloc[0]
        gap     = abs(block_price - entry)
        if gap == 0:
            return np.nan
        if direction > 0:
            best = window_bars["high"].max()
            moved = best - entry
        else:
            best = window_bars["low"].min()
            moved = entry - best
        return min(moved / gap, 1.0) * 100

    windows = {"1d": 1, "3d": 3, "1w": 7, "2w": 14, "1mo": 30}
    out = {
        "block_price":     block_price,
        "block_time":      str(block_time),
        "market_price":    bars["close"].iloc[0] if not bars.empty else np.nan,
        "direction":       "above_market" if direction > 0 else "below_market",
    }

    for label, days in windows.items():
        w = bars_within(days)
        out[f"reached_{label}"]    = reached_level(w, block_price, direction)
        out[f"pct_toward_{label}"] = pct_move_toward(w, block_price, direction)
        out[f"close_{label}"]      = w["close"].iloc[-1] if not w.empty else np.nan

    return out

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(EVENTS_CSV):
        print(f"Events file not found: {EVENTS_CSV}")
        print("Run pull_tape.py first.")
        exit(1)

    events = pd.read_csv(EVENTS_CSV)
    events["time"] = pd.to_datetime(events["time"], utc=True).dt.tz_convert("America/New_York")

    pending = events[events["forward_bars_pulled"] == False]
    print(f"Events to process: {len(pending):,} of {len(events):,} total\n")

    all_outcomes = []

    for idx, row in pending.iterrows():
        ticker      = row["ticker"]
        block_time  = row["time"]
        block_price = row["price"]

        print(f"[{idx+1}/{len(pending)}] {ticker} block at {block_time} @ ${block_price:.2f}...", end=" ", flush=True)

        try:
            fwd_start = block_time
            fwd_end   = block_time + timedelta(days=FORWARD_DAYS + 5)  # pad for weekends

            # Clamp end to now
            now_et = datetime.now(et)
            if fwd_end.replace(tzinfo=et) > now_et:
                fwd_end = now_et

            bars = pull_bars(ticker, fwd_start, fwd_end)

            outcomes = compute_outcomes(bars, block_price, block_time)

            if outcomes:
                outcomes["event_idx"]   = idx
                outcomes["ticker"]      = ticker
                outcomes["trade_date"]  = row["trade_date"]
                outcomes["size"]        = row["size"]
                outcomes["deviation"]   = row["deviation"]
                outcomes["dollar_value"] = row["dollar_value"]
                outcomes["exchange"]    = row["exchange"]
                all_outcomes.append(outcomes)
                print("done.")
            else:
                print("no forward bars available.")

            # Mark as processed in the events file
            events.at[idx, "forward_bars_pulled"] = True

        except Exception as e:
            print(f"ERROR: {e}")
            continue

        time.sleep(0.15)

    # Save outcomes
    if all_outcomes:
        outcomes_df = pd.DataFrame(all_outcomes)
        if os.path.exists(OUTCOMES_CSV):
            outcomes_df.to_csv(OUTCOMES_CSV, mode="a", header=False, index=False)
        else:
            outcomes_df.to_csv(OUTCOMES_CSV, index=False)
        print(f"\nSaved {len(all_outcomes)} outcomes to {OUTCOMES_CSV}")

    # Update the events file with processed flags
    events.to_csv(EVENTS_CSV, index=False)
    print("Updated events file.")