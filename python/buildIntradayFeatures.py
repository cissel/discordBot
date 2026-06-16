#!/usr/bin/env python3
"""
buildIntradayFeatures.py
========================
Reads yearly SPY 1-minute intraday CSVs and aggregates to ONE ROW PER TRADING
DAY with 12 intraday features.

Input files:
  outputs/markets/intraday/SPY_{YEAR}_1min.csv  (yearly, TZ-aware timestamps)
  outputs/markets/SPY_max_bars.csv              (daily bars, for prev-day close)

Output:
  outputs/features/markets/spy_intraday_features.csv

Columns: date (YYYY-MM-DD), first_hour_ret, last_hour_ret, am_range, pm_range,
         gap_fill_flag, vwap_dev_am, open_drive_flag, vol_am_pct,
         late_reversal_flag, premarket_ret, premarket_vol_ratio, overnight_gap

Usage:
  venv/bin/python3 python/buildIntradayFeatures.py
"""

import os
import glob
import warnings
import datetime
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR     = os.path.expanduser("~/discordBot")
INTRADAY_DIR = os.path.join(BASE_DIR, "outputs", "markets", "intraday")
DAILY_BARS   = os.path.join(BASE_DIR, "outputs", "markets", "SPY_max_bars.csv")
OUT_PATH     = os.path.join(BASE_DIR, "outputs", "features", "markets",
                             "spy_intraday_features.csv")

ET = "America/New_York"

# Regular session boundaries (ET)
ROPEN  = datetime.time(9,  30)
RCLOSE = datetime.time(16,  0)
PRE_OPEN = datetime.time(4,   0)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assign_session(ts: pd.Series) -> pd.Series:
    """
    Assign session label from a TZ-aware timestamp Series (already in ET).
    pre      : 04:00 <= t < 09:30
    regular  : 09:30 <= t < 16:00
    post     : 16:00 <= t < 20:00
    """
    t = ts.dt.time
    cond = [
        (t >= PRE_OPEN) & (t < ROPEN),
        (t >= ROPEN)    & (t < RCLOSE),
    ]
    return np.select(cond, ["pre", "regular"], default="post")


def safe_div(num, denom, fill=np.nan):
    try:
        if denom == 0 or pd.isna(denom):
            return fill
        return num / denom
    except Exception:
        return fill


# ---------------------------------------------------------------------------
# Load daily bars (for previous-day close lookups)
# ---------------------------------------------------------------------------

