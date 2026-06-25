#!/usr/bin/env python3
"""
fetchCboeSkew.py
================
Fetches CBOE SKEW Index (^SKEW) and VIX9D (^VIX9D) via yfinance.
Saves to outputs/markets/cache/cboe_skew_daily.csv.

CBOE SKEW measures the perceived tail-risk of S&P 500 returns over a 30-day horizon.
Values typically 100-150+. High SKEW = market paying up for OTM put protection.
VIX9D = 9-day VIX (short-term fear gauge, complements VIX 30-day).

Columns saved:
  date, cboe_skew, vix9d
"""

import os
import sys
import pandas as pd
import yfinance as yf

BASE      = os.path.expanduser("~/discordBot")
CACHE_DIR = os.path.join(BASE, "outputs/markets/cache")
OUT_PATH  = os.path.join(CACHE_DIR, "cboe_skew_daily.csv")
START     = "2011-01-01"   # SKEW history starts ~2011 on yfinance

os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_series(ticker: str, col_name: str, start: str) -> pd.Series:
    df = yf.download(ticker, start=start, progress=False, auto_adjust=True)
    if df.empty:
        print(f"  [warn] {ticker}: no data returned")
        return pd.Series(dtype=float, name=col_name)
    # yfinance MultiIndex columns when downloading single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].rename(col_name)
    s.index = pd.to_datetime(s.index).normalize()
    s.index.name = "date"
    return s


def main():
    print("[fetchCboeSkew] fetching CBOE SKEW + VIX9D...")

    skew  = fetch_series("^SKEW",  "cboe_skew", START)
    vix9d = fetch_series("^VIX9D", "vix9d",     START)

    if skew.empty:
        print("  ERROR: SKEW fetch failed - aborting")
        sys.exit(1)

    out = pd.DataFrame({"cboe_skew": skew, "vix9d": vix9d})
    out = out.reset_index().rename(columns={"index": "date"})
    out = out.sort_values("date").reset_index(drop=True)

    # Drop rows where SKEW is missing (VIX9D may have slightly different calendar)
    out = out.dropna(subset=["cboe_skew"])

    out.to_csv(OUT_PATH, index=False)
    print(f"  rows: {len(out)}")
    print(f"  date range: {out.date.min().date()} to {out.date.max().date()}")
    skew_cov  = out.cboe_skew.notna().mean()
    vix9d_cov = out.vix9d.notna().mean()
    print(f"  cboe_skew coverage: {skew_cov*100:.1f}%")
    print(f"  vix9d coverage:     {vix9d_cov*100:.1f}%")
    print(f"  -> {OUT_PATH}")


if __name__ == "__main__":
    main()
