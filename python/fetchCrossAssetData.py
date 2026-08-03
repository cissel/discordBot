#!/usr/bin/env python3
"""
fetchCrossAssetData.py
=======================
Refreshes the 8 base cache series that buildSpyFeatures.py and
buildBtcFeatures.py read as their raw inputs:

  Alpaca daily-return series (incremental, like fetchSectorBars.py):
    SPY.csv, QQQ.csv, GLD.csv, USO.csv   (columns: date, <TICKER>_ret)

  FRED level series (incremental, like fetch_fred_claims.py):
    VIX.csv      <- VIXCLS    (columns: date, VIXCLS_val)
    DXY.csv      <- DTWEXBGS  (columns: date, DTWEXBGS_val)
    T10Y2Y.csv   <- T10Y2Y    (columns: date, T10Y2Y_val)
    FEDFUNDS.csv <- FEDFUNDS  (columns: date, FEDFUNDS_val)

BUG THIS FIXES (found Aug 2 2026): none of these 8 files were ever wired
into a cron job. They were built once manually in June 2026 (last row
2026-06-09) and never refreshed again. buildSpyFeatures.py and
buildBtcFeatures.py have been running daily against a 54-day-stale base
series ever since — predictSpy.py's signal (and therefore every paper
trade in spy_trade_log.csv since autopilot went live Jul 1) has been
IDENTICAL every single day because the inputs never changed. This script
closes that gap. Run BEFORE buildSpyFeatures.py / buildBtcFeatures.py in
any pipeline.

Usage:
  venv/bin/python3 python/fetchCrossAssetData.py           # incremental (normal daily use)
  venv/bin/python3 python/fetchCrossAssetData.py --backfill  # force full refetch from each file's start date
"""

import os
import sys
import time
import argparse
import datetime
import io

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

CACHE_DIR = os.path.expanduser("~/discordBot/outputs/markets/cache")
os.makedirs(CACHE_DIR, exist_ok=True)