def load_daily_bars() -> pd.DataFrame:
    df = pd.read_csv(DAILY_BARS, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    # Keep only cols we need
    df = df[["date", "close"]].rename(columns={"close": "daily_close"})
    return df


# ---------------------------------------------------------------------------
# Load & concat all yearly intraday CSVs
# ---------------------------------------------------------------------------

def load_intraday() -> pd.DataFrame:
    pattern = os.path.join(INTRADAY_DIR, "SPY_*_1min.csv")
    files   = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(
            f"No intraday CSV files found at {pattern}\n"
            "Run the intraday backfill fetch first to populate "
            "outputs/markets/intraday/SPY_{{YEAR}}_1min.csv"
        )

    parts = []
    for fp in files:
        tmp = pd.read_csv(fp)
        parts.append(tmp)
        print(f"  Loaded {os.path.basename(fp)}: {len(tmp):,} rows")

    df = pd.concat(parts, ignore_index=True)

    # Normalise timestamp column (may be 'date' or 'timestamp')
    ts_col = "date" if "date" in df.columns else "timestamp"
    df = df.rename(columns={ts_col: "ts"})

    # Parse as UTC then convert to ET
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("ts").reset_index(drop=True)

    # Add session label if not already present
    if "session" not in df.columns:
        df["session"] = assign_session(df["ts"])

    # Trading date (date of the regular session that day — use calendar date in ET)
    df["trade_date"] = df["ts"].dt.normalize().dt.tz_localize(None).dt.date

    return df


# ---------------------------------------------------------------------------
# Feature computation per day
# ---------------------------------------------------------------------------

def compute_day_features(day_df: pd.DataFrame, prev_close: float) -> dict:
    """
    Compute all 12 intraday features for a single trading day.
    day_df   : all bars for this trade_date (ts already in ET, session labelled)
    prev_close: previous regular-session close from SPY_max_bars.csv
    """
    reg   = day_df[day_df["session"] == "regular"].sort_values("ts")
    pre   = day_df[day_df["session"] == "pre"].sort_values("ts")

    result = {}

    # ── REGULAR SESSION features ────────────────────────────────────────────

    if reg.empty:
        for k in ["first_hour_ret", "last_hour_ret", "am_range", "pm_range",
                  "gap_fill_flag", "vwap_dev_am", "open_drive_flag",
                  "vol_am_pct", "late_reversal_flag"]:
            result[k] = np.nan
    else:
        t = reg["ts"].dt.time

        session_open_price  = float(reg["open"].iloc[0])
        session_close_price = float(reg["close"].iloc[-1])

        # -- first_hour_ret: 09:30 open to 10:30 close ----------------------
        first_hr = reg[t <= datetime.time(10, 30)]
        if not first_hr.empty:
            c1030 = float(first_hr["close"].iloc[-1])
            result["first_hour_ret"] = safe_div(
                c1030 - session_open_price, session_open_price)
        else:
            result["first_hour_ret"] = np.nan

        # -- last_hour_ret: 15:00 open to 16:00 close -----------------------
        last_hr = reg[t >= datetime.time(15, 0)]
        if not last_hr.empty:
            o1500 = float(last_hr["open"].iloc[0])
            c1600 = float(last_hr["close"].iloc[-1])
            result["last_hour_ret"] = safe_div(c1600 - o1500, o1500)
        else:
            result["last_hour_ret"] = np.nan

        # -- am_range: high-low spread in 09:30-12:00, norm by session open -
        am_bars = reg[t < datetime.time(12, 0)]
        if not am_bars.empty and session_open_price > 0:
            result["am_range"] = safe_div(
                am_bars["high"].max() - am_bars["low"].min(),
                session_open_price)
        else:
            result["am_range"] = np.nan

        # -- pm_range: high-low spread in 12:00-16:00 -----------------------
        pm_bars = reg[(t >= datetime.time(12, 0)) & (t < datetime.time(16, 0))]
        if not pm_bars.empty and session_open_price > 0:
            result["pm_range"] = safe_div(
                pm_bars["high"].max() - pm_bars["low"].min(),
                session_open_price)
        else:
            result["pm_range"] = np.nan

        # -- gap_fill_flag: gap vs prev close, then crossed back through it --
        result["gap_fill_flag"] = 0
        if not pd.isna(prev_close) and prev_close > 0:
            gap = session_open_price - prev_close
            if abs(gap) > 0:
                # Gapped up -> price must touch <= prev_close during session
                # Gapped down -> price must touch >= prev_close during session
                lows  = reg["low"].values
                highs = reg["high"].values
                if gap > 0 and np.any(lows <= prev_close):
                    result["gap_fill_flag"] = 1
                elif gap < 0 and np.any(highs >= prev_close):
                    result["gap_fill_flag"] = 1

        # -- vwap_dev_am: (mean close 09:30-12:00 - session_vwap) / vwap ----
        # Session VWAP: volume-weighted average using bar vwap (col "vw")
        reg_vol = reg["volume"].astype(float)
        total_vol = reg_vol.sum()
        if total_vol > 0 and "vw" in reg.columns:
            session_vwap = (reg["vw"].astype(float) * reg_vol).sum() / total_vol
        else:
            session_vwap = reg["close"].mean()

        if not am_bars.empty and session_vwap > 0:
            result["vwap_dev_am"] = safe_div(
                am_bars["close"].mean() - session_vwap, session_vwap)
        else:
            result["vwap_dev_am"] = np.nan

        # -- open_drive_flag: first 15-min direction == full session direction
        first_15 = reg[t <= datetime.time(9, 45)]
        if not first_15.empty and session_open_price > 0:
            first_15_ret    = safe_div(
                float(first_15["close"].iloc[-1]) - session_open_price,
                session_open_price)
            full_session_ret = safe_div(
                session_close_price - session_open_price, session_open_price)
            if not (pd.isna(first_15_ret) or pd.isna(full_session_ret)):
                same_dir = (first_15_ret > 0 and full_session_ret > 0) or \
                           (first_15_ret < 0 and full_session_ret < 0)
                result["open_drive_flag"] = int(same_dir)
            else:
                result["open_drive_flag"] = np.nan
        else:
            result["open_drive_flag"] = np.nan

        # -- vol_am_pct: fraction of session volume in first 90 min ---------
        am90_bars = reg[t < datetime.time(11, 0)]
        if total_vol > 0:
            result["vol_am_pct"] = safe_div(
                am90_bars["volume"].sum(), total_vol)
        else:
            result["vol_am_pct"] = np.nan

        # -- late_reversal_flag: last_hour opposite to first_hour, >0.3% ----
        fhr = result.get("first_hour_ret", np.nan)
        lhr = result.get("last_hour_ret",  np.nan)
        if not (pd.isna(fhr) or pd.isna(lhr)):
            opposite = (fhr > 0 and lhr < 0) or (fhr < 0 and lhr > 0)
            result["late_reversal_flag"] = int(
                opposite and abs(lhr) > 0.003)
        else:
            result["late_reversal_flag"] = np.nan

    # ── PRE-MARKET features ─────────────────────────────────────────────────

    if pre.empty:
        result["premarket_ret"]       = np.nan
        result["premarket_vol_ratio"] = np.nan
    else:
        pm_open_price  = float(pre["open"].iloc[0])
        pm_close_price = float(pre["close"].iloc[-1])
        result["premarket_ret"] = safe_div(
            pm_close_price - pm_open_price, pm_open_price)

        reg_vol_total = reg["volume"].sum() if not reg.empty else 0
        pre_vol_total = pre["volume"].sum()
        result["premarket_vol_ratio"] = safe_div(
            float(pre_vol_total), float(reg_vol_total))

    # -- overnight_gap: (first regular open - prev day close) / prev day close
    if not reg.empty and not pd.isna(prev_close) and prev_close > 0:
        result["overnight_gap"] = safe_div(
            session_open_price - prev_close, prev_close)
    else:
        result["overnight_gap"] = np.nan

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("buildIntradayFeatures.py")
    print("=" * 60)

    # Load previous-day close reference from daily bars
    print("\nLoading daily bars for prev-close reference...")
    daily = load_daily_bars()
    # Build a lookup: trade_date -> previous day close
    daily = daily.sort_values("date")
    daily["prev_close"] = daily["daily_close"].shift(1)
    prev_close_map = dict(zip(
        daily["date"].dt.date,
        daily["prev_close"]
    ))
    print(f"  Daily bars: {len(daily):,} rows  "
          f"({daily['date'].min().date()} to {daily['date'].max().date()})")

    # Load all intraday bars
    print("\nLoading intraday 1-min bars...")
    intraday = load_intraday()
    print(f"  Total 1-min bars: {len(intraday):,}")
    trade_dates = sorted(intraday["trade_date"].unique())
    print(f"  Trading days: {len(trade_dates)}  "
          f"({trade_dates[0]} to {trade_dates[-1]})")

    # Compute features day by day
    print("\nComputing features per day...")
    rows = []
    for td in trade_dates:
        day_df     = intraday[intraday["trade_date"] == td]
        prev_close = prev_close_map.get(td, np.nan)
        feats      = compute_day_features(day_df, prev_close)
        feats["date"] = str(td)
        rows.append(feats)

    # Build output DataFrame
    FEAT_COLS = [
        "date",
        "first_hour_ret", "last_hour_ret",
        "am_range", "pm_range",
        "gap_fill_flag", "vwap_dev_am",
        "open_drive_flag", "vol_am_pct",
        "late_reversal_flag",
        "premarket_ret", "premarket_vol_ratio",
        "overnight_gap",
    ]

    out = pd.DataFrame(rows)[FEAT_COLS]
    out = out.sort_values("date").reset_index(drop=True)

    # Save
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out.to_csv(OUT_PATH, index=False)

    # ── Summary ─────────────────────────────────────────────────────────────
    n_rows   = len(out)
    date_min = out["date"].min()
    date_max = out["date"].max()

    print(f"\nN rows written : {n_rows:,}")
    print(f"Date range     : {date_min} to {date_max}")
    print("\nFeature coverage (non-null %):")
    for col in FEAT_COLS[1:]:
        pct = out[col].notna().mean() * 100
        print(f"  {col:<25s}: {pct:6.1f}%")
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
