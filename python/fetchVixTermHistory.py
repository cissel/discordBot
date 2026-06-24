#!/usr/bin/env python3
"""
fetchVixTermHistory.py
Fetches and caches daily historical data for:
  - ^VVIX  (vol-of-vol)
  - ^VIX9D (9-day VIX)
  - ^VIX3M (3-month VIX)
  - ^VIX6M (6-month VIX)

Output: outputs/markets/cache/vix_term_history.csv
Columns: date, vvix, vix9d, vix3m, vix6m

Incremental: only fetches dates after last row in cache.
Run daily in 5am cron before buildSpyFeatures.py.
"""

import os
import sys
import time
import requests
import pandas as pd
import datetime
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "outputs" / "markets" / "cache"
OUT_PATH  = CACHE_DIR / "vix_term_history.csv"

SYMBOLS = {
    "vvix":  "^VVIX",
    "vix9d": "^VIX9D",
    "vix3m": "^VIX3M",
    "vix6m": "^VIX6M",
}

HEADERS = {"User-Agent": "Mozilla/5.0"}
SLEEP_S = 1.5  # polite delay between Yahoo requests


# ── Yahoo Finance fetch ────────────────────────────────────────────────────────
def fetch_yf_history(symbol: str, start_date: datetime.date | None = None) -> pd.DataFrame:
    """
    Fetch daily closes for a Yahoo Finance symbol.
    If start_date is provided, fetches only from that date onward (uses 10y range
    and filters client-side — Yahoo doesn't support arbitrary start on the v8 chart API).
    Returns DataFrame with columns: date, close.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=10y"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [WARN] fetch failed for {symbol}: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["date", "close"])

    result = data.get("chart", {}).get("result", [])
    if not result:
        err = data.get("chart", {}).get("error")
        print(f"  [WARN] no data for {symbol}: {err}", file=sys.stderr)
        return pd.DataFrame(columns=["date", "close"])

    timestamps = result[0].get("timestamp", [])
    closes     = result[0]["indicators"]["quote"][0].get("close", [])

    rows = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        d = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).date()
        rows.append({"date": d, "close": round(c, 4)})

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if start_date is not None:
        cutoff = pd.Timestamp(start_date)
        df = df[df["date"] > cutoff].reset_index(drop=True)

    return df


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing cache
    if OUT_PATH.exists():
        existing = pd.read_csv(OUT_PATH, parse_dates=["date"])
        last_date = existing["date"].max()
        print(f"Cache exists: {len(existing)} rows, last={last_date.date()}")
    else:
        existing  = pd.DataFrame(columns=["date", "vvix", "vix9d", "vix3m", "vix6m"])
        last_date = None
        print("No cache found, doing full backfill (10yr).")

    # Check if already up to date (last trading day)
    today = datetime.date.today()
    most_recent_weekday = today if today.weekday() < 5 else (
        today - datetime.timedelta(days=today.weekday() - 4)
    )
    if last_date is not None and last_date.date() >= most_recent_weekday:
        print("Cache already up to date.")
        return

    # Fetch each symbol
    frames = {}
    for col, sym in SYMBOLS.items():
        print(f"Fetching {sym} ({col})...")
        df = fetch_yf_history(sym, start_date=last_date)
        if df.empty:
            print(f"  [WARN] got 0 rows for {sym}")
        else:
            print(f"  {len(df)} new rows")
        frames[col] = df.rename(columns={"close": col}).set_index("date")
        time.sleep(SLEEP_S)

    if not any(not f.empty for f in frames.values()):
        print("No new data fetched.")
        return

    # Join all symbols on date
    new_data = None
    for col, df in frames.items():
        if df.empty:
            continue
        if new_data is None:
            new_data = df
        else:
            new_data = new_data.join(df, how="outer")

    if new_data is None or new_data.empty:
        print("No new rows to add.")
        return

    new_data = new_data.reset_index()

    # Append to existing and deduplicate
    # Cast new columns to match existing dtypes to avoid FutureWarning on concat
    for col in existing.columns:
        if col in new_data.columns and col != "date" and existing[col].dtype != object:
            try:
                new_data[col] = new_data[col].astype(existing[col].dtype)
            except (ValueError, TypeError):
                pass
    combined = pd.concat([existing, new_data], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (
        combined
        .sort_values("date")
        .drop_duplicates(subset="date", keep="last")
        .reset_index(drop=True)
    )

    # Drop rows where ALL vol columns are NaN
    vol_cols = list(SYMBOLS.keys())
    combined = combined.dropna(subset=vol_cols, how="all").reset_index(drop=True)

    combined.to_csv(OUT_PATH, index=False)
    print(f"Saved {len(combined)} rows to {OUT_PATH}")
    print(f"  Date range: {combined['date'].min().date()} -> {combined['date'].max().date()}")

    # Quick sanity check
    for col in vol_cols:
        pct = combined[col].notna().mean() * 100
        print(f"  {col}: {pct:.1f}% coverage ({combined[col].notna().sum()} rows)")


if __name__ == "__main__":
    main()
