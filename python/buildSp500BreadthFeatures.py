#!/usr/bin/env python3
"""
buildSp500BreadthFeatures.py
===============================
Computes DAILY market-breadth features from the S&P 500 constituent bars
fetched by fetchSp500ConstituentBars.py, to test whether aggregate
"how many stocks are participating" signals add incremental predictive
value to the SPY next_dir_1d model beyond what SPY's own price/vol/macro
features already capture (Oct'26, James's idea: "what if we trained on
every ticker in the S&P500 as well").

SURVIVORSHIP BIAS CAVEAT (same as fetchSp500ConstituentBars.py): uses the
CURRENT constituent list applied retroactively. Breadth LEVELS in early
years are likely biased slightly high (today's survivors' own history
looks better on average than the full historical universe would have).
This mostly affects absolute breadth %, less so day-to-day CHANGES in
breadth, which is why several of the features below are changes/z-scores
rather than raw levels.

All features are CAUSAL by construction (each day's value only uses that
day's and prior days' constituent closes -- no forward-looking window).

Feature groups:
  1. Percent above moving averages (50d, 200d) -- classic breadth
  2. Advance/decline: % of constituents up today, 10d cumulative A/D line
  3. New highs - new lows (63d lookback, as % of universe)
  4. Breadth divergence: SPY's own return vs. the CROSS-SECTIONAL MEDIAN
     constituent return that day (a large gap = index being carried by a
     few names -- the historically interesting breadth-divergence signal)
  5. Dispersion: cross-sectional std of daily returns (high = violent
     rotation day; low = everything moves together)

Output: outputs/features/markets/spy_sp500_breadth_features.csv
  columns: date, pct_above_ma50, pct_above_ma200, pct_above_ma50_chg5d,
           advance_pct, ad_line_cum, ad_line_10d_chg,
           new_high_pct_63d, new_low_pct_63d, nh_nl_spread_63d,
           spy_vs_median_ret, cross_sectional_dispersion,
           breadth_thrust_flag

Usage:
  venv/bin/python3 python/buildSp500BreadthFeatures.py
"""
import os
import glob
import numpy as np
import pandas as pd

BARS_DIR = os.path.expanduser("~/discordBot/outputs/markets/sp500_constituent_bars")
SPY_BARS_PATH = os.path.expanduser("~/discordBot/outputs/markets/SPY_max_bars.csv")
OUT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_sp500_breadth_features.csv")

NH_NL_WINDOW = 63   # ~1 quarter, standard new-high/new-low lookback


def load_all_constituents():
    files = sorted(glob.glob(os.path.join(BARS_DIR, "*.csv")))
    print(f"Loading {len(files)} constituent files...")
    frames = []
    for fp in files:
        sym = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, parse_dates=["date"])
            df = df[["date", "close"]].dropna()
            df = df.drop_duplicates(subset="date").sort_values("date")
            df["symbol"] = sym
            frames.append(df)
        except Exception as e:
            print(f"  skip {sym}: {e}")
    panel = pd.concat(frames, ignore_index=True)
    print(f"  {len(panel)} total rows, {panel['symbol'].nunique()} symbols, "
          f"{panel['date'].min().date()} -> {panel['date'].max().date()}")
    return panel


