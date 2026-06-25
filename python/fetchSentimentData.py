#!/usr/bin/env python3
"""
fetchSentimentData.py
Fetches:
  1. AAII Investor Sentiment Survey (weekly since 1987)
  2. Crypto Fear & Greed Index (daily since 2018-02-01)

Outputs:
  outputs/markets/cache/sentiment_daily.csv

Columns (daily, forward-filled onto trading-day-shifted spine):
  aaii_bullish, aaii_neutral, aaii_bearish,
  aaii_bull_bear_spread, aaii_bull_z8,
  cfg_value, cfg_z21, cfg_extreme_fear, cfg_greed
"""

import io
import os
import sys
import time
import warnings
import requests
import numpy as np
import pandas as pd
from datetime import date

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUT_DIR     = os.path.join(PROJECT_DIR, "outputs", "markets", "cache")
OUT_FILE    = os.path.join(OUT_DIR, "sentiment_daily.csv")
os.makedirs(OUT_DIR, exist_ok=True)

# ── constants ─────────────────────────────────────────────────────────────────
DATE_START  = "2015-01-01"
DATE_END    = date.today().isoformat()

AAII_REFERER = "https://www.aaii.com/sentimentsurvey/sent_results"
AAII_URL     = "https://www.aaii.com/files/surveys/sentiment.xls"
FNG_URL      = "https://api.alternative.me/fng/?limit=0&format=json&date_format=world"
UA           = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ── helpers ───────────────────────────────────────────────────────────────────
def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score; NaN-safe."""
    m = series.rolling(window, min_periods=window // 2).mean()
    s = series.rolling(window, min_periods=window // 2).std(ddof=1)
    return (series - m) / s.replace(0, np.nan)


def add_trading_days(dates: pd.DatetimeIndex, n: int) -> pd.DatetimeIndex:
    """Shift each date forward by n NYSE business days (approx with B freq)."""
    # Use 'B' (Mon-Fri) as a conservative proxy for trading days
    shifted = []
    bday = pd.tseries.offsets.BDay(n)
    for d in dates:
        shifted.append(d + bday)
    return pd.DatetimeIndex(shifted)


# ── AAII ─────────────────────────────────────────────────────────────────────
def fetch_aaii() -> pd.DataFrame:
    """
    Returns weekly DataFrame with columns:
      date, aaii_bullish, aaii_neutral, aaii_bearish,
      aaii_bull_bear_spread, aaii_bull_z8
    Values are fractions (0.38 = 38 %).
    """
    print("  [AAII] Fetching sentiment survey…")
    session = requests.Session()
    session.headers.update({
        "User-Agent":      UA,
        "Referer":         AAII_REFERER,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    # warm up cookies (Incapsula WAF) — need both cookies set before XLS request
    try:
        session.get(AAII_REFERER, timeout=20)
        time.sleep(3)   # WAF needs time to register the session
    except Exception as e:
        warnings.warn(f"[AAII] cookie warm-up failed: {e}")

    session.headers.update({"Accept": "application/vnd.ms-excel,application/octet-stream,*/*"})
    r = session.get(AAII_URL, timeout=30)
    r.raise_for_status()
    # Verify we got actual XLS (OLE2 magic = d0 cf 11 e0), not HTML error page
    if r.content[:4] != b"\xd0\xcf\x11\xe0":
        raise ValueError(f"AAII returned non-XLS content (len={len(r.content)}, first4={r.content[:4]})")

    df = pd.read_excel(io.BytesIO(r.content), engine="xlrd", header=3)

    # Rename positional columns; keep only what we need
    df = df.rename(columns={
        df.columns[0]: "date",
        df.columns[1]: "aaii_bullish",
        df.columns[2]: "aaii_neutral",
        df.columns[3]: "aaii_bearish",
    })

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["aaii_bullish", "aaii_neutral", "aaii_bearish"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "aaii_bullish"]).copy()
    df = df[["date", "aaii_bullish", "aaii_neutral", "aaii_bearish"]]
    df = df.sort_values("date").reset_index(drop=True)

    # Derived features (computed on weekly data before resampling)
    df["aaii_bull_bear_spread"] = df["aaii_bullish"] - df["aaii_bearish"]
    df["aaii_bull_z8"]          = rolling_zscore(df["aaii_bullish"], 8)

    print(f"  [AAII] {len(df)} weekly rows "
          f"({df['date'].min().date()} → {df['date'].max().date()})")
    return df


def aaii_to_daily(weekly: pd.DataFrame, date_idx: pd.DatetimeIndex) -> pd.DataFrame:
    """
    Forward-fill AAII weekly (Thursday publish) onto daily spine,
    shifted +1 trading day (available Friday).
    """
    cols = ["aaii_bullish", "aaii_neutral", "aaii_bearish",
            "aaii_bull_bear_spread", "aaii_bull_z8"]

    # Shift publish date +1 business day (Thu → Fri)
    pub_dates = add_trading_days(pd.DatetimeIndex(weekly["date"]), 1)
    w = weekly[cols].copy()
    w.index = pub_dates

    # Reindex onto daily spine and forward-fill
    daily = w.reindex(date_idx).ffill()
    return daily


# ── Crypto Fear & Greed ───────────────────────────────────────────────────────
def fetch_fear_greed() -> pd.DataFrame:
    """
    Returns daily DataFrame with columns:
      date, cfg_value, cfg_z21, cfg_extreme_fear, cfg_greed
    """
    print("  [CFG] Fetching Crypto Fear & Greed index…")
    r = requests.get(FNG_URL, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(data)
    df["date"]  = pd.to_datetime(df["timestamp"], format="%d-%m-%Y", errors="coerce")
    df["cfg_value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "cfg_value"]).copy()
    df = df[["date", "cfg_value"]].sort_values("date").reset_index(drop=True)

    df["cfg_z21"]          = rolling_zscore(df["cfg_value"], 21)
    df["cfg_extreme_fear"] = (df["cfg_value"] < 25).astype(float)
    df["cfg_greed"]        = (df["cfg_value"] > 75).astype(float)

    print(f"  [CFG] {len(df)} daily rows "
          f"({df['date'].min().date()} → {df['date'].max().date()})")
    return df


def cfg_to_daily(cfg: pd.DataFrame, date_idx: pd.DatetimeIndex) -> pd.DataFrame:
    cols = ["cfg_value", "cfg_z21", "cfg_extreme_fear", "cfg_greed"]
    daily = cfg.set_index("date")[cols].reindex(date_idx).ffill()
    return daily


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print("fetchSentimentData.py")
    print(f"  Date range : {DATE_START} → {DATE_END}")
    print(f"  Output     : {OUT_FILE}")
    print(f"{'='*60}\n")

    # Full daily date spine (calendar days; we'll keep all, NaN on weekends)
    date_idx = pd.date_range(DATE_START, DATE_END, freq="D")

    frames = {}

    # ── AAII ──────────────────────────────────────────────────────────────────
    try:
        aaii_weekly = fetch_aaii()
        frames["aaii"] = aaii_to_daily(aaii_weekly, date_idx)
    except Exception as e:
        warnings.warn(f"[AAII] FAILED — filling with NaN. Error: {e}")
        frames["aaii"] = pd.DataFrame(
            index=date_idx,
            columns=["aaii_bullish", "aaii_neutral", "aaii_bearish",
                     "aaii_bull_bear_spread", "aaii_bull_z8"],
            dtype=float,
        )

    # ── Crypto Fear & Greed ───────────────────────────────────────────────────
    try:
        cfg_raw = fetch_fear_greed()
        frames["cfg"] = cfg_to_daily(cfg_raw, date_idx)
    except Exception as e:
        warnings.warn(f"[CFG] FAILED — filling with NaN. Error: {e}")
        frames["cfg"] = pd.DataFrame(
            index=date_idx,
            columns=["cfg_value", "cfg_z21", "cfg_extreme_fear", "cfg_greed"],
            dtype=float,
        )

    # ── merge ─────────────────────────────────────────────────────────────────
    out = pd.concat([frames["aaii"], frames["cfg"]], axis=1)
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
