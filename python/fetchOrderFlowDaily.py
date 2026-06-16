#!/usr/bin/env python3
"""
fetchOrderFlowDaily.py
======================
Fetches SPY tick-level trades from Alpaca, classifies each trade's aggressor
side using the Lee-Ready tick rule, aggregates to per-minute Cumulative Volume
Delta (CVD) metrics, and saves year-partitioned CSVs.

Output: outputs/markets/orderflow/SPY_{YEAR}_cvd.csv

Columns (one row per minute of regular session):
  date             -- ISO8601 minute timestamp in America/New_York
                      e.g. '2026-05-07 09:30:00-04:00'
  buy_vol          -- sum of share size where side = +1 (buyer aggressor)
  sell_vol         -- sum of share size where side = -1 (seller aggressor)
  cvd              -- buy_vol - sell_vol
  large_buy_vol    -- buy_vol for trades where price*size >= $1,000,000
  large_sell_vol   -- sell_vol for trades where price*size >= $1,000,000
  large_cvd        -- large_buy_vol - large_sell_vol
  trade_count      -- total trades in minute (pre-filter)
  clean_trade_count -- trades that pass the EXCLUDE condition filter

Modes:
  DAILY (no args):
      Fetch last trading day, skip if already present, append + re-sort.
  BACKFILL (--backfill YYYY-MM-DD YYYY-MM-DD):
      Iterate all calendar dates in range, skip weekends + cached dates.
      0.5 s sleep between days.

Lee-Ready tick rule:
  Exclude conditions: '4' (derivatively priced), 'Q' (official close),
                      'O' (official open).
  Sort ascending by timestamp. For each clean trade:
    price_change = trade.price - prev.price
    > 0  => side = +1  (buyer, uptick)
    < 0  => side = -1  (seller, downtick)
    == 0 => side = inherit from last non-zero-tick side (zero-tick rule)
  First trade defaults to side = +1 (opening print convention).

Run:
  venv/bin/python3 python/fetchOrderFlowDaily.py
  venv/bin/python3 python/fetchOrderFlowDaily.py --backfill 2025-01-01 2025-12-31
"""

import argparse
import datetime
import os
import sys
import time

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Credentials / config
# ---------------------------------------------------------------------------

load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

TRADES_URL    = "https://data.alpaca.markets/v2/stocks/SPY/trades"
TICKER        = "SPY"

OUT_DIR       = os.path.expanduser("~/discordBot/outputs/markets/orderflow")

ET_TZ         = "America/New_York"
SESSION_OPEN  = datetime.time(9, 30)   # 9:30 AM ET
SESSION_CLOSE = datetime.time(16, 0)   # 4:00 PM ET

LARGE_TRADE_THRESHOLD = 1_000_000      # $1 M notional

# Conditions that indicate a non-print (exclude from aggressor classification)
EXCLUDE_CONDITIONS = {"4", "Q", "O"}

CSV_COLUMNS = [
    "date",
    "buy_vol", "sell_vol", "cvd",
    "large_buy_vol", "large_sell_vol", "large_cvd",
    "trade_count", "clean_trade_count",
]


# ---------------------------------------------------------------------------
# Alpaca trade fetch
# ---------------------------------------------------------------------------

def fetch_trades(start_iso: str, end_iso: str) -> list[dict]:
    """
    Fetch all SIP trades for SPY between start_iso and end_iso (UTC ISO8601).
    Paginates via next_page_token. Returns list of raw trade dicts.
    Raises requests.HTTPError on non-2xx responses.
    NOTE: start and end must be included on every page request, not just the first.
    """
    base_params = {
        "start": start_iso,
        "end":   end_iso,
        "limit": 10000,
        "feed":  "sip",
    }
    all_trades: list[dict] = []
    next_tok: str | None = None

    while True:
        params = dict(base_params)
        if next_tok:
            params["page_token"] = next_tok
        resp = requests.get(TRADES_URL, headers=HEADERS, params=params, timeout=60)
        resp.raise_for_status()
        data       = resp.json()
        trades     = data.get("trades") or []
        all_trades.extend(trades)
        next_tok   = data.get("next_page_token")
        if not next_tok:
            break

    return all_trades


