#!/usr/bin/env python3
"""
fetchIntradayBars.py
====================
Fetches 1-minute SPY bars from Alpaca and stores them as year-partitioned CSVs.

Output:
  outputs/markets/intraday/SPY_{YEAR}_1min.csv
  Columns: date, open, high, low, close, volume, vw, n, session

Modes:
  DAILY (no args):   fetch yesterday's full session (4am-8pm ET)
  BACKFILL:          --backfill YYYY-MM-DD YYYY-MM-DD

Usage:
  python3 fetchIntradayBars.py
  python3 fetchIntradayBars.py --backfill 2024-01-01 2024-12-31
"""

import os
import sys
import time
import datetime
import argparse
import requests
import pandas as pd
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
HEADERS    = {
    "APCA-API-KEY-ID":    API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

TICKER   = "SPY"
ET       = ZoneInfo("America/New_York")
BASE_URL = f"https://data.alpaca.markets/v2/stocks/{TICKER}/bars"
OUT_DIR  = os.path.expanduser("~/discordBot/outputs/markets/intraday")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def csv_path(year: int) -> str:
    return os.path.join(OUT_DIR, f"{TICKER}_{year}_1min.csv")


def classify_session(ts: pd.Timestamp) -> str:
    """Classify a bar timestamp (ET-aware) into pre / regular / post."""
    t = ts.time()
    market_open  = datetime.time(9, 30)
    market_close = datetime.time(16, 0)
    if t < market_open:
        return "pre"
    elif t < market_close:
        return "regular"
    else:
        return "post"


def load_existing_dates(year: int) -> set:
    """Return the set of date strings (YYYY-MM-DD) already present in the CSV."""
    path = csv_path(year)
    if not os.path.exists(path):
        return set()
    df = pd.read_csv(path, usecols=["date"])
    # date column is an ISO8601 string with TZ; strip to just the date part
    return set(df["date"].str[:10].unique())


def load_existing_df(year: int) -> pd.DataFrame:
    """Load full existing CSV for a year, or return empty DataFrame."""
    path = csv_path(year)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def save_year_df(df: pd.DataFrame, year: int):
    """Sort by date and write the year CSV."""
    path = csv_path(year)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(path, index=False)
    size_kb = os.path.getsize(path) / 1024
    date_min = df["date"].min()[:10]
    date_max = df["date"].max()[:10]
    print(f"  -> {path}")
    print(f"     {len(df)} bars | {date_min} to {date_max} | {size_kb:.1f} KB")


def fetch_bars_for_date(trade_date: datetime.date) -> list:
    """
    Fetch all 1-min bars for trade_date covering extended hours (4am-8pm ET).
    Paginates automatically. Returns list of raw bar dicts.
    """
    # 4:00 AM ET start, 8:00 PM ET end
    start_et = datetime.datetime(trade_date.year, trade_date.month, trade_date.day,
                                  4, 0, 0, tzinfo=ET)
    end_et   = datetime.datetime(trade_date.year, trade_date.month, trade_date.day,
                                  20, 0, 0, tzinfo=ET)

    params = {
        "timeframe":  "1Min",
        "start":      start_et.isoformat(),
        "end":        end_et.isoformat(),
        "feed":       "sip",
        "limit":      10000,
        "adjustment": "all",
    }

    all_bars = []
    url = BASE_URL

    while url:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        bars = data.get("bars", [])
        all_bars.extend(bars)
        next_token = data.get("next_page_token")
        if next_token:
            params = {"page_token": next_token}
        else:
            url = None

    return all_bars


def bars_to_df(raw_bars: list) -> pd.DataFrame:
    """Convert raw Alpaca bar list to a clean DataFrame with ET timestamps."""
    if not raw_bars:
        return pd.DataFrame()

    df = pd.DataFrame(raw_bars)
    df = df.rename(columns={
        "t": "date",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
    })

    # vw and n may or may not be present; ensure columns exist
    for col in ("vw", "n"):
        if col not in df.columns:
            df[col] = None

    # convert timestamps to ET
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(ET)
    df["session"] = df["date"].apply(classify_session)
    # store as ISO8601 string with TZ offset (keeps timezone info in CSV)
    df["date"] = df["date"].apply(lambda ts: ts.isoformat())

    return df[["date", "open", "high", "low", "close", "volume", "vw", "n", "session"]]


def append_bars(new_df: pd.DataFrame, year: int, trade_date_str: str) -> int:
    """
    Merge new_df into the existing year CSV (idempotent).
    Returns the number of new bars actually written.
    """
    existing_df = load_existing_df(year)

    if existing_df.empty:
        combined = new_df
    else:
        # drop any rows from existing that match today's date (replace them cleanly)
        mask = existing_df["date"].str[:10] == trade_date_str
        existing_df = existing_df[~mask]
        combined = pd.concat([existing_df, new_df], ignore_index=True)

    save_year_df(combined, year)
    return len(new_df)


def probe_last_trading_day() -> datetime.date:
    """
    Step back from today up to 7 days to find the last trading day by
    probing Alpaca for at least 1 bar.
    """
    today = datetime.date.today()
    for i in range(1, 8):
        candidate = today - datetime.timedelta(days=i)
        if candidate.weekday() >= 5:   # skip weekends fast
            continue
        try:
            r = requests.get(BASE_URL, headers=HEADERS, params={
                "timeframe":  "1Min",
                "start":      candidate.isoformat(),
                "end":        candidate.isoformat(),
                "feed":       "sip",
                "limit":      1,
                "adjustment": "all",
            }, timeout=15)
            if r.status_code == 200 and r.json().get("bars"):
                return candidate
        except Exception:
            pass
    raise RuntimeError("Could not find a trading day in the last 7 calendar days.")


def iter_calendar_dates(start: datetime.date, end: datetime.date):
    """Yield each calendar date from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------

def run_daily():
    """DAILY mode: fetch the last trading day if not already present."""
    print(f"[fetchIntradayBars] DAILY mode")

    trade_date = probe_last_trading_day()
    date_str   = trade_date.isoformat()
    year       = trade_date.year

    existing_dates = load_existing_dates(year)
    if date_str in existing_dates:
        print(f"  {date_str} already in CSV -- nothing to do.")
        return

    print(f"  Fetching {TICKER} 1-min bars for {date_str} ...")
    try:
        raw = fetch_bars_for_date(trade_date)
    except Exception as e:
        print(f"  WARNING: API error for {date_str}: {e}")
        return

    if not raw:
        print(f"  WARNING: No bars returned for {date_str} -- market may have been closed.")
        return

    new_df = bars_to_df(raw)
    os.makedirs(OUT_DIR, exist_ok=True)
    n = append_bars(new_df, year, date_str)
    print(f"  Written {n} new bars for {date_str}.")


def run_backfill(start_date: datetime.date, end_date: datetime.date):
    """BACKFILL mode: iterate date range, skip weekends and dates already present."""
    print(f"[fetchIntradayBars] BACKFILL mode: {start_date} -> {end_date}")
    os.makedirs(OUT_DIR, exist_ok=True)

    # pre-load which dates we already have (per year, lazy)
    existing_dates_cache: dict[int, set] = {}

    total_bars = 0
    skipped    = 0

    all_dates = list(iter_calendar_dates(start_date, end_date))
    total_days = len(all_dates)

    for idx, d in enumerate(all_dates, 1):
        date_str = d.isoformat()
        year     = d.year

        # skip weekends
        if d.weekday() >= 5:
            continue

        # load date cache for this year if not yet loaded
        if year not in existing_dates_cache:
            existing_dates_cache[year] = load_existing_dates(year)

        if date_str in existing_dates_cache[year]:
            print(f"  [{idx}/{total_days}] {date_str} -- already present, skipping.")
            skipped += 1
            continue

        print(f"  [{idx}/{total_days}] {date_str} -- fetching ...", end=" ", flush=True)
        try:
            raw = fetch_bars_for_date(d)
        except Exception as e:
            print(f"WARNING: API error: {e}")
            time.sleep(0.3)
            continue

        if not raw:
            print("no bars (holiday or closed market)")
            time.sleep(0.3)
            continue

        new_df = bars_to_df(raw)
        n = append_bars(new_df, year, date_str)

        # update in-memory cache so later dates in same year skip correctly
        existing_dates_cache[year].add(date_str)

        total_bars += n
        print(f"{n} bars written.")
        time.sleep(0.3)

    print(f"\n[fetchIntradayBars] Done. {total_bars} total bars written, {skipped} dates skipped.")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch 1-min SPY bars from Alpaca into year-partitioned CSVs."
    )
    parser.add_argument(
        "--backfill",
        nargs=2,
        metavar=("START", "END"),
        help="Backfill date range: YYYY-MM-DD YYYY-MM-DD",
    )
    args = parser.parse_args()

    if not API_KEY or not API_SECRET:
        print("ERROR: Alpaca API keys not set. Check APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env.",
              file=sys.stderr)
        sys.exit(1)

    if args.backfill:
        try:
            start = datetime.date.fromisoformat(args.backfill[0])
            end   = datetime.date.fromisoformat(args.backfill[1])
        except ValueError as e:
            print(f"ERROR: Invalid date format: {e}", file=sys.stderr)
            sys.exit(1)
        if start > end:
            print("ERROR: START must be <= END", file=sys.stderr)
            sys.exit(1)
        run_backfill(start, end)
    else:
        run_daily()


if __name__ == "__main__":
    main()
