#!/usr/bin/env python3
"""
buildSpyIntradayFeatures.py
=============================
Builds 5-min-bar SPY features for the intraday RL sizing spike (Oct'26).
Resamples 1-min regular-session bars (outputs/markets/intraday/SPY_{YEAR}_1min.csv)
to 5-min bars, computes causal intraday features (no lookahead), and merges
same-day daily regime/vix/gex context from spy_features.csv (that file's
"today" row = features known as of prior close, so it's safe to join as
context available at today's open).

Output: outputs/features/markets/spy_intraday_5min_features.csv
Columns include: date (bar timestamp), trading_date, bar_idx_in_day,
n_bars_in_day, ohlcv, ret_1 (this bar's return), vwap_dev, rolling vol/
momentum features, daily regime one-hot + vix/gex context, and
next_ret_5min (forward 5-min return, NaN on the last bar of each day --
agent is forced flat there, no overnight carry).

Usage:
  venv/bin/python3 python/buildSpyIntradayFeatures.py
"""
import os
import glob
import numpy as np
import pandas as pd

INTRADAY_DIR = os.path.expanduser("~/discordBot/outputs/markets/intraday")
DAILY_FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_intraday_5min_features.csv")

BAR_MIN = 5
REGIMES = ["bull", "bear", "chop"]


def load_1min_regular():
    frames = []
    for path in sorted(glob.glob(os.path.join(INTRADAY_DIR, "SPY_*_1min.csv"))):
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/New_York")
        df = df[df["session"] == "regular"].copy()
        frames.append(df)
    out = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    out = out.drop_duplicates(subset="date")
    return out


def resample_5min(df1m):
    df1m = df1m.set_index("date")
    df1m["trading_date"] = df1m.index.date
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "n": "sum"}
    out = []
    for day, g in df1m.groupby("trading_date"):
        r = g.resample(f"{BAR_MIN}min", label="left", closed="left").agg(agg).dropna(subset=["open"])
        r["trading_date"] = day
        out.append(r)
    res = pd.concat(out).reset_index()
    res["vw"] = res["volume"].replace(0, np.nan)  # placeholder, vwap recomputed below from close*vol if needed
    return res


def build_features(bars):
    bars = bars.sort_values("date").reset_index(drop=True)
    bars["bar_idx_in_day"] = bars.groupby("trading_date").cumcount()
    bars["n_bars_in_day"] = bars.groupby("trading_date")["close"].transform("size")

    bars["ret_1"] = bars["close"].pct_change()
    # zero out first-bar-of-day return (no continuity from prior day's last bar
    # intraday — the agent trades flat overnight, so the "return" of the first
    # bar vs prior day's close is an overnight gap the agent never held through)
    first_mask = bars["bar_idx_in_day"] == 0
    bars.loc[first_mask, "ret_1"] = 0.0

    # session-since-open stats (reset each day)
    bars["cum_ret_since_open"] = bars.groupby("trading_date")["ret_1"].cumsum()
    bars["session_high_so_far"] = bars.groupby("trading_date")["high"].cummax()
    bars["session_low_so_far"] = bars.groupby("trading_date")["low"].cummin()
    bars["dist_from_high_so_far"] = (bars["close"] - bars["session_high_so_far"]) / bars["session_high_so_far"]
    bars["dist_from_low_so_far"] = (bars["close"] - bars["session_low_so_far"]) / bars["session_low_so_far"]

    # session vwap-to-date (volume-weighted, causal within day)
    bars["pv"] = bars["close"] * bars["volume"]
    bars["cum_pv"] = bars.groupby("trading_date")["pv"].cumsum()
    bars["cum_vol"] = bars.groupby("trading_date")["volume"].cumsum()
    bars["session_vwap"] = bars["cum_pv"] / bars["cum_vol"].replace(0, np.nan)
    bars["vwap_dev"] = (bars["close"] - bars["session_vwap"]) / bars["session_vwap"]

    # cross-day rolling features (causal, chronological across the whole series)
    bars["rv_12bar"] = bars["ret_1"].rolling(12).std() * np.sqrt(78 * 252)   # ~1hr realized vol, annualized
    bars["rv_36bar"] = bars["ret_1"].rolling(36).std() * np.sqrt(78 * 252)  # ~3hr
    bars["vol_z_36bar"] = (bars["volume"] - bars["volume"].rolling(36).mean()) / bars["volume"].rolling(36).std()
    bars["mom_12bar"] = bars["close"].pct_change(12)

    # time-of-day (normalized position in session, 0=open .. 1=close)
    bars["tod_frac"] = bars["bar_idx_in_day"] / bars["n_bars_in_day"].clip(lower=1)

    # forward 5-min return (reward target); NaN on last bar of each day
    bars["next_ret_5min"] = bars.groupby("trading_date")["close"].shift(-1) / bars["close"] - 1
    last_mask = bars["bar_idx_in_day"] == (bars["n_bars_in_day"] - 1)
    bars.loc[last_mask, "next_ret_5min"] = np.nan
    bars["is_last_bar"] = last_mask

    return bars