def compute_breadth(panel):
    wide = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    print(f"Wide panel: {wide.shape[0]} days x {wide.shape[1]} symbols")

    ret = wide.pct_change()

    ma50 = wide.rolling(50, min_periods=50).mean()
    ma200 = wide.rolling(200, min_periods=200).mean()
    above_ma50 = (wide > ma50)
    above_ma200 = (wide > ma200)

    valid50 = ma50.notna()
    valid200 = ma200.notna()
    pct_above_ma50 = (above_ma50 & valid50).sum(axis=1) / valid50.sum(axis=1).replace(0, np.nan)
    pct_above_ma200 = (above_ma200 & valid200).sum(axis=1) / valid200.sum(axis=1).replace(0, np.nan)

    valid_ret = ret.notna()
    advancing = (ret > 0) & valid_ret
    declining = (ret < 0) & valid_ret
    advance_pct = advancing.sum(axis=1) / valid_ret.sum(axis=1).replace(0, np.nan)
    net_advance = advancing.sum(axis=1) - declining.sum(axis=1)
    ad_line_cum = net_advance.cumsum()

    roll_high_63 = wide.rolling(NH_NL_WINDOW, min_periods=NH_NL_WINDOW).max()
    roll_low_63 = wide.rolling(NH_NL_WINDOW, min_periods=NH_NL_WINDOW).min()
    is_new_high = (wide >= roll_high_63) & roll_high_63.notna()
    is_new_low = (wide <= roll_low_63) & roll_low_63.notna()
    valid_hl = roll_high_63.notna()
    new_high_pct = is_new_high.sum(axis=1) / valid_hl.sum(axis=1).replace(0, np.nan)
    new_low_pct = is_new_low.sum(axis=1) / valid_hl.sum(axis=1).replace(0, np.nan)

    cross_sectional_median_ret = ret.median(axis=1)
    cross_sectional_dispersion = ret.std(axis=1)

    out = pd.DataFrame({
        "date": wide.index,
        "pct_above_ma50": pct_above_ma50.values,
        "pct_above_ma200": pct_above_ma200.values,
        "advance_pct": advance_pct.values,
        "ad_line_cum": ad_line_cum.values,
        "new_high_pct_63d": new_high_pct.values,
        "new_low_pct_63d": new_low_pct.values,
        "cross_sectional_median_ret": cross_sectional_median_ret.values,
        "cross_sectional_dispersion": cross_sectional_dispersion.values,
    }).reset_index(drop=True)

    out["pct_above_ma50_chg5d"] = out["pct_above_ma50"].diff(5)
    out["ad_line_10d_chg"] = out["ad_line_cum"].diff(10)
    out["nh_nl_spread_63d"] = out["new_high_pct_63d"] - out["new_low_pct_63d"]
    # Breadth thrust: classic Zweig-style signal -- advance_pct crosses from
    # <40% to >61.5% within 10 trading days (rare, historically bullish).
    # Approximated here as a rolling max/min check on advance_pct.
    adv_roll_min_10 = out["advance_pct"].rolling(10, min_periods=5).min()
    out["breadth_thrust_flag"] = ((adv_roll_min_10 < 0.40) & (out["advance_pct"] > 0.615)).astype(float)

    return out


def merge_spy_divergence(breadth, spy_bars_path):
    spy = pd.read_csv(spy_bars_path, parse_dates=["date"])
    spy = spy[["date", "close"]].rename(columns={"close": "spy_close"})
    spy["spy_ret"] = spy["spy_close"].pct_change()
    merged = breadth.merge(spy[["date", "spy_ret"]], on="date", how="left")
    merged["spy_vs_median_ret"] = merged["spy_ret"] - merged["cross_sectional_median_ret"]
    merged = merged.drop(columns=["spy_ret"])
    return merged


def main():
    panel = load_all_constituents()
    breadth = compute_breadth(panel)

    if os.path.exists(SPY_BARS_PATH):
        breadth = merge_spy_divergence(breadth, SPY_BARS_PATH)
    else:
        print(f"  [warn] {SPY_BARS_PATH} not found, skipping spy_vs_median_ret")
        breadth["spy_vs_median_ret"] = np.nan

    out_cols = ["date", "pct_above_ma50", "pct_above_ma200", "pct_above_ma50_chg5d",
                "advance_pct", "ad_line_cum", "ad_line_10d_chg",
                "new_high_pct_63d", "new_low_pct_63d", "nh_nl_spread_63d",
                "spy_vs_median_ret", "cross_sectional_dispersion", "breadth_thrust_flag"]
    out = breadth[out_cols].copy()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out.to_csv(OUT_PATH, index=False)

    print(f"\nSaved {len(out)} rows -> {OUT_PATH}")
    print(f"Date range: {out['date'].min()} -> {out['date'].max()}")
    print("\nFeature coverage (non-null %):")
    for col in out_cols[1:]:
        pct = out[col].notna().mean() * 100
        print(f"  {col:<28}: {pct:6.1f}%")


if __name__ == "__main__":
    main()
