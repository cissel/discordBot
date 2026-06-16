#!/usr/bin/env python3
"""
fetchVwapDaily.py
=================
Fetches SPY 1-minute intraday bars from Alpaca and computes daily VWAP
features, appending results to a cache CSV.

Modes:
  DAILY    (no args)                  -- fetch last trading day, append one row
  BACKFILL (--backfill YYYY-MM-DD YYYY-MM-DD) -- fill a date range day by day

Output:
  outputs/markets/cache/spy_vwap_daily.csv

Features per trading day (9:30 AM - 4:00 PM ET bars only):
  date, session_vwap, open_price, close_price,
  vwap_dev_close, vwap_dev_open,
  vwap_cross_count, vwap_time_above_pct,
  high_vol_above_vwap, vol_concentration, session_vol_total

Run:
  venv/bin/python3 python/fetchVwapDaily.py
  venv/bin/python3 python/fetchVwapDaily.py --backfill 2024-01-01 2024-12-31
"""

import os
import sys
import time
import argparse
import datetime
import requests
import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config / credentials
# ---------------------------------------------------------------------------

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

BASE_URL = "https://data.alpaca.markets/v2/stocks/SPY/bars"
TICKER   = "SPY"

OUT_PATH = os.path.expanduser(
    "~/discordBot/outputs/markets/cache/spy_vwap_daily.csv"
)

SESSION_OPEN  = datetime.time(9, 30)   # 9:30 AM ET
SESSION_CLOSE = datetime.time(16, 0)   # 4:00 PM ET


# ---------------------------------------------------------------------------
# Alpaca fetch helpers
# ---------------------------------------------------------------------------

def fetch_1min_bars(date_str: str) -> list[dict]:
    """
    Fetch all 1-min bars for TICKER on a single calendar date.
    Paginates automatically via next_page_token.
    Returns a list of raw bar dicts (t, o, h, l, c, v, vw).
    Raises on HTTP error.
    """
    params = {
        "timeframe":  "1Min",
        "start":      date_str,
        "end":        date_str,
        "adjustment": "all",
        "feed":       "sip",
        "limit":      10000,
    }
    all_bars = []
    while True:
        resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data      = resp.json()
        bars      = data.get("bars", []) or []
        all_bars.extend(bars)
        next_tok  = data.get("next_page_token")
        if not next_tok:
            break
        params = {"page_token": next_tok}
    return all_bars


def find_last_trading_day() -> datetime.date:
    """
    Start from today and step back up to 7 days to find the most recent
    date that has actual SPY 1-min bar data on Alpaca.
    """
    check = datetime.date.today()
    for _ in range(7):
        if check.weekday() < 5:  # Mon-Fri
            try:
                params = {
                    "timeframe": "1Min",
                    "start":     check.isoformat(),
                    "end":       check.isoformat(),
                    "feed":      "sip",
                    "limit":     1,
                }
                r = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=15)
                if r.status_code == 200 and r.json().get("bars"):
                    return check
            except Exception:
                pass
        check -= datetime.timedelta(days=1)
    raise RuntimeError("Could not find a trading day with data in the last 7 days.")


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_features(date_str: str, bars_raw: list[dict]) -> dict | None:
    """
    Given raw bar dicts for one day, filter to regular session (9:30-16:00 ET)
    and compute all VWAP features.  Returns None if no session bars found.
    """
    if not bars_raw:
        return None

    df = pd.DataFrame(bars_raw)
    # Rename to readable columns
    df = df.rename(columns={
        "t": "ts", "o": "open", "h": "high",
        "l": "low",  "c": "close", "v": "volume", "vw": "bar_vwap",
    })
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")

    # Filter to regular session only
    bar_time = df["ts"].dt.time
    df = df[(bar_time >= SESSION_OPEN) & (bar_time < SESSION_CLOSE)].copy()
    df = df.sort_values("ts").reset_index(drop=True)

    if df.empty:
        return None

    vol    = df["volume"].astype(float)
    close  = df["close"].astype(float)
    bvwap  = df["bar_vwap"].astype(float)
    total_vol = vol.sum()

    if total_vol == 0:
        return None

    # -- session_vwap: cumulative day VWAP using bar_vwap as price proxy --
    session_vwap = (bvwap * vol).sum() / total_vol

    # -- open / close prices --
    open_price  = float(df["open"].iloc[0])
    close_price = float(df["close"].iloc[-1])

    # -- vwap_dev_close: how far close ended from session VWAP --
    vwap_dev_close = (close_price - session_vwap) / session_vwap

    # -- vwap_dev_open: first bar's vw as early-session anchor --
    first_bar_vw   = float(bvwap.iloc[0])
    vwap_dev_open  = (open_price - first_bar_vw) / first_bar_vw if first_bar_vw != 0 else 0.0

    # -- running (cumulative) VWAP at each bar --
    cum_vol       = vol.cumsum()
    cum_notional  = (bvwap * vol).cumsum()
    running_vwap  = cum_notional / cum_vol   # pd.Series

    # -- vwap_cross_count: sign changes in (close - running_vwap) --
    above   = (close - running_vwap).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    # count transitions between non-zero sign values
    cross_count = 0
    prev_sign   = 0
    for s in above:
        if s != 0:
            if prev_sign != 0 and s != prev_sign:
                cross_count += 1
            prev_sign = s

    # -- vwap_time_above_pct: fraction of bars where close > running_vwap --
    time_above_pct = float((close > running_vwap).sum()) / len(df)

    # -- high_vol_above_vwap: volume above session_vwap / total volume --
    high_vol_above_vwap = float(vol[close > session_vwap].sum()) / total_vol

    # -- vol_concentration: fraction of volume in top 20% highest-volume bars --
    n_top = max(1, int(len(df) * 0.20))
    top_vol = vol.nlargest(n_top).sum()
    vol_concentration = float(top_vol) / total_vol

    return {
        "date":                 date_str,
        "session_vwap":         round(session_vwap,          4),
        "open_price":           round(open_price,            4),
        "close_price":          round(close_price,           4),
        "vwap_dev_close":       round(vwap_dev_close,        6),
        "vwap_dev_open":        round(vwap_dev_open,         6),
        "vwap_cross_count":     cross_count,
        "vwap_time_above_pct":  round(time_above_pct,        4),
        "high_vol_above_vwap":  round(high_vol_above_vwap,   4),
        "vol_concentration":    round(vol_concentration,     4),
        "session_vol_total":    int(total_vol),
    }


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