def session_window_utc(day: datetime.date) -> tuple[str, str]:
    """
    Return (start_utc, end_utc) ISO8601 strings for the regular session
    (9:30 AM - 4:00 PM ET) on day.
    """
    import pytz
    et = pytz.timezone(ET_TZ)
    open_dt  = et.localize(datetime.datetime(day.year, day.month, day.day, 9, 30, 0))
    close_dt = et.localize(datetime.datetime(day.year, day.month, day.day, 16, 0, 0))
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        open_dt.astimezone(pytz.utc).strftime(fmt),
        close_dt.astimezone(pytz.utc).strftime(fmt),
    )


def find_last_trading_day() -> datetime.date:
    """
    Step back from today (up to 7 days) to find the most recent completed
    trading day that has actual SPY trade data on Alpaca.
    """
    import pytz
    et = pytz.timezone(ET_TZ)
    today = datetime.datetime.now(tz=et).date()

    for offset in range(8):
        candidate = today - datetime.timedelta(days=offset)
        if candidate.weekday() >= 5:        # skip weekends fast
            continue
        # Make sure the session is over (avoid partial-day fetch)
        close_dt = et.localize(
            datetime.datetime(candidate.year, candidate.month, candidate.day, 16, 5, 0)
        )
        if datetime.datetime.now(tz=et) < close_dt:
            continue                        # market still open or same-day before close
        try:
            start_utc, end_utc = session_window_utc(candidate)
            params = {
                "start": start_utc,
                "end":   end_utc,
                "limit": 1,
                "feed":  "sip",
            }
            r = requests.get(TRADES_URL, headers=HEADERS, params=params, timeout=15)
            if r.status_code == 200 and r.json().get("trades"):
                return candidate
        except Exception:
            pass

    raise RuntimeError("Could not find a completed trading day with data in the last 7 days.")


# ---------------------------------------------------------------------------
# Lee-Ready tick rule classifier
# ---------------------------------------------------------------------------

def classify_side(trades: list[dict]) -> list[int]:
    """
    Apply the Lee-Ready tick rule to a sorted list of raw trade dicts.

    Each dict must have keys: 'p' (price float), 'c' (conditions list).
    EXCLUDE_CONDITIONS trades are skipped for classification purposes but
    the returned list is parallel to the INPUT list (excluded trades get
    side = 0, caller must handle).

    Returns a list of int side values (same length as input):
      +1 = buyer aggressor (uptick or zero-uptick)
      -1 = seller aggressor (downtick or zero-downtick)
       0 = excluded (derivatively priced / official open-close print)
    """
    sides = [0] * len(trades)
    last_side  = 1        # opening convention: first real print = buyer
    last_price: float | None = None

    for i, trade in enumerate(trades):
        conds = trade.get("c") or []
        # Exclude if any condition is in EXCLUDE_CONDITIONS
        if any(c in EXCLUDE_CONDITIONS for c in conds):
            sides[i] = 0
            continue

        price = float(trade["p"])

        if last_price is None:
            sides[i]  = 1          # first clean trade: buyer convention
            last_side = 1
        else:
            delta = price - last_price
            if delta > 0:
                side = 1
            elif delta < 0:
                side = -1
            else:
                side = last_side   # zero-tick: inherit last direction
            sides[i]  = side
            last_side = side

        last_price = price

    return sides


# ---------------------------------------------------------------------------
# Per-minute aggregation
# ---------------------------------------------------------------------------

