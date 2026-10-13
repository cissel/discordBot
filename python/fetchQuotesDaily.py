#!/usr/bin/env python3
"""
fetchQuotesDaily.py
====================
Fetches SPY NBBO quotes from Alpaca (SIP feed), computes size-weighted
microprice and quote imbalance per minute, aggregates to per-minute summary
rows, and saves year-partitioned CSVs. Mirrors fetchOrderFlowDaily.py's
structure/patterns but reads quotes instead of trades.

Raw quotes are NEVER persisted to disk (SIP quote volume is ~2.5x the trade
tape -- ~1.9M quotes/day on SPY). Each day's raw quotes are streamed in,
aggregated to minute-level, then discarded.

Output: outputs/markets/quotes/SPY_{YEAR}_micro.csv

Columns (one row per minute of regular session):
  date              -- ISO8601 minute timestamp in America/New_York
  microprice_mean    -- mean size-weighted microprice for the minute
                        microprice = (bid*ask_size + ask*bid_size) / (bid_size+ask_size)
                        (weights toward the side with MORE size resting,
                        i.e. toward the side more likely to get run through)
  mid_mean           -- mean simple mid ((bid+ask)/2), for comparison/spread calc
  micro_mid_dev      -- mean(microprice - mid), the pure imbalance-driven skew
  imbalance_mean      -- mean (bid_size - ask_size) / (bid_size + ask_size) across quotes
                        +1 = all size on bid (buy pressure), -1 = all size on ask
  spread_mean         -- mean (ask - bid), raw dollar spread
  spread_bps_mean     -- mean spread in bps of mid price (comparable across vol regimes)
  quote_count         -- number of NBBO updates in the minute (quote intensity)

Modes:
  DAILY (no args):
      Fetch last trading day, skip if already present, append + re-sort.
  BACKFILL (--backfill YYYY-MM-DD YYYY-MM-DD):
      Iterate all calendar dates in range, skip weekends + cached dates.

Run:
  venv/bin/python3 python/fetchQuotesDaily.py
  venv/bin/python3 python/fetchQuotesDaily.py --backfill 2026-07-01 2026-10-09
"""

import argparse
import datetime
import os
import sys

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

QUOTES_URL    = "https://data.alpaca.markets/v2/stocks/SPY/quotes"
TICKER        = "SPY"

OUT_DIR       = os.path.expanduser("~/discordBot/outputs/markets/quotes")

ET_TZ         = "America/New_York"
SESSION_OPEN  = datetime.time(9, 30)
SESSION_CLOSE = datetime.time(16, 0)

CSV_COLUMNS = [
    "date",
    "microprice_mean", "mid_mean", "micro_mid_dev",
    "imbalance_mean", "spread_mean", "spread_bps_mean",
    "quote_count",
]

PAGE_LIMIT = 10000


# ---------------------------------------------------------------------------
# Alpaca quote fetch (streamed: aggregate incrementally, never hold full day)
# ---------------------------------------------------------------------------

def session_window_utc(day: datetime.date) -> tuple[str, str]:
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
    import pytz
    et = pytz.timezone(ET_TZ)
    today = datetime.datetime.now(tz=et).date()

    for offset in range(8):
        candidate = today - datetime.timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        close_dt = et.localize(
            datetime.datetime(candidate.year, candidate.month, candidate.day, 16, 5, 0)
        )
        if datetime.datetime.now(tz=et) < close_dt:
            continue
        try:
            start_utc, end_utc = session_window_utc(candidate)
            params = {"start": start_utc, "end": end_utc, "limit": 1, "feed": "sip"}
            r = requests.get(QUOTES_URL, headers=HEADERS, params=params, timeout=15)
            if r.status_code == 200 and r.json().get("quotes"):
                return candidate
        except Exception:
            pass

    raise RuntimeError("Could not find a completed trading day with data in the last 7 days.")


