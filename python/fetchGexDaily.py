#!/usr/bin/env python3
"""
fetchGexDaily.py
================
Downloads the SqueezeMetrics DIX/GEX CSV for SPY/SPX and caches it locally.

Source : https://squeezemetrics.com/monitor/static/DIX.csv
Format : date, price (SPX level), dix, gex
History: 2011-05-02 to present (~3800 rows, updated daily)

Output : outputs/markets/cache/spy_gex_daily.csv
Columns: date, price, dix, gex

GEX note:
  gex is in raw dollar terms (~$1B-$24B range for SPX).
  Negative GEX (<0) = dealers net short gamma -> destabilizing regime.
  Positive GEX     = dealers net long gamma  -> volatility suppression.
  Empirical check (2011-2026): gex vs |next_day_SPY_ret| Pearson = -0.28
  (high GEX -> lower next-day realized vol). Directional: near-zero (-0.02).

Usage:
  venv/bin/python3 python/fetchGexDaily.py          # incremental update
  venv/bin/python3 python/fetchGexDaily.py --full   # force full re-download
"""

import os
import sys
import datetime
import requests
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR  = os.path.expanduser("~/discordBot")
OUT_PATH  = os.path.join(BASE_DIR, "outputs", "markets", "cache", "spy_gex_daily.csv")
SOURCE_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer":    "https://squeezemetrics.com/monitor/dix",
}
TIMEOUT    = 30   # seconds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_remote() -> pd.DataFrame:
    """Download the full DIX/GEX CSV from SqueezeMetrics."""
    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(resp.text), parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[["date", "price", "dix", "gex"]]


def load_cached() -> pd.DataFrame | None:
    """Load existing cache if present; return None if missing."""
    if not os.path.exists(OUT_PATH):
        return None
    return pd.read_csv(OUT_PATH)


def is_stale(cached: pd.DataFrame) -> bool:
    """Return True if cache is missing today's (or yesterday's) row."""
    if cached is None or cached.empty:
        return True
    last_date = pd.to_datetime(cached["date"].max()).date()
    # SqueezeMetrics updates EOD; consider fresh if last row is within 2 calendar days
    today = datetime.date.today()
    return (today - last_date).days > 2


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    force_full = "--full" in sys.argv

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    cached = load_cached()

    if not force_full and cached is not None and not is_stale(cached):
        last = cached["date"].max()
        print(f"[fetchGexDaily] cache is fresh (last row: {last}) -- nothing to do")
        return

    print(f"[fetchGexDaily] downloading from SqueezeMetrics...")
    remote = fetch_remote()
    print(f"  remote rows: {len(remote)}  "
          f"({remote['date'].min()} to {remote['date'].max()})")

    if not force_full and cached is not None and not cached.empty:
        # Merge: keep all remote rows (remote is authoritative)
        combined = (
            pd.concat([cached, remote], ignore_index=True)
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        combined = remote

    combined.to_csv(OUT_PATH, index=False)
    print(f"  saved {len(combined)} rows -> {OUT_PATH}")
    print(f"  gex range: {combined['gex'].min()/1e9:.2f}B to {combined['gex'].max()/1e9:.2f}B")
    print(f"  negative gex days: {(combined['gex'] < 0).sum()} "
          f"({(combined['gex'] < 0).mean()*100:.1f}%)")


if __name__ == "__main__":
    main()
