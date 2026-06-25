#!/usr/bin/env python3
"""
fetchMacroSentiment.py
Fetches macro/sentiment series from FRED (no API key required):
  - ICSA   : Initial Jobless Claims (weekly)
  - CCSA   : Continuing Claims     (weekly)
  - UMCSENT: U. Michigan Consumer Sentiment (monthly)
  - CSCICP03USM665S: OECD Consumer Confidence (monthly)

Publication lags applied (data visible to model):
  ICSA / CCSA : +4 trading days
  UMCSENT     : +21 calendar days
  CSCICP03    : +21 calendar days

Outputs:
  outputs/markets/cache/macro_sentiment_daily.csv
"""

import io
import os
import sys
import warnings
import requests
import numpy as np
import pandas as pd
from datetime import date

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUT_DIR     = os.path.join(PROJECT_DIR, "outputs", "markets", "cache")
OUT_FILE    = os.path.join(OUT_DIR, "macro_sentiment_daily.csv")
os.makedirs(OUT_DIR, exist_ok=True)

# ── constants ─────────────────────────────────────────────────────────────────
DATE_START = "2015-01-01"
DATE_END   = date.today().isoformat()

FRED_BASE  = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

FRED_SERIES = {
    "ICSA":               "icsa",
    "CCSA":               "ccsa",
    "UMCSENT":            "umcsent",
    "CSCICP03USM665S":    "cscicp03",
}

# Publication lag config  (type, n)
# type "B" = business days, type "C" = calendar days
LAGS = {
    "icsa":     ("B", 4),
    "ccsa":     ("B", 4),
    "umcsent":  ("C", 21),
    "cscicp03": ("C", 21),
}