def aggregate_cvd(trades_raw: list[dict]) -> pd.DataFrame:
    """
    Given raw trade dicts for one regular session, run Lee-Ready
    classification then aggregate to per-minute CVD rows.

    Returns a DataFrame with columns matching CSV_COLUMNS (no 'date' yet --
    caller must handle time zone formatting).
    """
    if not trades_raw:
        return pd.DataFrame(columns=CSV_COLUMNS)

    # Sort ascending by timestamp (Alpaca already returns sorted, but be safe)
    trades_raw = sorted(trades_raw, key=lambda t: t["t"])

    sides = classify_side(trades_raw)

    # Build working DataFrame
    df = pd.DataFrame(trades_raw)
    df["side"]  = sides
    df["ts"]    = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET_TZ)
    df["price"] = df["p"].astype(float)
    df["size"]  = df["s"].astype(int)
    df["notional"] = df["price"] * df["size"]
    df["minute"] = df["ts"].dt.floor("min")

    # is_clean: not in EXCLUDE_CONDITIONS
    def _is_clean(conds):
        if not isinstance(conds, list):
            return True
        return not any(c in EXCLUDE_CONDITIONS for c in conds)

    df["is_clean"] = df["c"].apply(_is_clean)
    df["is_large"] = df["notional"] >= LARGE_TRADE_THRESHOLD

    rows = []
    for minute, grp in df.groupby("minute", sort=True):
        total_count = len(grp)
        clean       = grp[grp["is_clean"]]
        clean_count = len(clean)

        buyers  = clean[clean["side"] ==  1]
        sellers = clean[clean["side"] == -1]

        buy_vol  = int(buyers["size"].sum())
        sell_vol = int(sellers["size"].sum())
        cvd      = buy_vol - sell_vol

        large_buyers  = buyers[buyers["is_large"]]
        large_sellers = sellers[sellers["is_large"]]

        large_buy  = int(large_buyers["size"].sum())
        large_sell = int(large_sellers["size"].sum())
        large_cvd  = large_buy - large_sell

        rows.append({
            "date":              str(minute),       # tz-aware string
            "buy_vol":           buy_vol,
            "sell_vol":          sell_vol,
            "cvd":               cvd,
            "large_buy_vol":     large_buy,
            "large_sell_vol":    large_sell,
            "large_cvd":         large_cvd,
            "trade_count":       total_count,
            "clean_trade_count": clean_count,
        })

    result = pd.DataFrame(rows, columns=CSV_COLUMNS)
    return result


# ---------------------------------------------------------------------------
# Year-partitioned CSV helpers
# ---------------------------------------------------------------------------

def csv_path(year: int) -> str:
    return os.path.join(OUT_DIR, f"{TICKER}_{year}_cvd.csv")


