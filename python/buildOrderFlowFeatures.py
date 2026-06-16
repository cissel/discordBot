#!/usr/bin/env python3
"""
buildOrderFlowFeatures.py
=========================
Aggregates per-minute CVD (Cumulative Volume Delta) data into 14 daily
order-flow features for the SPY ML model.

Input:
  outputs/markets/orderflow/SPY_{YEAR}_cvd.csv
    Columns: date (TZ-aware ET, minute precision), buy_vol, sell_vol, cvd,
             large_buy_vol, large_sell_vol, large_cvd, trade_count,
             clean_trade_count

  outputs/markets/SPY_max_bars.csv
    Columns: date, open, high, low, close, volume, vw, n

Output:
  outputs/features/markets/spy_orderflow_features.csv
    One row per trading day, columns: date + 14 order-flow features

Features (all from regular session 09:30-16:00, no forward leakage):
  1.  cvd_total           - net CVD for full session (sum buy_vol - sell_vol)
  2.  cvd_normalized      - cvd_total / daily volume (from SPY_max_bars)
  3.  cvd_first_hour      - CVD in first 60 min (09:30-10:30)
  4.  cvd_last_hour       - CVD in last 60 min  (15:00-16:00)
  5.  cvd_direction_flip  - 1 if sign(cvd_first_hour) != sign(cvd_last_hour)
  6.  large_cvd_total     - net large-trade CVD for full session
  7.  large_cvd_ratio     - large_cvd_total / cvd_total (when both nonzero)
  8.  cvd_z21             - z-score of cvd_normalized over rolling 21-day window
  9.  large_cvd_z21       - z-score of large_cvd_total/volume over rolling 21-day
  10. cvd_momentum_ratio  - pearsonr(bar_index, cumulative_cvd_within_session)
  11. cvd_peak_hour       - hour (9-15) of highest abs per-minute CVD bar
  12. buy_intensity       - buy_vol / clean_trade_count (avg buy size per trade)
  13. sell_intensity      - sell_vol / clean_trade_count
  14. intensity_ratio     - buy_intensity / sell_intensity

Usage:
  venv/bin/python3 python/buildOrderFlowFeatures.py
"""

import os
import glob
import warnings
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR    = os.path.expanduser("~/discordBot")
CVD_DIR     = os.path.join(BASE_DIR, "outputs", "markets", "orderflow")
DAILY_BARS  = os.path.join(BASE_DIR, "outputs", "markets", "SPY_max_bars.csv")
OUT_DIR     = os.path.join(BASE_DIR, "outputs", "features", "markets")
OUT_PATH    = os.path.join(OUT_DIR, "spy_orderflow_features.csv")

ET          = "America/New_York"

# Regular session boundaries
REG_OPEN    = "09:30"
REG_CLOSE   = "16:00"
FIRST_HOUR_END  = "10:30"   # 9:30 + 60 min
LAST_HOUR_START = "15:00"   # 16:00 - 60 min

