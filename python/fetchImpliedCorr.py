#!/usr/bin/env python3
"""
fetchImpliedCorr.py
====================
Fetches Cboe Implied Correlation indices (COR1M, COR3M) - free historical
CSVs served from Cboe's CDN (same mechanism that feeds the public index
dashboards at cboe.com/us/indices/dashboard/cor1m/, cor3m/). Daily since
2006-01-03.

COR1M = Cboe 1-Month Implied Correlation Index
COR3M = Cboe 3-Month Implied Correlation Index (the one Cboe's own docs
        describe as "the" Implied Correlation Index - ATM delta constant
        maturity SPX vs avg top-50 single-name implied vol spread)

Output: outputs/markets/cache/implied_corr_daily.csv
  columns: date, cor1m, cor3m   (close-of-day values, both indices)

Run: /home/jhcv/discordBot/venv/bin/python3 python/fetchImpliedCorr.py
"""
import os
import sys
import pandas as pd
import requests

BASE = os.path.expanduser("~/discordBot")
CACHE_DIR = os.path.join(BASE, "outputs/markets/cache")
OUT_PATH = os.path.join(CACHE_DIR, "implied_corr_daily.csv")

URLS = {
    "cor1m": "https://cdn.cboe.com/api/global/us_indices/daily_prices/COR1M_History.csv",
    "cor3m": "https://cdn.cboe.com/api/global/us_indices/daily_prices/COR3M_History.csv",
}
HEADERS = {"User-Agent": "Mozilla/5.0"}

os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_one(name, url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y")
    s = df.set_index("date")["close"].rename(name)
    return s


def main():
    print("[fetchImpliedCorr] fetching COR1M + COR3M from Cboe CDN...")
    series = {}
    for name, url in URLS.items():
        try:
            s = fetch_one(name, url)
            series[name] = s
            print(f"  {name}: {len(s)} rows, {s.index.min().date()} to {s.index.max().date()}")
        except Exception as e:
            print(f"  ERROR fetching {name}: {e}")

    if not series:
        print("  nothing fetched - aborting")
        sys.exit(1)

    out = pd.DataFrame(series).reset_index().rename(columns={"index": "date"})
    out = out.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    out.to_csv(OUT_PATH, index=False)
    print(f"[fetchImpliedCorr] wrote {len(out)} rows -> {OUT_PATH}")
    last = out.iloc[-1]
    print(f"  last row: {last['date'].date()} cor1m={last.get('cor1m')} cor3m={last.get('cor3m')}")


if __name__ == "__main__":
    main()
