#!/usr/bin/env python3
"""
fetchCotSp500.py
================
Downloads CFTC "Traders in Financial Futures" (TFF) weekly Commitment of
Traders data for CME S&P 500 Index futures, via the free Office of Financial
Research (OFR) Hedge Fund Monitor mirror (no API key, full history, clean CSV).

Source: https://data.financialresearch.gov/hf/v1/series/dataset?dataset=tff&output=csv
        (full TFF dataset, wide format: one row per as-of Tuesday, one column
        per series across ALL futures contracts the TFF report covers - we
        select only the S&P 500 columns)

Series pulled (only S&P 500 columns present in the OFR mirror - the TFF
report's full Asset-Manager/Dealer/Other-Reportable breakdown is NOT in this
mirror, only Leveraged Funds):
  TFF-LF_SP_LONG_POSITION   - Leveraged funds long notional ($)
  TFF-LF_SP_NET_POSITION    - Leveraged funds net notional ($)
  TFF-LF_SP_SHORT_POSITION  - Leveraged funds short notional ($)

Leveraged funds (hedge funds / CTAs / managed futures) is the closest TFF
analog to the Legacy report's "non-commercial / large speculator" category
that the COT literature (CXO Advisory, Wang 2000) actually studied.

Report timing / lookahead: as-of date is always a Tuesday; CFTC publicly
releases it the following Friday ~3:30pm ET. We store BOTH as_of_date (the
Tuesday data date) and release_date (as_of_date + 3 calendar days, the
Friday) so downstream studies can lag correctly and avoid using the signal
before it was public.

Output : outputs/markets/cache/cot_sp500.csv
Columns: as_of_date, release_date, lf_long, lf_net, lf_short, net_ratio
  net_ratio = lf_net / (lf_long + lf_short)  (bounded -1..1, removes most of
  the index-price-level scaling in the raw notional-dollar figures)

Usage:
  venv/bin/python3 python/fetchCotSp500.py          # incremental update
  venv/bin/python3 python/fetchCotSp500.py --full   # force full re-download
"""

import os
import sys
import datetime
import requests
import pandas as pd
from io import StringIO

BASE_DIR = os.path.expanduser("~/discordBot")
OUT_PATH = os.path.join(BASE_DIR, "outputs", "markets", "cache", "cot_sp500.csv")
SOURCE_URL = "https://data.financialresearch.gov/hf/v1/series/dataset?dataset=tff&output=csv"
TIMEOUT = 60

COLS = {
    "TFF-LF_SP_LONG_POSITION": "lf_long",
    "TFF-LF_SP_NET_POSITION": "lf_net",
    "TFF-LF_SP_SHORT_POSITION": "lf_short",
}


def fetch_remote() -> pd.DataFrame:
    resp = requests.get(SOURCE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    raw = list(pd.read_csv(StringIO(resp.text), header=None, dtype=str).itertuples(index=False, name=None))
    header_row = raw[3]  # row index 3 = 'date', 'TFF-...' column names
    data_rows = raw[4:]
    df = pd.DataFrame(data_rows, columns=header_row)
    needed = ["date"] + list(COLS.keys())
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(f"OFR TFF CSV missing expected columns: {missing}")
    df = df[needed].rename(columns={"date": "as_of_date", **COLS})
    df["as_of_date"] = pd.to_datetime(df["as_of_date"])
    for c in ["lf_long", "lf_net", "lf_short"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["lf_long", "lf_net", "lf_short"]).sort_values("as_of_date").reset_index(drop=True)
    df["release_date"] = df["as_of_date"] + pd.Timedelta(days=3)  # Tuesday as-of -> Friday release
    df["net_ratio"] = df["lf_net"] / (df["lf_long"] + df["lf_short"])
    df["as_of_date"] = df["as_of_date"].dt.strftime("%Y-%m-%d")
    df["release_date"] = df["release_date"].dt.strftime("%Y-%m-%d")
    return df[["as_of_date", "release_date", "lf_long", "lf_net", "lf_short", "net_ratio"]]


def load_cached():
    if not os.path.exists(OUT_PATH):
        return None
    return pd.read_csv(OUT_PATH)


def is_stale(cached) -> bool:
    if cached is None or cached.empty:
        return True
    last = pd.to_datetime(cached["as_of_date"].max()).date()
    today = datetime.date.today()
    return (today - last).days > 9  # weekly series; refetch if more than ~1 report stale


def main():
    force_full = "--full" in sys.argv
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    cached = load_cached()

    if not force_full and cached is not None and not is_stale(cached):
        print(f"[fetchCotSp500] cache fresh (last as_of_date: {cached['as_of_date'].max()}) -- nothing to do")
        return

    print("[fetchCotSp500] downloading OFR TFF mirror...")
    remote = fetch_remote()
    print(f"  remote rows: {len(remote)} ({remote['as_of_date'].min()} to {remote['as_of_date'].max()})")

    remote.to_csv(OUT_PATH, index=False)
    print(f"[fetchCotSp500] wrote {OUT_PATH} ({len(remote)} rows)")


if __name__ == "__main__":
    main()
