"""
fetchBlockSignals.py
--------------------
Detects large SPY block trades from Alpaca tick data and maintains two CSVs:

  outputs/research/block_events.csv
    columns: ticker, trade_date, time, price, size, deviation,
             dollar_value, exchange, forward_bars_pulled

  outputs/research/block_outcomes.csv
    columns: block_price, block_time, market_price, direction,
             reached_1d, pct_toward_1d, close_1d,
             reached_3d, pct_toward_3d, close_3d,
             reached_1w, pct_toward_1w, close_1w,
             reached_2w, pct_toward_2w, close_2w,
             reached_1mo, pct_toward_1mo, close_1mo,
             event_idx, ticker, trade_date, size, deviation,
             dollar_value, exchange

Usage:
  python fetchBlockSignals.py                          # daily mode
  python fetchBlockSignals.py --backfill 2025-01-01 2025-06-30
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytz
import requests
from dotenv import load_dotenv

# ── env / credentials ─────────────────────────────────────────────────────────
load_dotenv(os.path.expanduser("~/discordBot/.env"))

API_KEY    = os.getenv("APCA_API_KEY_ID",     "").strip()
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()

if not API_KEY or not API_SECRET:
    print("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not found in .env",
          file=sys.stderr)
    sys.exit(1)

# ── paths ─────────────────────────────────────────────────────────────────────
OUT_DIR      = os.path.expanduser("~/discordBot/outputs/research")
EVENTS_CSV   = os.path.join(OUT_DIR, "block_events.csv")
OUTCOMES_CSV = os.path.join(OUT_DIR, "block_outcomes.csv")

os.makedirs(OUT_DIR, exist_ok=True)

# ── constants ─────────────────────────────────────────────────────────────────
TICKER            = "SPY"
BASE_URL          = "https://data.alpaca.markets/v2"

# Session window: 9 AM - 5 PM ET (captures pre-market blocks and 4 PM prints)
SESSION_START_H   = 9
SESSION_END_H     = 17

# Dynamic threshold: 0.15 % of avg daily volume over last 20 trading days
SIZE_THRESHOLD_PCT = 0.0015
SIZE_FLOOR         = 100_000          # never go below 100k shares

# Block qualification criteria
MIN_DEVIATION      = 0.005            # 0.5 % from last close
MIN_DOLLAR_VALUE   = 300_000_000      # $300 M

# De-duplication window
DEDUP_WINDOW_MINS  = 5

# How many prior trading days to use for avg-volume calculation
VOL_LOOKBACK_DAYS  = 20

# Forward-bar horizons expressed in *trading* days
HORIZONS = {
    "1d":  1,
    "3d":  3,
    "1w":  5,
    "2w":  10,
    "1mo": 21,
}

et  = pytz.timezone("America/New_York")
nyse = mcal.get_calendar("NYSE")

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# ── CSV column order (must match existing files exactly) ──────────────────────
EVENTS_COLS = [
    "ticker", "trade_date", "time", "price", "size",
    "deviation", "dollar_value", "exchange", "forward_bars_pulled",
]
OUTCOMES_COLS = [
    "block_price", "block_time", "market_price", "direction",
    "reached_1d", "pct_toward_1d", "close_1d",
    "reached_3d", "pct_toward_3d", "close_3d",
    "reached_1w", "pct_toward_1w", "close_1w",
    "reached_2w", "pct_toward_2w", "close_2w",
    "reached_1mo", "pct_toward_1mo", "close_1mo",
    "event_idx", "ticker", "trade_date", "size",
    "deviation", "dollar_value", "exchange",
]


# ── calendar helpers ───────────────────────────────────────────────────────────
def get_trading_days(start_date: date, end_date: date) -> list:
    """Return sorted list of trading day date objects between start and end (inclusive)."""
    sched = nyse.schedule(start_date=str(start_date), end_date=str(end_date))
    return [d.date() for d in sched.index]


def last_trading_day() -> date:
    """Return the most recent completed trading day (today if market closed, else yesterday)."""
    today = datetime.now(et).date()
    for offset in range(7):
        candidate = today - timedelta(days=offset)
        days = get_trading_days(candidate, candidate)
        if days:
            # If candidate is today and market is still open, step back one more
            now_et = datetime.now(et)
            mkt_close = et.localize(
                datetime(candidate.year, candidate.month, candidate.day, 16, 0, 0)
            )
            if candidate == today and now_et < mkt_close:
                continue
            return candidate
    raise RuntimeError("Could not find a recent trading day in the last 7 calendar days.")


def trading_days_after(start_date: date, n: int) -> list:
    """Return up to n trading days AFTER start_date (not including start_date)."""
    search_end = start_date + timedelta(days=n * 3 + 30)  # generous pad
    all_days = get_trading_days(start_date, search_end)
    after = [d for d in all_days if d > start_date]
    return after[:n]


# ── already-processed guard ────────────────────────────────────────────────────
def dates_in_events_csv() -> set:
    """Return the set of trade_date strings already in block_events.csv."""
    if not os.path.exists(EVENTS_CSV):
        return set()
    try:
        df = pd.read_csv(EVENTS_CSV, usecols=["trade_date"])
        return set(df["trade_date"].astype(str).unique())
    except Exception:
        return set()


# ── Alpaca API helpers ─────────────────────────────────────────────────────────
def fetch_daily_bars(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Fetch adjusted daily OHLCV bars for ticker between start_date and end_date.
    Returns DataFrame with columns [date, open, high, low, close, volume].
    date column is a Python date object.
    """
    url    = f"{BASE_URL}/stocks/{ticker}/bars"
    params = {
        "timeframe":  "1Day",
        "start":      str(start_date),
        "end":        str(end_date),
        "adjustment": "all",
        "feed":       "sip",
        "limit":      10000,
    }
    all_bars   = []
    page_token = None

    while True:
        if page_token:
            params["page_token"] = page_token
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] fetch_daily_bars error: {exc}")
            break
        data = resp.json()
        all_bars.extend(data.get("bars", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break
        time.sleep(0.1)

    if not all_bars:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_bars).rename(columns={
        "t": "date", "o": "open", "h": "high",
        "l": "low",  "c": "close", "v": "volume",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_trades_for_day(ticker: str, day: date) -> pd.DataFrame:
    """
    Fetch all SIP trades for ticker on day between SESSION_START_H and SESSION_END_H ET.
    Returns DataFrame with columns [time, price, size, exchange, conditions, tape].
    time is a tz-aware pandas Timestamp (America/New_York).
    """
    start_et = et.localize(datetime(day.year, day.month, day.day, SESSION_START_H, 0, 0))
    end_et   = et.localize(datetime(day.year, day.month, day.day, SESSION_END_H, 0, 0))
    start_utc = start_et.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = end_et.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    url        = f"{BASE_URL}/stocks/{ticker}/trades"
    all_trades = []
    page_token = None

    while True:
        params = {
            "start": start_utc,
            "end":   end_utc,
            "limit": 10000,
            "feed":  "sip",
        }
        if page_token:
            params["page_token"] = page_token

        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] fetch_trades_for_day error on page: {exc}")
            break

        data = resp.json()
        all_trades.extend(data.get("trades", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break
        time.sleep(0.1)

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades).rename(columns={
        "t": "time", "p": "price", "s": "size",
        "x": "exchange", "c": "conditions", "z": "tape",
    })
    df["time"]  = pd.to_datetime(df["time"], utc=True, format="ISO8601").dt.tz_convert("America/New_York")
    df["price"] = df["price"].astype(float)
    df["size"]  = df["size"].astype(int)
    df = df.sort_values("time").reset_index(drop=True)
    return df


# ── dynamic size threshold ─────────────────────────────────────────────────────
def compute_size_threshold(ref_date: date) -> int:
    """
    Fetch the last VOL_LOOKBACK_DAYS daily bars ending the day before ref_date,
    compute avg daily volume, and return 0.15 % of it (floored at SIZE_FLOOR).
    """
    end_date   = ref_date - timedelta(days=1)
    start_date = ref_date - timedelta(days=VOL_LOOKBACK_DAYS * 3)  # pad for weekends
    try:
        bars = fetch_daily_bars(TICKER, start_date, end_date)
    except Exception as exc:
        print(f"  [WARN] Could not fetch bars for size threshold: {exc}. Using floor.")
        return SIZE_FLOOR

    if bars.empty:
        return SIZE_FLOOR

    recent = bars.tail(VOL_LOOKBACK_DAYS)
    avg_vol = recent["volume"].mean()
    threshold = max(int(avg_vol * SIZE_THRESHOLD_PCT), SIZE_FLOOR)
    return threshold


# ── block detection ────────────────────────────────────────────────────────────
def detect_blocks(trades: pd.DataFrame, last_close: float,
                  size_threshold: int) -> pd.DataFrame:
    """
    Identify qualifying block trades from a day's trade tape.

    Criteria (all must pass):
      1. size >= size_threshold
      2. deviation = |price - last_close| / last_close >= MIN_DEVIATION
      3. dollar_value = price * size >= MIN_DOLLAR_VALUE

    De-duplication: within any DEDUP_WINDOW_MINS-minute window keep only the
    single largest trade (by dollar_value) to avoid counting partial fills.

    Returns DataFrame with columns:
      [time, price, size, deviation, dollar_value, exchange]
    """
    if trades.empty or last_close <= 0:
        return pd.DataFrame()

    df = trades.copy()
    df["deviation"]    = (df["price"] - last_close).abs() / last_close
    df["dollar_value"] = df["price"] * df["size"]

    candidates = df[
        (df["size"]        >= size_threshold) &
        (df["deviation"]   >= MIN_DEVIATION) &
        (df["dollar_value"] >= MIN_DOLLAR_VALUE)
    ].copy()

    if candidates.empty:
        return pd.DataFrame()

    # De-duplicate: bucket into DEDUP_WINDOW_MINS-minute windows, keep max dollar_value
    candidates = candidates.sort_values("time")
    window_secs = DEDUP_WINDOW_MINS * 60
    candidates["_bucket"] = (
        candidates["time"].astype(np.int64) // (window_secs * 1_000_000_000)
    )
    best_idx = candidates.groupby("_bucket")["dollar_value"].idxmax()
    blocks   = candidates.loc[best_idx].drop(columns="_bucket").reset_index(drop=True)

    return blocks[["time", "price", "size", "deviation", "dollar_value", "exchange"]]


# ── append block events ────────────────────────────────────────────────────────
def append_block_events(blocks: pd.DataFrame, day: date, ticker: str) -> None:
    """Append qualified blocks to block_events.csv."""
    if blocks.empty:
        return

    out = blocks.copy()
    out.insert(0, "ticker",     ticker)
    out.insert(1, "trade_date", str(day))
    out["forward_bars_pulled"] = False
    out = out[EVENTS_COLS]

    header = not os.path.exists(EVENTS_CSV)
    out.to_csv(EVENTS_CSV, mode="a", header=header, index=False)
    print(f"  Appended {len(out)} block event(s) to {EVENTS_CSV}.")


# ── outcome computation (trading-day horizons) ────────────────────────────────
def compute_outcomes_for_event(
    row: pd.Series,
    forward_bars: pd.DataFrame,
    event_idx: int,
) -> tuple:
    """
    Compute outcome metrics for a single block event.

    forward_bars: daily bars DataFrame starting from the day AFTER the block trade_date.
                  columns: [date, open, high, low, close, volume]
                  date is a Python date object.

    Returns (outcomes_dict, all_horizons_filled: bool).
    """
    block_price  = float(row["price"])
    block_time   = row["time"]   # tz-aware timestamp string or Timestamp
    market_price = float(row["market_price"])
    direction    = "above_market" if block_price > market_price else "below_market"

    out = {
        "block_price":  block_price,
        "block_time":   str(block_time),
        "market_price": market_price,
        "direction":    direction,
    }

    all_filled = True

    for label, n_days in HORIZONS.items():
        window = forward_bars.iloc[:n_days]   # first n_days rows after block date

        if len(window) < n_days:
            # Not enough trading days have elapsed yet
            out[f"reached_{label}"]    = False
            out[f"pct_toward_{label}"] = 0.0
            out[f"close_{label}"]      = float("nan")
            all_filled = False
            continue

        close_n = float(window["close"].iloc[-1])

        if direction == "above_market":
            # Block above market => smart money bought above. Bullish pull.
            # "reached" = max price in window >= block_price
            reached = float(window["high"].max()) >= block_price
            gap     = block_price - market_price
            if gap == 0:
                pct = 0.0
            else:
                best_move = float(window["high"].max()) - market_price
                pct = min(best_move / gap, 1.0) * 100.0
        else:
            # Block below market => printed below current price. Bearish pull.
            # "reached" = min price in window <= block_price
            reached = float(window["low"].min()) <= block_price
            gap     = market_price - block_price
            if gap == 0:
                pct = 0.0
            else:
                best_drop = market_price - float(window["low"].min())
                pct = min(best_drop / gap, 1.0) * 100.0

        out[f"reached_{label}"]    = reached
        out[f"pct_toward_{label}"] = pct
        out[f"close_{label}"]      = close_n

    # Attach metadata
    out["event_idx"]    = event_idx
    out["ticker"]       = row["ticker"]
    out["trade_date"]   = row["trade_date"]
    out["size"]         = int(row["size"])
    out["deviation"]    = float(row["deviation"])
    out["dollar_value"] = float(row["dollar_value"])
    out["exchange"]     = row["exchange"]

    return out, all_filled


# ── pull forward bars and write outcomes ──────────────────────────────────────
def process_pending_outcomes(events: pd.DataFrame) -> pd.DataFrame:
    """
    For all rows in events where forward_bars_pulled == False, fetch forward
    daily bars and compute outcomes. Writes/appends to OUTCOMES_CSV.
    Updates events in-place and returns the modified DataFrame.
    """
    pending_mask = events["forward_bars_pulled"].astype(str).str.lower() != "true"
    pending      = events[pending_mask]

    if pending.empty:
        print("No pending outcome rows.")
        return events

    print(f"Computing outcomes for {len(pending)} pending block event(s)...")

    # Pre-compute a market_price column if not already there
    # market_price = last close before the block trade_date
    if "market_price" not in events.columns:
        events["market_price"] = np.nan

    # Cache daily bars per ticker to avoid redundant API calls
    bars_cache: dict = {}

    new_outcomes = []

    for df_idx, row in pending.iterrows():
        ticker     = row["ticker"]
        trade_date = pd.to_datetime(row["trade_date"]).date()

        print(f"  [{df_idx}] {ticker} {trade_date} @ ${float(row['price']):.4f} ...",
              end=" ", flush=True)

        # Need bars starting from the day before trade_date (for last_close)
        # through 21 trading days after (1mo horizon)
        cache_key = ticker
        if cache_key not in bars_cache:
            fetch_start = trade_date - timedelta(days=60)
            fetch_end   = date.today() + timedelta(days=5)
            try:
                bars_cache[cache_key] = fetch_daily_bars(ticker, fetch_start, fetch_end)
            except Exception as exc:
                print(f"ERROR fetching bars: {exc}")
                continue

        all_bars = bars_cache[cache_key]

        if all_bars.empty:
            print("no daily bars available.")
            continue

        # Last close BEFORE trade_date
        prior = all_bars[all_bars["date"] < trade_date]
        if prior.empty:
            print("no prior close available.")
            continue
        market_price = float(prior["close"].iloc[-1])

        # Forward bars: trading days STRICTLY AFTER trade_date
        forward = all_bars[all_bars["date"] > trade_date].reset_index(drop=True)

        # Attach market_price onto the row for compute_outcomes_for_event
        row_copy = row.copy()
        row_copy["market_price"] = market_price
        events.at[df_idx, "market_price"] = market_price   # harmless extra column

        try:
            outcome, all_filled = compute_outcomes_for_event(row_copy, forward, df_idx)
        except Exception as exc:
            print(f"ERROR computing outcomes: {exc}")
            continue

        new_outcomes.append(outcome)

        if all_filled:
            events.at[df_idx, "forward_bars_pulled"] = True
            print("done (all horizons filled).")
        else:
            # Partial: still mark True so we don't re-run the same row forever
            # on every daily run; user can reset forward_bars_pulled to re-check.
            events.at[df_idx, "forward_bars_pulled"] = True
            print("done (some horizons not yet available - marked complete).")

        time.sleep(0.2)

    if new_outcomes:
        out_df  = pd.DataFrame(new_outcomes)[OUTCOMES_COLS]
        header  = not os.path.exists(OUTCOMES_CSV)
        out_df.to_csv(OUTCOMES_CSV, mode="a", header=header, index=False)
        print(f"  Wrote {len(new_outcomes)} outcome row(s) to {OUTCOMES_CSV}.")
    else:
        print("  No new outcomes to write.")

    return events


# ── refresh partial outcomes (any horizon close still empty) ──────────────────
def refresh_partial_outcomes() -> None:
    """
    Re-compute outcomes for any row in block_outcomes.csv where ANY horizon
    close is still empty but enough trading days have elapsed for it to exist.
    Covers:
      - Blocks from yesterday with empty close_1d
      - Older blocks where 3d/1w/2w/1mo closes never got written
    """
    if not os.path.exists(OUTCOMES_CSV):
        return

    outcomes = pd.read_csv(OUTCOMES_CSV)
    if outcomes.empty:
        return

    today = date.today()

    def _empty(val):
        if val is None:
            return True
        try:
            if isinstance(val, float) and np.isnan(val):
                return True
        except Exception:
            pass
        return str(val).strip() == ""

    # A row needs refresh if ANY horizon close is empty AND enough days have
    # elapsed for that horizon to have data.
    HORIZON_DAYS = {"close_1d": 1, "close_3d": 3, "close_1w": 5,
                    "close_2w": 10, "close_1mo": 21}

    def row_needs_refresh(row):
        try:
            trade_date = pd.to_datetime(row.get("trade_date")).date()
        except Exception:
            return False
        trading_days_elapsed = len(get_trading_days(trade_date, today)) - 1  # exclude trade_date itself
        for col, min_days in HORIZON_DAYS.items():
            if trading_days_elapsed >= min_days and _empty(row.get(col)):
                return True
        return False

    needs_refresh = outcomes[outcomes.apply(row_needs_refresh, axis=1)].copy()

    if needs_refresh.empty:
        print("No partial outcome rows to refresh.")
        return

    print(f"Refreshing {len(needs_refresh)} partial outcome row(s) with empty closes...")

    # Fetch bars once across the full date range needed
    all_trade_dates = pd.to_datetime(needs_refresh["trade_date"]).dt.date
    fetch_start = min(all_trade_dates) - timedelta(days=5)
    fetch_end   = today + timedelta(days=5)
    bars_cache: dict = {}

    updated_rows = {}

    for df_idx, row in needs_refresh.iterrows():
        ticker     = str(row.get("ticker", TICKER))
        trade_date = pd.to_datetime(row.get("trade_date")).date()

        print(f"  [{df_idx}] {ticker} {trade_date} @ ${float(row['block_price']):.4f} ...",
              end=" ", flush=True)

        cache_key = ticker
        if cache_key not in bars_cache:
            try:
                bars_cache[cache_key] = fetch_daily_bars(ticker, fetch_start, fetch_end)
            except Exception as exc:
                print(f"ERROR fetching bars: {exc}")
                continue

        all_bars = bars_cache[cache_key]
        if all_bars.empty:
            print("no bars available.")
            continue

        # Forward bars: trading days STRICTLY AFTER trade_date
        forward = all_bars[all_bars["date"] > trade_date].reset_index(drop=True)

        if forward.empty:
            print("no forward bars yet.")
            continue

        pseudo_row = row.copy()
        pseudo_row["price"] = row["block_price"]
        pseudo_row["time"]  = row["block_time"]

        try:
            outcome, all_filled = compute_outcomes_for_event(
                pseudo_row, forward, int(row.get("event_idx", df_idx))
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue

        updated_rows[df_idx] = outcome
        status = "all horizons filled" if all_filled else "partial"
        print(f"done ({status}).")
        time.sleep(0.1)

    if not updated_rows:
        print("  Nothing updated.")
        return

    # Apply updates back into outcomes DataFrame
    update_df = pd.DataFrame(updated_rows).T
    update_df.index = list(updated_rows.keys())

    for col in OUTCOMES_COLS:
        if col in update_df.columns:
            outcomes.loc[list(updated_rows.keys()), col] = update_df[col].astype(outcomes[col].dtype, errors="ignore").values

    outcomes[OUTCOMES_COLS].to_csv(OUTCOMES_CSV, index=False)
    print(f"  Refreshed {len(updated_rows)} row(s) in {OUTCOMES_CSV}.")


# ── process a single trading day ──────────────────────────────────────────────
def process_day(day: date) -> int:
    """
    Fetch trades and detect block events for one trading day.
    Returns number of block events found and appended (0 if none).
    """
    print(f"\n[{day}] Fetching SPY trades ({SESSION_START_H}:00-{SESSION_END_H}:00 ET)...",
          end=" ", flush=True)

    # Dynamic size threshold
    size_threshold = compute_size_threshold(day)
    print(f"size_threshold={size_threshold:,}", end=" ", flush=True)

    # Last close for deviation calculation
    prior_start = day - timedelta(days=10)
    prior_bars  = fetch_daily_bars(TICKER, prior_start, day - timedelta(days=1))
    if prior_bars.empty:
        print("ERROR: no prior daily bars for last_close calculation.")
        return 0
    last_close = float(prior_bars["close"].iloc[-1])
    print(f"last_close=${last_close:.4f}", end=" ", flush=True)

    # Pull full trade tape
    try:
        trades = fetch_trades_for_day(TICKER, day)
    except Exception as exc:
        print(f"\n  ERROR fetching trades: {exc}")
        return 0

    if trades.empty:
        print(f"\n  No trades returned for {day}.")
        return 0

    total_trades = len(trades)
    print(f"-> {total_trades:,} trades fetched.")

    # Detect blocks
    blocks = detect_blocks(trades, last_close, size_threshold)

    n_candidates = len(trades[trades["size"] >= size_threshold])
    n_blocks     = len(blocks)
    print(f"  Found {n_candidates} size candidates, {n_blocks} qualified as block event(s).")

    if n_blocks == 0:
        return 0

    # Append to CSV
    append_block_events(blocks, day, TICKER)
    return n_blocks


# ── entry points ──────────────────────────────────────────────────────────────
def run_daily() -> None:
    """Process the last trading day (skip if already in events CSV)."""
    day            = last_trading_day()
    already_done   = dates_in_events_csv()

    if str(day) in already_done:
        print(f"[SKIP] {day} already in {EVENTS_CSV}. Nothing to do.")
    else:
        process_day(day)

    # Refresh any outcome rows where closes are still empty (partial fills from prior days)
    refresh_partial_outcomes()

    # Outcome pass for any pending rows (forward_bars_pulled == False)
    if os.path.exists(EVENTS_CSV):
        events = pd.read_csv(EVENTS_CSV)
        events = process_pending_outcomes(events)
        events[EVENTS_COLS].to_csv(EVENTS_CSV, index=False)


def run_backfill(start_str: str, end_str: str) -> None:
    """Process a range of trading days, skipping those already in events CSV."""
    start = date.fromisoformat(start_str)
    end   = date.fromisoformat(end_str)

    trading_days = get_trading_days(start, end)
    already_done = dates_in_events_csv()

    print(f"Backfill: {start} to {end} -> {len(trading_days)} trading day(s) to check.")

    total_blocks = 0
    for day in trading_days:
        if str(day) in already_done:
            print(f"[SKIP] {day} already processed.")
            continue
        n = process_day(day)
        total_blocks += n
        time.sleep(0.5)   # be polite between days

    print(f"\nBackfill complete. Total new block events appended: {total_blocks}.")

    # Outcome pass for any pending rows
    if os.path.exists(EVENTS_CSV):
        events = pd.read_csv(EVENTS_CSV)
        events = process_pending_outcomes(events)
        events[EVENTS_COLS].to_csv(EVENTS_CSV, index=False)


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect SPY block trades and maintain block_events / block_outcomes CSVs."
    )
    parser.add_argument(
        "--backfill", nargs=2, metavar=("START", "END"),
        help="Backfill mode: process YYYY-MM-DD to YYYY-MM-DD."
    )
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill[0], args.backfill[1])
    else:
        run_daily()