# Rolling z-score window
Z_WINDOW    = 21


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Z-score using a rolling window. fillna(0) applied beforehand."""
    s   = series.fillna(0)
    mu  = s.rolling(window, min_periods=1).mean()
    std = s.rolling(window, min_periods=1).std()
    return (s - mu) / std.replace(0, np.nan)


def safe_ratio(num, denom, fill=0.0):
    """Scalar safe division."""
    if pd.isna(denom) or denom == 0:
        return fill
    return float(num) / float(denom)


# ---------------------------------------------------------------------------
# Load input data
# ---------------------------------------------------------------------------

def load_cvd() -> pd.DataFrame:
    """Load and concatenate all yearly CVD CSVs. Returns TZ-aware ET df."""
    pattern = os.path.join(CVD_DIR, "SPY_*_cvd.csv")
    files   = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(
            f"No CVD CSV files found at: {pattern}\n"
            "Run the order-flow fetch first to populate "
            "outputs/markets/orderflow/SPY_{YEAR}_cvd.csv"
        )

    parts = []
    for fp in files:
        tmp = pd.read_csv(fp)
        parts.append(tmp)
        print(f"  Loaded {os.path.basename(fp)}: {len(tmp):,} rows")

    df = pd.concat(parts, ignore_index=True)

    # Normalise timestamp column
    ts_col = "date" if "date" in df.columns else "timestamp"
    df = df.rename(columns={ts_col: "ts"})

    # Parse as UTC-aware then convert to ET
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("ts").reset_index(drop=True)

    # Derive calendar date (ET)
    df["trade_date"] = df["ts"].dt.date

    print(f"  Total minute rows: {len(df):,}")
    return df


def load_daily_volume() -> pd.Series:
    """Load SPY daily bars; return Series[date -> volume]."""
    bars = pd.read_csv(DAILY_BARS, parse_dates=["date"])
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars = bars.sort_values("date")
    vol = bars.set_index("date")["volume"]
    print(f"  Daily bars: {len(bars):,} rows  "
          f"({bars['date'].min()} to {bars['date'].max()})")
    return vol


# ---------------------------------------------------------------------------
# Per-day feature computation
# ---------------------------------------------------------------------------

def compute_day_features(day_df: pd.DataFrame) -> dict:
    """
    Compute all 14 order-flow features for a single trading day.

    Parameters
    ----------
    day_df : DataFrame of per-minute CVD bars for one trade_date,
             already filtered to the regular session (09:30-16:00),
             sorted by ts ascending.
    """
    result = {}

    if day_df.empty:
        for k in [
            "cvd_total", "cvd_normalized", "cvd_first_hour", "cvd_last_hour",
            "cvd_direction_flip", "large_cvd_total", "large_cvd_ratio",
            "cvd_momentum_ratio", "cvd_peak_hour",
            "buy_intensity", "sell_intensity", "intensity_ratio",
        ]:
            result[k] = np.nan
        return result

    t = day_df["ts"].dt.time

    # ── 1. cvd_total ─────────────────────────────────────────────────────────
    buy_vol  = day_df["buy_vol"].sum()
    sell_vol = day_df["sell_vol"].sum()
    cvd_total = float(buy_vol - sell_vol)
    result["cvd_total"] = cvd_total

    # ── 2. cvd_normalized — filled later after joining daily volume ───────────
    # Store total_vol as placeholder; normalisation applied after daily join.
    result["_total_vol_placeholder"] = np.nan  # filled in main()

    # ── 3 & 4. cvd_first_hour / cvd_last_hour ────────────────────────────────
    import datetime as dt

    first_hr_mask = day_df["ts"].dt.time < dt.time(10, 30)
    last_hr_mask  = day_df["ts"].dt.time >= dt.time(15, 0)

    first_hr_df = day_df[first_hr_mask]
    last_hr_df  = day_df[last_hr_mask]

    if not first_hr_df.empty:
        cvd_first = float(
            first_hr_df["buy_vol"].sum() - first_hr_df["sell_vol"].sum()
        )
    else:
        cvd_first = np.nan
    result["cvd_first_hour"] = cvd_first

    if not last_hr_df.empty:
        cvd_last = float(
            last_hr_df["buy_vol"].sum() - last_hr_df["sell_vol"].sum()
        )
    else:
        cvd_last = np.nan
    result["cvd_last_hour"] = cvd_last

    # ── 5. cvd_direction_flip ─────────────────────────────────────────────────
    if pd.isna(cvd_first) or pd.isna(cvd_last) or cvd_first == 0 or cvd_last == 0:
        result["cvd_direction_flip"] = np.nan
    else:
        result["cvd_direction_flip"] = int(np.sign(cvd_first) != np.sign(cvd_last))

    # ── 6. large_cvd_total ───────────────────────────────────────────────────
    large_cvd_total = float(
        day_df["large_buy_vol"].sum() - day_df["large_sell_vol"].sum()
    )
    result["large_cvd_total"] = large_cvd_total

    # ── 7. large_cvd_ratio ───────────────────────────────────────────────────
    if cvd_total != 0 and large_cvd_total != 0:
        result["large_cvd_ratio"] = large_cvd_total / cvd_total
    else:
        result["large_cvd_ratio"] = 0.0

    # ── 10. cvd_momentum_ratio ───────────────────────────────────────────────
    # pearsonr(bar_index, cumulative intra-session CVD)
    n_bars = len(day_df)
    if n_bars >= 5:
        cum_cvd = (day_df["buy_vol"] - day_df["sell_vol"]).cumsum().values
        idx_arr = np.arange(n_bars, dtype=float)
        try:
            r, _ = pearsonr(idx_arr, cum_cvd)
            result["cvd_momentum_ratio"] = float(r) if not np.isnan(r) else np.nan
        except Exception:
            result["cvd_momentum_ratio"] = np.nan
    else:
        result["cvd_momentum_ratio"] = np.nan

    # ── 11. cvd_peak_hour ────────────────────────────────────────────────────
    # Hour (9-15) in which the abs per-minute CVD bar was largest
    minute_cvd = (day_df["buy_vol"] - day_df["sell_vol"]).abs()
    if not minute_cvd.empty and minute_cvd.max() > 0:
        peak_idx  = minute_cvd.idxmax()
        peak_hour = day_df.loc[peak_idx, "ts"].hour
        result["cvd_peak_hour"] = int(peak_hour)
    else:
        result["cvd_peak_hour"] = np.nan

    # ── 12 & 13. buy_intensity / sell_intensity ───────────────────────────────
    clean_trades = day_df["clean_trade_count"].sum()
    if clean_trades > 0:
        result["buy_intensity"]  = float(buy_vol)  / float(clean_trades)
        result["sell_intensity"] = float(sell_vol) / float(clean_trades)
    else:
        result["buy_intensity"]  = np.nan
        result["sell_intensity"] = np.nan

    # ── 14. intensity_ratio ──────────────────────────────────────────────────
    si = result.get("sell_intensity", np.nan)
    bi = result.get("buy_intensity",  np.nan)
    if pd.isna(si) or si == 0:
        result["intensity_ratio"] = np.nan
    else:
        result["intensity_ratio"] = float(bi) / float(si)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("buildOrderFlowFeatures.py")
    print("=" * 60)

    os.makedirs(OUT_DIR, exist_ok=True)

    # ── Load inputs ──────────────────────────────────────────────────────────
    print("\nLoading CVD minute bars...")
    cvd = load_cvd()

    print("\nLoading daily volume reference...")
    daily_vol = load_daily_volume()

    # ── Filter to regular session ─────────────────────────────────────────────
    import datetime as dt
    t_col = cvd["ts"].dt.time
    reg_mask = (t_col >= dt.time(9, 30)) & (t_col < dt.time(16, 0))
    cvd_reg  = cvd[reg_mask].copy()
    print(f"\n  Regular-session bars: {len(cvd_reg):,} "
          f"(dropped {len(cvd) - len(cvd_reg):,} pre/post-market)")

    # ── Compute per-day features ──────────────────────────────────────────────
    trade_dates = sorted(cvd_reg["trade_date"].unique())
    print(f"\nComputing features for {len(trade_dates)} trading days "
          f"({trade_dates[0]} to {trade_dates[-1]})...")

    rows = []
    for td in trade_dates:
        day_df = cvd_reg[cvd_reg["trade_date"] == td].sort_values("ts")
        feats  = compute_day_features(day_df)
        feats["date"] = str(td)
        rows.append(feats)

    out = pd.DataFrame(rows)
    out = out.sort_values("date").reset_index(drop=True)
    out["date_key"] = pd.to_datetime(out["date"]).dt.date

    # ── Attach daily volume and compute volume-dependent features ─────────────
    out["_daily_vol"] = out["date_key"].map(daily_vol)

    # 2. cvd_normalized
    out["cvd_normalized"] = out.apply(
        lambda r: safe_ratio(r["cvd_total"], r["_daily_vol"], fill=np.nan),
        axis=1,
    )

    # 9. large_cvd_normalized (for z-score)
    out["_large_cvd_norm"] = out.apply(
        lambda r: safe_ratio(r["large_cvd_total"], r["_daily_vol"], fill=np.nan),
        axis=1,
    )

    # Drop helper columns
    out = out.drop(columns=["_total_vol_placeholder", "_daily_vol",
                             "date_key"], errors="ignore")

    # ── Rolling z-scores (computed on sorted daily series) ────────────────────
    out = out.sort_values("date").reset_index(drop=True)

    # 8. cvd_z21
    out["cvd_z21"] = rolling_zscore(out["cvd_normalized"], Z_WINDOW)

    # 9. large_cvd_z21
    out["large_cvd_z21"] = rolling_zscore(out["_large_cvd_norm"], Z_WINDOW)
    out = out.drop(columns=["_large_cvd_norm"], errors="ignore")

    # ── Finalise column order ─────────────────────────────────────────────────
    FEAT_COLS = [
        "date",
        "cvd_total",
        "cvd_normalized",
        "cvd_first_hour",
        "cvd_last_hour",
        "cvd_direction_flip",
        "large_cvd_total",
        "large_cvd_ratio",
        "cvd_z21",
        "large_cvd_z21",
        "cvd_momentum_ratio",
        "cvd_peak_hour",
        "buy_intensity",
        "sell_intensity",
        "intensity_ratio",
    ]
    out = out[[c for c in FEAT_COLS if c in out.columns]]

    # ── Save ──────────────────────────────────────────────────────────────────
    out.to_csv(OUT_PATH, index=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    n_rows   = len(out)
    date_min = out["date"].min()
    date_max = out["date"].max()

    print(f"\nN rows written : {n_rows:,}")
    print(f"Date range     : {date_min} to {date_max}")
    print("\nFeature coverage (non-null %):")
    for col in FEAT_COLS[1:]:
        if col in out.columns:
            pct = out[col].notna().mean() * 100
            print(f"  {col:<25s}: {pct:6.1f}%")

    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
