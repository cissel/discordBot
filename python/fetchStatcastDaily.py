#!/usr/bin/env python3
"""
fetchStatcastDaily.py
=====================
Fetches pitch-level Statcast data from Baseball Savant.
Appends to a rolling CSV partitioned by month for manageability.

Modes:
  --backfill            : fetch all missing dates from season start to yesterday
  --date YYYY-MM-DD     : fetch a specific date (default: yesterday)

Output:
  outputs/sports/mlb/statcast/statcast_YYYY_MM.csv   (one file per month)
  outputs/sports/mlb/statcast/fetch_log.csv          (dates fetched, row counts)

Columns kept (subset of 119 — only what's useful for feature engineering):
  game_date, batter, pitcher, player_name, stand, p_throws,
  pitch_type, pitch_name, release_speed, release_spin_rate,
  plate_x, plate_z, zone, description, events, bb_type,
  launch_speed, launch_angle, hit_distance_sc,
  estimated_ba_using_speedangle, estimated_woba_using_speedangle,
  woba_value, babip_value, iso_value, delta_run_exp,
  bat_speed, swing_length, attack_angle,
  balls, strikes, outs_when_up, inning,
  n_thruorder_pitcher, n_priorpa_thisgame_player_at_bat,
  game_pk, at_bat_number, pitch_number,
  home_team, away_team, game_type, game_year
"""

import os
import sys
import time
import datetime
import argparse
import requests
import pandas as pd

SEASON_START = datetime.date(2026, 3, 25)
OUT_DIR      = os.path.expanduser("~/discordBot/outputs/sports/mlb/statcast")
LOG_PATH     = os.path.join(OUT_DIR, "fetch_log.csv")
os.makedirs(OUT_DIR, exist_ok=True)

SAVANT_URL = "https://baseballsavant.mlb.com/statcast_search/csv"

# Columns to keep — discard deprecated/redundant/fielder position fields
KEEP_COLS = [
    "game_date", "game_pk", "game_year", "game_type",
    "batter", "pitcher", "player_name", "stand", "p_throws",
    "home_team", "away_team", "inning", "inning_topbot",
    "at_bat_number", "pitch_number", "balls", "strikes", "outs_when_up",
    "pitch_type", "pitch_name", "release_speed", "release_spin_rate",
    "release_extension", "effective_speed",
    "plate_x", "plate_z", "zone",
    "pfx_x", "pfx_z",
    "description", "events", "bb_type", "hit_location",
    "launch_speed", "launch_angle", "hit_distance_sc",
    "hc_x", "hc_y",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "woba_value", "babip_value", "iso_value",
    "delta_run_exp", "delta_home_win_exp",
    "bat_speed", "swing_length", "attack_angle", "swing_path_tilt",
    "n_thruorder_pitcher", "n_priorpa_thisgame_player_at_bat",
    "sz_top", "sz_bot",
]


def load_fetch_log():
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH, parse_dates=["date"])
    return pd.DataFrame(columns=["date", "rows", "status", "fetched_at"])


def mark_fetched(date, rows, status):
    log = load_fetch_log()
    new_row = pd.DataFrame([{
        "date":       str(date),
        "rows":       rows,
        "status":     status,
        "fetched_at": datetime.datetime.now().isoformat(),
    }])
    # Replace existing entry for this date if present
    log = log[log["date"].astype(str) != str(date)]
    log = pd.concat([log, new_row], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)


def already_fetched(date, log_df):
    date_str = str(date)
    match = log_df[log_df["date"].astype(str) == date_str]
    if match.empty:
        return False
    row = match.iloc[-1]
    # Only treat as already fetched if we actually got rows.
    # 0-row "ok" entries may be 5am runs before Savant publishes data (~9am ET).
    return row["status"] == "ok" and int(row["rows"]) > 0


def month_path(date):
    return os.path.join(OUT_DIR, f"statcast_{date.year}_{date.month:02d}.csv")