# ── helpers ───────────────────────────────────────────────────────────────────
def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score; NaN-safe."""
    m = series.rolling(window, min_periods=window // 2).mean()
    s = series.rolling(window, min_periods=window // 2).std(ddof=1)
    return (series - m) / s.replace(0, np.nan)


def shift_lag(index: pd.DatetimeIndex, lag_type: str, n: int) -> pd.DatetimeIndex:
    """Shift a DatetimeIndex by n business days ('B') or calendar days ('C')."""
    if lag_type == "B":
        offset = pd.tseries.offsets.BDay(n)
        return pd.DatetimeIndex([d + offset for d in index])
    else:  # calendar
        return index + pd.Timedelta(days=n)


# ── FRED fetcher ──────────────────────────────────────────────────────────────
def fetch_fred(series_id: str) -> pd.DataFrame:
    """
    Fetch a FRED series via the public CSV endpoint.
    Returns DataFrame with columns [date, value].
    '.' values (missing) are dropped.
    """
    url = FRED_BASE.format(series_id)
    print(f"  [FRED] Fetching {series_id}…")
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    df = pd.read_csv(io.StringIO(r.text))
    # FRED CSV columns: observation_date, <series_id>
    date_col  = df.columns[0]
    value_col = df.columns[1]

    df = df.rename(columns={date_col: "date", value_col: "value"})
    df["date"]  = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["value"] != "."].copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)

    print(f"  [FRED] {series_id}: {len(df)} rows "
          f"({df['date'].min().date()} → {df['date'].max().date()})")
    return df


# ── per-series feature engineering ───────────────────────────────────────────
def engineer_icsa(df: pd.DataFrame) -> pd.DataFrame:
    """
    52-week rolling z-score and 4-week change.
    Input is weekly; features computed on weekly cadence then daily-resampled.
    """
    df = df.copy()
    df["icsa_z52"]    = rolling_zscore(df["value"], 52)
    df["icsa_chg_4w"] = df["value"].diff(4)
    return df.rename(columns={"value": "icsa"})


def engineer_ccsa(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ccsa_z52"]    = rolling_zscore(df["value"], 52)
    df["ccsa_chg_4w"] = df["value"].diff(4)
    return df.rename(columns={"value": "ccsa"})


def engineer_umcsent(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["umcsent_z12"]    = rolling_zscore(df["value"], 12)
    df["umcsent_chg_3m"] = df["value"].diff(3)
    return df.rename(columns={"value": "umcsent"})


def engineer_cscicp03(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cscicp03_z12"]    = rolling_zscore(df["value"], 12)
    df["cscicp03_chg_3m"] = df["value"].diff(3)
    return df.rename(columns={"value": "cscicp03"})


ENGINEERS = {
    "icsa":     engineer_icsa,
    "ccsa":     engineer_ccsa,
    "umcsent":  engineer_umcsent,
    "cscicp03": engineer_cscicp03,
}


# ── daily resampler with lag ──────────────────────────────────────────────────
def to_daily_lagged(df: pd.DataFrame, name: str,
                    date_idx: pd.DatetimeIndex) -> pd.DataFrame:
    """
    1. Apply publication lag to the observation dates.
    2. Reindex onto the daily spine.
    3. Forward-fill (data persists until next release).
    """
    lag_type, lag_n = LAGS[name]
    lagged_dates = shift_lag(pd.DatetimeIndex(df["date"]), lag_type, lag_n)

    # Build a copy with lagged index
    feat_cols = [c for c in df.columns if c != "date"]
    tmp = df[feat_cols].copy()
    tmp.index = lagged_dates

    # Deduplicate index (rare edge case with lag collisions)
    tmp = tmp[~tmp.index.duplicated(keep="last")]

    # Reindex onto full daily spine, forward-fill
    daily = tmp.reindex(date_idx).ffill()
    return daily


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print("fetchMacroSentiment.py")
    print(f"  Date range : {DATE_START} → {DATE_END}")
    print(f"  Output     : {OUT_FILE}")
    print(f"{'='*60}\n")

    date_idx = pd.date_range(DATE_START, DATE_END, freq="D")

    daily_frames = []

    for fred_id, name in FRED_SERIES.items():
        try:
            raw  = fetch_fred(fred_id)
            eng  = ENGINEERS[name](raw)
            day  = to_daily_lagged(eng, name, date_idx)
            daily_frames.append(day)
        except Exception as e:
            warnings.warn(f"[{fred_id}] FAILED — filling with NaN. Error: {e}")
            # Determine expected columns from engineer function
            # by running on empty DataFrame
            placeholder_cols = {
                "icsa":     ["icsa", "icsa_z52", "icsa_chg_4w"],
                "ccsa":     ["ccsa", "ccsa_z52", "ccsa_chg_4w"],
                "umcsent":  ["umcsent", "umcsent_z12", "umcsent_chg_3m"],
                "cscicp03": ["cscicp03", "cscicp03_z12", "cscicp03_chg_3m"],
            }
            blank = pd.DataFrame(
                index=date_idx,
                columns=placeholder_cols[name],
                dtype=float,
            )
            daily_frames.append(blank)

    # ── merge all series onto common spine ────────────────────────────────────
    out = pd.concat(daily_frames, axis=1)
    out.index.name = "date"
    out = out.sort_index()

    out.to_csv(OUT_FILE)
    print(f"\n✓ Saved {len(out)} rows × {len(out.columns)} cols → {OUT_FILE}")

    # ── coverage summary ──────────────────────────────────────────────────────
    print("\n── Coverage Summary ──────────────────────────────────────────")
    for col in out.columns:
        valid   = out[col].notna().sum()
        pct     = valid / len(out) * 100
        first   = out[col].first_valid_index()
        last    = out[col].last_valid_index()
        fstr    = first.date() if first is not None else "N/A"
        lstr    = last.date()  if last  is not None else "N/A"
        print(f"  {col:<28}  {valid:5d}/{len(out)} ({pct:5.1f}%)  "
              f"{fstr} → {lstr}")
    print("──────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