def process_day(day: datetime.date) -> pd.DataFrame | None:
    """
    Fetch all quotes for one session (paginated), load into a DataFrame,
    and aggregate to per-minute rows with vectorised pandas ops (the raw
    per-quote Python loop this replaced took ~6+ min/day -- CPU-bound, not
    network-bound -- for ~1.9M quotes/day; vectorised aggregation is the
    same pattern fetchOrderFlowDaily.py already uses successfully).
    """
    start_utc, end_utc = session_window_utc(day)

    base_params = {"start": start_utc, "end": end_utc, "limit": PAGE_LIMIT, "feed": "sip"}
    next_tok = None
    all_quotes: list[dict] = []

    while True:
        params = dict(base_params)
        if next_tok:
            params["page_token"] = next_tok
        try:
            resp = requests.get(QUOTES_URL, headers=HEADERS, params=params, timeout=60)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  [warn] fetch failed for {day}: {exc}")
            return None

        data   = resp.json()
        quotes = data.get("quotes") or []
        all_quotes.extend(quotes)

        next_tok = data.get("next_page_token")
        if not next_tok:
            break

    n_quotes = len(all_quotes)
    if n_quotes == 0:
        print(f"  [skip] no quotes for {day} -- probable non-trading day")
        return None

    df = pd.DataFrame(all_quotes)

    # Vectorised validity filter: both sides present, positive, bid < ask
    df["bp"] = pd.to_numeric(df["bp"], errors="coerce")
    df["ap"] = pd.to_numeric(df["ap"], errors="coerce")
    df["bs"] = pd.to_numeric(df["bs"], errors="coerce")
    df["as"] = pd.to_numeric(df["as"], errors="coerce")

    valid = (
        df["bp"].notna() & df["ap"].notna() & df["bs"].notna() & df["as"].notna()
        & (df["bp"] > 0) & (df["ap"] > 0) & (df["bs"] > 0) & (df["as"] > 0)
        & (df["bp"] < df["ap"])
    )
    n_valid = int(valid.sum())
    df = df[valid].copy()

    if df.empty:
        print(f"  [skip] no valid quotes survived filtering for {day}")
        return None

    # Vectorised microstructure calcs
    df["microprice"] = (df["bp"] * df["as"] + df["ap"] * df["bs"]) / (df["bs"] + df["as"])
    df["mid"]        = (df["bp"] + df["ap"]) / 2.0
    df["micro_mid_dev"] = df["microprice"] - df["mid"]
    df["imbalance"]  = (df["bs"] - df["as"]) / (df["bs"] + df["as"])
    df["spread"]     = df["ap"] - df["bp"]
    df["spread_bps"] = (df["spread"] / df["mid"]) * 10000.0

    df["ts"]     = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET_TZ)
    df["minute"] = df["ts"].dt.floor("min")

    grouped = df.groupby("minute").agg(
        microprice_mean=("microprice", "mean"),
        mid_mean=("mid", "mean"),
        micro_mid_dev=("micro_mid_dev", "mean"),
        imbalance_mean=("imbalance", "mean"),
        spread_mean=("spread", "mean"),
        spread_bps_mean=("spread_bps", "mean"),
        quote_count=("mid", "size"),
    ).reset_index()

    grouped["date"] = grouped["minute"].astype(str)
    result = grouped[CSV_COLUMNS].sort_values("date").reset_index(drop=True)

    print(
        f"  {day} -> {n_quotes:,} raw quotes ({n_valid:,} valid, "
        f"{n_valid/n_quotes*100:.1f}%), {len(result)} minute rows, "
        f"mean imbalance={result['imbalance_mean'].mean():+.3f}, "
        f"mean spread_bps={result['spread_bps_mean'].mean():.2f}"
    )
    return result


# ---------------------------------------------------------------------------
# Year-partitioned CSV helpers (identical pattern to fetchOrderFlowDaily.py)
# ---------------------------------------------------------------------------

def csv_path(year: int) -> str:
    return os.path.join(OUT_DIR, f"{TICKER}_{year}_micro.csv")


def load_csv(year: int) -> pd.DataFrame:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = csv_path(year)
    if os.path.exists(path):
        return pd.read_csv(path, dtype=str)
    return pd.DataFrame(columns=CSV_COLUMNS)


def existing_minute_dates(df: pd.DataFrame) -> set[str]:
    if df.empty or "date" not in df.columns:
        return set()
    return set(df["date"].str[:10].unique())


def save_csv(df: pd.DataFrame, year: int) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(csv_path(year), index=False)


def append_day_to_csv(new_rows: pd.DataFrame) -> None:
    if new_rows.empty:
        return
    first_date_str = new_rows["date"].iloc[0]
    year = int(first_date_str[:4])

    existing = load_csv(year)
    if existing.empty:
        combined = new_rows.copy()
    else:
        combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"])
    save_csv(combined, year)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_daily() -> None:
    if not API_KEY or not API_SECRET:
        print("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print("[fetchQuotesDaily] daily mode -- locating last trading day...")

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
        f"\n[fetchQuotesDaily] summary: 1 day written | "
        f"{len(rows)} minute rows | "
        f"CSV date range {updated['date'].iloc[0][:10]} to {updated['date'].iloc[-1][:10]}"
    )
    print(f"  saved -> {csv_path(year)}")


def run_backfill(start_str: str, end_str: str) -> None:
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

    print(f"[fetchQuotesDaily] backfill mode {start_str} to {end_str}")

    candidates: list[datetime.date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            candidates.append(cur)
        cur += datetime.timedelta(days=1)

    total = len(candidates)

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
            existing = cached_csvs.get(year, pd.DataFrame(columns=CSV_COLUMNS))
            if existing.empty:
                combined = rows.copy()
            else:
                combined = pd.concat([existing, rows], ignore_index=True)
            combined = combined.drop_duplicates(subset=["date"])
            cached_csvs[year] = combined
            known_by_year.setdefault(year, set()).add(date_str)
            # Save incrementally after every day so a crash doesn't lose progress
            save_csv(combined, year)

    print(f"\n[fetchQuotesDaily] backfill complete: {total} candidate days processed.")
    for y in sorted(years_touched):
        df = load_csv(y)
        if not df.empty:
            print(f"  {y}: {len(df)} minute rows, "
                  f"{df['date'].str[:10].nunique()} trading days, "
                  f"range {df['date'].iloc[0][:10]} to {df['date'].iloc[-1][:10]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", nargs=2, metavar=("START", "END"))
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill[0], args.backfill[1])
    else:
        run_daily()


if __name__ == "__main__":
    main()