def fetch_date(date):
    """Fetch all pitches for a single calendar date. Returns DataFrame or None."""
    date_str  = date.strftime("%Y-%m-%d")
    next_str  = (date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "game_date_gt": date_str,
        "game_date_lt": next_str,
        "player_type":  "batter",
        "type":         "details",
        "hfSea":        f"{date.year}|",
        "hfGT":         "R|",   # regular season only
        "all":          "true",
    }

    try:
        r = requests.get(SAVANT_URL, params=params, timeout=60)
        r.raise_for_status()

        # Savant returns header-only (1 line) if no data
        lines = r.text.strip().split("\n")
        if len(lines) <= 1:
            print(f"  {date_str}: no data (off-day or pre-season)")
            mark_fetched(date, 0, "ok")
            return None

        from io import StringIO
        df = pd.read_csv(StringIO(r.text), low_memory=False)

        if df.empty:
            mark_fetched(date, 0, "ok")
            return None

        # Keep only useful columns (intersect with what's available)
        cols = [c for c in KEEP_COLS if c in df.columns]
        df = df[cols]

        # Normalise game_date
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.date.astype(str)

        return df

    except Exception as e:
        print(f"  {date_str}: ERROR - {e}")
        mark_fetched(date, 0, f"error: {e}")
        return None


def append_to_monthly(df, date):
    """Append rows to the correct monthly CSV."""
    path = month_path(date)
    if os.path.exists(path):
        existing = pd.read_csv(path, low_memory=False)
        # Remove any existing rows for this date (idempotent re-fetch)
        date_str = str(date)
        existing = existing[existing["game_date"].astype(str) != date_str]
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    combined.to_csv(path, index=False)
    return len(df)


def get_missing_dates(start, end):
    """Return list of dates in [start, end] not yet successfully fetched."""
    log = load_fetch_log()
    missing = []
    d = start
    while d <= end:
        if not already_fetched(d, log):
            missing.append(d)
        d += datetime.timedelta(days=1)
    return missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true",
                        help="Fetch all missing dates from season start to yesterday")
    parser.add_argument("--date", type=str, default=None,
                        help="Fetch a specific date (YYYY-MM-DD). Default: yesterday.")
    parser.add_argument("--start", type=str, default=None,
                        help="Backfill start date override (YYYY-MM-DD)")
    args = parser.parse_args()

    yesterday = datetime.date.today() - datetime.timedelta(days=1)

    if args.backfill:
        start = datetime.date.fromisoformat(args.start) if args.start else SEASON_START
        dates = get_missing_dates(start, yesterday)
        print(f"[fetchStatcastDaily] backfill mode: {len(dates)} missing dates "
              f"({start} to {yesterday})")
    elif args.date:
        dates = [datetime.date.fromisoformat(args.date)]
        print(f"[fetchStatcastDaily] single date: {args.date}")
    else:
        dates = [yesterday]
        print(f"[fetchStatcastDaily] daily mode: {yesterday}")

    total_rows = 0
    for i, date in enumerate(dates):
        print(f"  [{i+1}/{len(dates)}] fetching {date}...", end=" ", flush=True)
        df = fetch_date(date)
        if df is not None and not df.empty:
            n = append_to_monthly(df, date)
            mark_fetched(date, n, "ok")
            total_rows += n
            print(f"{n} pitches")
        else:
            print("0 / skipped")

        # Polite rate limiting — Savant allows ~1 req/sec sustained
        if i < len(dates) - 1:
            time.sleep(2.0)

    print(f"\n[fetchStatcastDaily] done. Total rows appended: {total_rows:,}")

    # Summary of what's on disk
    files = sorted(f for f in os.listdir(OUT_DIR) if f.startswith("statcast_") and f.endswith(".csv"))
    for f in files:
        path = os.path.join(OUT_DIR, f)
        size_mb = os.path.getsize(path) / 1e6
        row_count = sum(1 for _ in open(path)) - 1
        print(f"  {f}: {row_count:,} rows, {size_mb:.1f}MB")


if __name__ == "__main__":
    main()