def merge_daily_context(bars, daily_path):
    daily = pd.read_csv(daily_path, parse_dates=["date"])
    keep_cols = ["date", "regime", "vix_level", "vix_z21", "gex_sign", "vix_rv_spread"]
    daily = daily[[c for c in keep_cols if c in daily.columns]].copy()
    daily["trading_date"] = daily["date"].dt.date
    bars["trading_date"] = pd.to_datetime(bars["trading_date"]).dt.date
    merged = bars.merge(daily.drop(columns=["date"]), on="trading_date", how="left")
    merged["regime"] = merged["regime"].ffill()
    for c in ["vix_level", "vix_z21", "gex_sign", "vix_rv_spread"]:
        if c in merged.columns:
            merged[c] = merged[c].ffill()
    for r in REGIMES:
        merged[f"regime_{r}"] = (merged["regime"] == r).astype(float)
    return merged


def main():
    print("Loading 1-min regular-session bars...")
    df1m = load_1min_regular()
    print(f"  {len(df1m)} 1-min bars, {df1m['date'].dt.date.nunique()} trading days, "
          f"{df1m['date'].min()} -> {df1m['date'].max()}")

    print(f"Resampling to {BAR_MIN}-min bars...")
    bars = resample_5min(df1m)
    print(f"  {len(bars)} {BAR_MIN}-min bars")

    print("Computing intraday features...")
    bars = build_features(bars)

    print("Merging daily regime/vix/gex context...")
    merged = merge_daily_context(bars, DAILY_FEAT_PATH)

    core_cols = ["date", "trading_date", "bar_idx_in_day", "n_bars_in_day", "is_last_bar",
                 "open", "high", "low", "close", "volume",
                 "ret_1", "cum_ret_since_open", "dist_from_high_so_far", "dist_from_low_so_far",
                 "vwap_dev", "rv_12bar", "rv_36bar", "vol_z_36bar", "mom_12bar", "tod_frac",
                 "regime", "regime_bull", "regime_bear", "regime_chop",
                 "vix_level", "vix_z21", "gex_sign", "vix_rv_spread",
                 "next_ret_5min"]
    out = merged[[c for c in core_cols if c in merged.columns]].copy()
    n_before = len(out)
    out = out.dropna(subset=["rv_36bar", "vol_z_36bar"]).reset_index(drop=True)
    print(f"  Dropped {n_before - len(out)} warmup rows (rolling windows)")

    out.to_csv(OUT_PATH, index=False)
    print(f"\nSaved {len(out)} rows -> {OUT_PATH}")
    print(f"Date range: {out['trading_date'].min()} -> {out['trading_date'].max()}")
    print(f"Trading days: {out['trading_date'].nunique()}")
    print(f"NaN next_ret_5min (last-bar-of-day, expected): {out['next_ret_5min'].isna().sum()}")


if __name__ == "__main__":
    main()
