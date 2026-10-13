#!/usr/bin/env python3
"""
buildQuoteFeatures.py
======================
Aggregates per-minute microprice/quote-imbalance data into daily
microstructure features for regime-classification use (NOT the main
SPY direct-prediction feature set -- order-flow/CVD was already tried there
and failed as a next-day-return predictor, see buildOrderFlowFeatures.py's
docstring and buildSpyFeatures.py section 13's removal note, June 2026).

This output is consumed only by exploreChopSubRegimes.py as candidate
sub-regime splitter variables. It is deliberately kept OUT of
spy_features.csv / buildSpyFeatures.py.

Input:
  outputs/markets/quotes/SPY_{YEAR}_micro.csv
    Columns: date (TZ-aware ET, minute precision), microprice_mean, mid_mean,
             micro_mid_dev, imbalance_mean, spread_mean, spread_bps_mean,
             quote_count

Output:
  outputs/features/markets/spy_quote_features.csv
    One row per trading day:
      date
      imbalance_session       - session mean imbalance ((bid_sz-ask_sz)/(bid_sz+ask_sz))
      imbalance_first_hour    - mean imbalance 09:30-10:30
      imbalance_last_hour     - mean imbalance 15:00-16:00
      imbalance_direction_flip- 1 if sign(first_hour) != sign(last_hour)
      imbalance_z21           - rolling 21-day z-score of imbalance_session
      micro_mid_dev_session   - session mean (microprice - mid): pure book skew,
                                 signed the same way as imbalance (positive = bid-heavy)
      micro_mid_dev_z21       - rolling 21-day z-score
      spread_bps_session      - session mean spread in bps (liquidity proxy)
      spread_bps_z21          - rolling 21-day z-score (relative liquidity regime)
      quote_intensity         - total quote_count for the day (quoting activity /
                                 market-maker churn proxy)
      quote_intensity_z21     - rolling 21-day z-score

Usage:
  venv/bin/python3 python/buildQuoteFeatures.py
"""

import os
import glob
import datetime as dt
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR  = os.path.expanduser("~/discordBot")
QUOTE_DIR = os.path.join(BASE_DIR, "outputs", "markets", "quotes")
OUT_DIR   = os.path.join(BASE_DIR, "outputs", "features", "markets")
OUT_PATH  = os.path.join(OUT_DIR, "spy_quote_features.csv")

ET = "America/New_York"
Z_WINDOW = 21


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    s   = series.fillna(0)
    mu  = s.rolling(window, min_periods=1).mean()
    std = s.rolling(window, min_periods=1).std()
    return (s - mu) / std.replace(0, np.nan)


def load_quotes() -> pd.DataFrame:
    pattern = os.path.join(QUOTE_DIR, "SPY_*_micro.csv")
    files   = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No quote CSV files found at: {pattern}\n"
            "Run python/fetchQuotesDaily.py --backfill first."
        )
    parts = []
    for fp in files:
        tmp = pd.read_csv(fp)
        parts.append(tmp)
        print(f"  Loaded {os.path.basename(fp)}: {len(tmp):,} rows")
    df = pd.concat(parts, ignore_index=True)
    df["ts"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("ts").reset_index(drop=True)
    df["trade_date"] = df["ts"].dt.date
    print(f"  Total minute rows: {len(df):,}")
    return df


def compute_day_features(day_df: pd.DataFrame) -> dict:
    result = {}
    if day_df.empty:
        for k in ["imbalance_session", "imbalance_first_hour", "imbalance_last_hour",
                  "imbalance_direction_flip", "micro_mid_dev_session",
                  "spread_bps_session", "quote_intensity"]:
            result[k] = np.nan
        return result

    t = day_df["ts"].dt.time
    first_hr = day_df[t < dt.time(10, 30)]
    last_hr  = day_df[t >= dt.time(15, 0)]

    # Weight by quote_count so busier minutes count proportionally more
    def wmean(sub, col):
        if sub.empty or sub["quote_count"].sum() == 0:
            return np.nan
        return float((sub[col] * sub["quote_count"]).sum() / sub["quote_count"].sum())

    imb_session = wmean(day_df, "imbalance_mean")
    imb_first   = wmean(first_hr, "imbalance_mean")
    imb_last    = wmean(last_hr, "imbalance_mean")

    result["imbalance_session"]    = imb_session
    result["imbalance_first_hour"] = imb_first
    result["imbalance_last_hour"]  = imb_last

    if pd.isna(imb_first) or pd.isna(imb_last) or imb_first == 0 or imb_last == 0:
        result["imbalance_direction_flip"] = np.nan
    else:
        result["imbalance_direction_flip"] = int(np.sign(imb_first) != np.sign(imb_last))

    result["micro_mid_dev_session"] = wmean(day_df, "micro_mid_dev")
    result["spread_bps_session"]    = wmean(day_df, "spread_bps_mean")
    result["quote_intensity"]       = float(day_df["quote_count"].sum())

    return result


def main():
    print("=" * 60)
    print("buildQuoteFeatures.py")
    print("=" * 60)

    os.makedirs(OUT_DIR, exist_ok=True)

    print("\nLoading quote minute bars...")
    q = load_quotes()

    t_col = q["ts"].dt.time
    reg_mask = (t_col >= dt.time(9, 30)) & (t_col < dt.time(16, 0))
    q_reg = q[reg_mask].copy()
    print(f"\n  Regular-session bars: {len(q_reg):,} "
          f"(dropped {len(q) - len(q_reg):,} pre/post-market)")

    trade_dates = sorted(q_reg["trade_date"].unique())
    print(f"\nComputing features for {len(trade_dates)} trading days "
          f"({trade_dates[0]} to {trade_dates[-1]})...")

    rows = []
    for td in trade_dates:
        day_df = q_reg[q_reg["trade_date"] == td].sort_values("ts")
        feats = compute_day_features(day_df)
        feats["date"] = str(td)
        rows.append(feats)

    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    out["imbalance_z21"]       = rolling_zscore(out["imbalance_session"], Z_WINDOW)
    out["micro_mid_dev_z21"]   = rolling_zscore(out["micro_mid_dev_session"], Z_WINDOW)
    out["spread_bps_z21"]      = rolling_zscore(out["spread_bps_session"], Z_WINDOW)
    out["quote_intensity_z21"] = rolling_zscore(out["quote_intensity"], Z_WINDOW)

    FEAT_COLS = [
        "date",
        "imbalance_session", "imbalance_first_hour", "imbalance_last_hour",
        "imbalance_direction_flip", "imbalance_z21",
        "micro_mid_dev_session", "micro_mid_dev_z21",
        "spread_bps_session", "spread_bps_z21",
        "quote_intensity", "quote_intensity_z21",
    ]
    out = out[[c for c in FEAT_COLS if c in out.columns]]
    out.to_csv(OUT_PATH, index=False)

    print(f"\nN rows written : {len(out):,}")
    print(f"Date range     : {out['date'].min()} to {out['date'].max()}")
    print("\nFeature coverage (non-null %):")
    for col in FEAT_COLS[1:]:
        if col in out.columns:
            pct = out[col].notna().mean() * 100
            print(f"  {col:<28s}: {pct:6.1f}%")
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