def load_csv(year: int) -> pd.DataFrame:
    """Load (or create empty) the CSV for the given year."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = csv_path(year)
    if os.path.exists(path):
        df = pd.read_csv(path, dtype=str)
        return df
    return pd.DataFrame(columns=CSV_COLUMNS)


def existing_minute_dates(df: pd.DataFrame) -> set[str]:
    """
    Return the set of DATE strings (YYYY-MM-DD) already covered in the CSV.
    The 'date' column holds full minute timestamps; we extract just the date.
    """
    if df.empty or "date" not in df.columns:
        return set()
    # Parse the date portion from ISO strings like '2026-05-07 09:30:00-04:00'
    return set(df["date"].str[:10].unique())


def save_csv(df: pd.DataFrame, year: int) -> None:
    """Sort by date, drop duplicates, write CSV."""
    os.makedirs(OUT_DIR, exist_ok=True)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(csv_path(year), index=False)


def append_day_to_csv(new_rows: pd.DataFrame) -> None:
    """
    Merge new_rows (one day) into the correct year CSV.
    Sorts and deduplicates on the 'date' (minute timestamp) column.
    """
    if new_rows.empty:
        return

    # Determine year from first row's date
    first_date_str = new_rows["date"].iloc[0]
    year = int(first_date_str[:4])

    existing = load_csv(year)
    combined = pd.concat([existing, new_rows], ignore_index=True)
    # Drop exact duplicate minute timestamps
    combined = combined.drop_duplicates(subset=["date"])
    save_csv(combined, year)


# ---------------------------------------------------------------------------
# Single-day processor
# ---------------------------------------------------------------------------

def process_day(day: datetime.date) -> pd.DataFrame | None:
    """
    Fetch and aggregate CVD rows for one trading day.
    Returns a DataFrame of per-minute rows, or None on error/no data.
    """
    start_utc, end_utc = session_window_utc(day)

    try:
        trades_raw = fetch_trades(start_utc, end_utc)
    except Exception as exc:
        print(f"  [warn] fetch failed for {day}: {exc}")
        return None

    if not trades_raw:
        print(f"  [skip] no trades for {day} -- probable non-trading day")
        return None

    agg = aggregate_cvd(trades_raw)

    if agg.empty:
        print(f"  [skip] aggregation produced no rows for {day}")
        return None

    total_cvd      = int(agg["cvd"].sum())
    total_large    = int(agg["large_cvd"].sum())
    total_trades   = int(agg["trade_count"].sum())

    print(
        f"  {day} -> {total_trades:,} trades, "
        f"CVD={total_cvd:+,}, large_cvd={total_large:+,}, "
        f"{len(agg)} minute rows"
    )

    return agg


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_daily() -> None:
    """Fetch yesterday (or last trading day) and append if not already stored."""
    if not API_KEY or not API_SECRET:
        print("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print("[fetchOrderFlowDaily] daily mode -- locating last trading day...")

    try:
        trade_day = find_last_trading_day()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    date_str = trade_day.isoformat()
    year     = trade_day.year

    existing = load_csv(year)
    known    = existing_minute_dates(existing)

    if date_str in known:
        print(f"  {date_str} already in CSV -- nothing to do.")
        return

    print(f"  [1/1] {date_str}...")
    rows = process_day(trade_day)

    if rows is None or rows.empty:
        print("  No data written.")
        return

    append_day_to_csv(rows)
    updated = load_csv(year)
    print(
        f"\n[fetchOrderFlowDaily] summary: 1 day written | "
        f"{len(rows)} minute rows | "
        f"CSV date range {updated['date'].iloc[0][:10]} to {updated['date'].iloc[-1][:10]}"
    )
    print(f"  saved -> {csv_path(year)}")


def run_backfill(start_str: str, end_str: str) -> None:
    """Iterate a date range, skip weekends and already-present dates."""
    if not API_KEY or not API_SECRET:
        print("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set.", file=sys.stderr)
        sys.exit(1)

    try:
        start = datetime.date.fromisoformat(start_str)
        end   = datetime.date.fromisoformat(end_str)
    except ValueError as exc:
        print(f"ERROR: bad date format: {exc}", file=sys.stderr)
        sys.exit(1)

    if start > end:
        print("ERROR: start must be <= end.", file=sys.stderr)
        sys.exit(1)

    print(f"[fetchOrderFlowDaily] backfill mode {start_str} to {end_str}")

    # Build candidate list (weekdays only)
    candidates: list[datetime.date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            candidates.append(cur)
        cur += datetime.timedelta(days=1)

    total     = len(candidates)
    days_done = 0
    rows_done = 0

    # Pre-load all year CSVs touched by this range
    years_touched = set(d.year for d in candidates)
    cached_csvs: dict[int, pd.DataFrame] = {y: load_csv(y) for y in years_touched}
    known_by_year: dict[int, set[str]]   = {
        y: existing_minute_dates(df) for y, df in cached_csvs.items()
    }

    for i, day in enumerate(candidates, start=1):
        date_str = day.isoformat()
        year     = day.year

        if date_str in known_by_year.get(year, set()):
            print(f"  [{i}/{total}] {date_str} already cached -- skip")
            continue

        print(f"  [{i}/{total}] {date_str}...", end=" ", flush=True)

        try:
            rows = process_day(day)
        except Exception as exc:
            print(f"[warn] unexpected error for {date_str}: {exc}")
            rows = None

        if rows is not None and not rows.empty:
            # Merge into in-memory CSV cache
            existing = cached_csvs.get(year, pd.DataFrame(columns=CSV_COLUMNS))
            combined = pd.concat([existing, rows], ignore_index=True)
            combined = combined.drop_duplicates(subset=["date"])
            combined = combined.sort_values("date").reset_index(drop=True)
            cached_csvs[year]   = combined
            known_by_year[year] = existing_minute_dates(combined)
            days_done += 1
            rows_done += len(rows)

        time.sleep(0.5)

    # Flush all modified CSVs
    for year, df in cached_csvs.items():
        if not df.empty:
            save_csv(df, year)
            print(f"  saved -> {csv_path(year)}")

    if days_done == 0:
        print("\n[fetchOrderFlowDaily] no new days written.")
        return

    # Summary
    all_dfs = [df for df in cached_csvs.values() if not df.empty]
    if all_dfs:
        combined_all = pd.concat(all_dfs, ignore_index=True).sort_values("date")
        d_min = combined_all["date"].iloc[0][:10]
        d_max = combined_all["date"].iloc[-1][:10]
    else:
        d_min = d_max = "N/A"

    print(
        f"\n[fetchOrderFlowDaily] summary: {days_done} day(s) written | "
        f"{rows_done:,} total minute rows | "
        f"date range {d_min} to {d_max}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch SPY tick trades, classify aggressor side (Lee-Ready), "
                    "aggregate per-minute CVD, save year-partitioned CSVs."
    )
    parser.add_argument(
        "--backfill",
        nargs=2,
        metavar=("START", "END"),
        help="Backfill a date range: START END as YYYY-MM-DD.",
    )
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill[0], args.backfill[1])
    else:
        run_daily()


if __name__ == "__main__":
    main()