COLUMNS = [
    "date", "session_vwap", "open_price", "close_price",
    "vwap_dev_close", "vwap_dev_open",
    "vwap_cross_count", "vwap_time_above_pct",
    "high_vol_above_vwap", "vol_concentration", "session_vol_total",
]


def load_existing() -> pd.DataFrame:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    if os.path.exists(OUT_PATH):
        df = pd.read_csv(OUT_PATH, parse_dates=["date"])
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        return df
    return pd.DataFrame(columns=COLUMNS)


def save_df(df: pd.DataFrame) -> None:
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df.to_csv(OUT_PATH, index=False)


def existing_dates(df: pd.DataFrame) -> set:
    return set(df["date"].tolist())


# ---------------------------------------------------------------------------
# Process a single day
# ---------------------------------------------------------------------------

def process_day(date_str: str) -> dict | None:
    """
    Fetch bars for date_str, compute features, return row dict or None.
    Prints a warning on any error and returns None.
    """
    try:
        bars = fetch_1min_bars(date_str)
    except Exception as e:
        print(f"  [warn] fetch failed for {date_str}: {e}")
        return None

    if not bars:
        print(f"  [skip] no bars for {date_str} (non-trading day or no data)")
        return None

    row = compute_features(date_str, bars)
    if row is None:
        print(f"  [skip] no regular-session bars for {date_str}")
        return None
    return row


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_daily() -> None:
    """Fetch last trading day and append one row."""
    if not API_KEY or not API_SECRET:
        print("ERROR: Alpaca API keys not set (APCA_API_KEY_ID / APCA_API_SECRET_KEY)", file=sys.stderr)
        sys.exit(1)

    existing = load_existing()
    known    = existing_dates(existing)

    print("[fetchVwapDaily] daily mode -- finding last trading day...")
    trade_date = find_last_trading_day()
    date_str   = trade_date.isoformat()

    if date_str in known:
        print(f"  {date_str} already in cache -- nothing to do")
        return

    print(f"  processing {date_str}...")
    row = process_day(date_str)

    if row is None:
        print("  no data to append")
        return

    new_df = pd.DataFrame([row])
    combined = pd.concat([existing, new_df], ignore_index=True)
    save_df(combined)
    print(f"  appended {date_str} -> {OUT_PATH}")
    print(f"  summary: 1 row written | date range {combined['date'].min()} to {combined['date'].max()}")


def run_backfill(start_str: str, end_str: str) -> None:
    """Fetch a date range day by day, skipping dates already in the cache."""
    if not API_KEY or not API_SECRET:
        print("ERROR: Alpaca API keys not set (APCA_API_KEY_ID / APCA_API_SECRET_KEY)", file=sys.stderr)
        sys.exit(1)

    try:
        start = datetime.date.fromisoformat(start_str)
        end   = datetime.date.fromisoformat(end_str)
    except ValueError as e:
        print(f"ERROR: Invalid date format: {e}", file=sys.stderr)
        sys.exit(1)

    if start > end:
        print("ERROR: start date must be <= end date", file=sys.stderr)
        sys.exit(1)

    existing = load_existing()
    known    = existing_dates(existing)
    new_rows = []

    print(f"[fetchVwapDaily] backfill mode {start_str} to {end_str}")

    current = start
    while current <= end:
        date_str = current.isoformat()
        current += datetime.timedelta(days=1)

        # Skip weekends quickly
        if (current - datetime.timedelta(days=1)).weekday() >= 5:
            continue

        if date_str in known:
            print(f"  [skip] {date_str} already cached")
            continue

        print(f"  fetching {date_str}...", end=" ", flush=True)
        row = process_day(date_str)
        if row:
            new_rows.append(row)
            print(
                f"vwap={row['session_vwap']:.2f} "
                f"vol={row['session_vol_total']:,} "
                f"crosses={row['vwap_cross_count']}"
            )
        else:
            print("skipped")

        time.sleep(0.5)

    if not new_rows:
        print("[fetchVwapDaily] no new rows to write")
        return

    new_df = pd.DataFrame(new_rows)
    # Cast columns to match existing dtypes before concat to avoid FutureWarning
    for col in existing.columns:
        if col in new_df.columns and existing[col].dtype != object:
            try:
                new_df[col] = new_df[col].astype(existing[col].dtype)
            except (ValueError, TypeError):
                pass
    combined = pd.concat([existing, new_df], ignore_index=True)
    save_df(combined)
    n       = len(new_rows)
    d_min   = combined["date"].min()
    d_max   = combined["date"].max()
    print(f"\n[fetchVwapDaily] summary: {n} new row(s) written | cache covers {d_min} to {d_max}")
    print(f"  saved -> {OUT_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch SPY 1-min bars from Alpaca and compute daily VWAP features."
    )
    parser.add_argument(
        "--backfill",
        nargs=2,
        metavar=("START", "END"),
        help="Backfill mode: provide start and end dates as YYYY-MM-DD.",
    )
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill[0], args.backfill[1])
    else:
        run_daily()


if __name__ == "__main__":
    main()