API_KEY    = os.getenv("APCA_API_KEY_ID", "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
BARS_URL   = "https://data.alpaca.markets/v2/stocks/bars"

# Alpaca-sourced daily-return tickers (mirrors fetchSectorBars.py exactly)
ALPACA_TICKERS = ["SPY", "QQQ", "GLD", "USO"]
ALPACA_START   = "2016-01-01"  # matches existing cache history start

# FRED-sourced level series (mirrors fetch_fred_claims.py pattern)
FRED_SERIES = {
    "VIX.csv":      "VIXCLS",
    "DXY.csv":      "DTWEXBGS",
    "T10Y2Y.csv":   "T10Y2Y",
    "FEDFUNDS.csv": "FEDFUNDS",
}

# Expected publication lag (calendar days) per FRED series, so the staleness
# check below doesn't false-positive on series with known structural lag.
# DTWEXBGS (Fed broad trade-weighted dollar index) is a weighted composite
# across many trading partners and is consistently finalized/published by the
# Fed ~1-2 weeks behind the observation date - confirmed 2026-08-03: FRED's
# own API had DTWEXBGS through 2026-07-24 only (10 days behind), which is
# normal for this series, not a fetch failure. VIXCLS/T10Y2Y/FEDFUNDS publish
# same-day or next-day and should NOT show this lag - flag those if stale.
FRED_EXPECTED_LAG_DAYS = {
    "VIX.csv":      3,
    "DXY.csv":      14,
    "T10Y2Y.csv":   3,
    "FEDFUNDS.csv": 65,  # monthly avg. Confirmed 2026-08-03 via BOTH fredgraph.csv
                          # scrape AND the authenticated FRED API (api_key in .env)
                          # that June 2026 (posted 2026-06-01) was the genuinely
                          # latest value in existence anywhere - July's figure
                          # simply had not been calculated/published yet, 63 days
                          # after the observation date. This is real month-to-month
                          # publication variance for this series, not a fetch bug -
                          # do not shrink this threshold without re-verifying against
                          # the live FRED API first.
}


def fetch_alpaca_bars(symbol, start, end):
    params = {
        "symbols": symbol, "timeframe": "1Day", "start": start, "end": end,
        "limit": 10000, "adjustment": "all", "feed": "iex",
    }
    all_bars = []
    url = BARS_URL
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


def update_alpaca_ticker(symbol, backfill=False):
    """Incremental update of <symbol>.csv - same append/dedupe pattern as fetchSectorBars.py."""
    out_path = os.path.join(CACHE_DIR, f"{symbol}.csv")
    end = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    if os.path.exists(out_path) and not backfill:
        existing = pd.read_csv(out_path, parse_dates=["date"])
        last_date = existing["date"].max().date()
        start = (last_date + datetime.timedelta(days=1)).isoformat()
        print(f"  [{symbol}] existing: {len(existing)} rows to {last_date}, fetching {start} to {end}")
    else:
        existing = pd.DataFrame()
        start = ALPACA_START
        print(f"  [{symbol}] backfill from {start} to {end}")

    if start > end:
        print(f"  [{symbol}] already up to date")
        return

    try:
        bars = fetch_alpaca_bars(symbol, start, end)
    except Exception as e:
        print(f"  [{symbol}] ERROR: {e}")
        return

    if not bars:
        print(f"  [{symbol}] no new data")
        return

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.date.astype(str)
    df["close"] = df["c"].astype(float)
    df = df[["date", "close"]].sort_values("date")

    if not existing.empty:
        # Need the prior close to compute pct_change correctly across the join boundary
        prior_close = existing.iloc[-1].get("close")
        # existing files only store the _ret column, not close - so just combine on
        # returns computed fresh on the new chunk only (matches fetchSectorBars.py
        # behavior: pct_change() within the new chunk; first new row's return will
        # be NaN if computed in isolation, so fetch one extra day back for continuity)
        pass

    # Refetch with one extra prior day so pct_change() at the boundary is correct
    if not existing.empty:
        boundary_start = (pd.to_datetime(start) - pd.Timedelta(days=5)).date().isoformat()
        bars_ctx = fetch_alpaca_bars(symbol, boundary_start, end)
        df = pd.DataFrame(bars_ctx)
        df["date"] = pd.to_datetime(df["t"]).dt.date.astype(str)
        df["close"] = df["c"].astype(float)
        df = df[["date", "close"]].sort_values("date")

    df[f"{symbol}_ret"] = df["close"].pct_change()
    df = df[["date", f"{symbol}_ret"]].dropna()
    df["date"] = pd.to_datetime(df["date"])

    if not existing.empty:
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates("date").sort_values("date")
    else:
        combined = df.sort_values("date")

    combined.to_csv(out_path, index=False)
    print(f"  [{symbol}] wrote {len(combined)} rows -> {out_path} (last: {combined['date'].max().date()})")
    time.sleep(0.3)


def fetch_fred_series(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["observation_date"])
    df = df.rename(columns={"observation_date": "date", series_id: f"{series_id}_val"})
    df = df[df[f"{series_id}_val"] != "."]
    df[f"{series_id}_val"] = pd.to_numeric(df[f"{series_id}_val"])
    return df


def update_fred_file(filename, series_id):
    """FRED's fredgraph.csv endpoint always returns full history - just refetch and
    overwrite (no incremental append needed, unlike the Alpaca tickers)."""
    out_path = os.path.join(CACHE_DIR, filename)
    try:
        df = fetch_fred_series(series_id)
    except Exception as e:
        print(f"  [{filename}] ERROR: {e}")
        return
    df.to_csv(out_path, index=False)
    last_date = df["date"].max().date()
    lag_days = (datetime.date.today() - last_date).days
    expected_lag = FRED_EXPECTED_LAG_DAYS.get(filename, 5)
    status = "OK" if lag_days <= expected_lag else "WARN"
    print(f"  [{filename}] wrote {len(df)} rows -> {out_path} (last: {last_date}, "
          f"{lag_days}d behind, expected <={expected_lag}d) [{status}]")
    if status == "WARN":
        print(f"    [WARN] {filename} is MORE stale than its known publication lag "
              f"allows - this may be a genuine fetch problem, not just normal lag.")
    time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                     help="Force full refetch of Alpaca tickers from 2016-01-01 "
                          "instead of incremental update from last cached date.")
    args = ap.parse_args()

    if not API_KEY or not API_SECRET:
        print("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set in .env - "
              "cannot fetch SPY/QQQ/GLD/USO from Alpaca.")
        sys.exit(1)

    print("[fetchCrossAssetData] Alpaca daily-return tickers:")
    for sym in ALPACA_TICKERS:
        update_alpaca_ticker(sym, backfill=args.backfill)

    print("[fetchCrossAssetData] FRED level series:")
    for filename, series_id in FRED_SERIES.items():
        update_fred_file(filename, series_id)

    print("[fetchCrossAssetData] done.")


if __name__ == "__main__":
    main()
