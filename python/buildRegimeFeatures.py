#!/usr/bin/env python3
"""
buildRegimeFeatures.py
======================
Computes per-regime Spearman correlation of every feature vs next_dir_1d
and next_ret_5d. Surfaces features that are strong in one regime but weak
(or opposite-signed) in another — these are candidates for regime-specific
model feature lists rather than the universal SPY_FEATURES.

Output:
  outputs/features/markets/regime_feature_importance.csv
  Columns: feature, rho_dir_all, rho_dir_bull, rho_dir_bear, rho_dir_chop,
           rho_5d_all, rho_5d_bull, rho_5d_bear, rho_5d_chop,
           cov_all, cov_bull, cov_bear, cov_chop,
           regime_divergence   (max abs difference between regimes)

Usage:
  venv/bin/python3 python/buildRegimeFeatures.py
  venv/bin/python3 python/buildRegimeFeatures.py --min-rho 0.03
"""

import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_PATH  = os.path.expanduser("~/discordBot/outputs/features/markets/regime_feature_importance.csv")

EXCLUDE = {
    "date", "SPY_ret", "QQQ_ret", "GLD_ret", "USO_ret", "VIX", "T10Y2Y",
    "DXY_ret", "FEDFUNDS", "next_ret_1d", "next_ret_5d", "next_dir_1d",
    "next_dir_tail", "next_dir_tail_bin", "next_ret_5d_vadj", "next_dir_5d_vadj",
    "regime",
}

TARGETS = ["next_dir_1d", "next_ret_5d"]


def spearman_safe(x, y):
    """Spearman rho, returns (rho, n) with NaN safety."""
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < 30:
        return np.nan, len(df)
    rho, _ = spearmanr(df["x"], df["y"])
    return round(float(rho), 4), len(df)


def compute_regime_spearman(df, feat_cols, regime_col="regime"):
    regimes   = ["all", "bull", "bear", "chop"]
    rows      = []

    for col in feat_cols:
        row = {"feature": col}

        for target in TARGETS:
            suffix = "dir" if target == "next_dir_1d" else "5d"

            # All data
            rho, n = spearman_safe(df[col], df[target])
            row[f"rho_{suffix}_all"] = rho
            row[f"cov_{suffix}_all"] = round(df[col].notna().mean(), 3)
            row[f"n_{suffix}_all"]   = n

            # Per regime
            for reg in ("bull", "bear", "chop"):
                mask = df[regime_col] == reg
                rho_r, n_r = spearman_safe(df.loc[mask, col], df.loc[mask, target])
                row[f"rho_{suffix}_{reg}"] = rho_r
                row[f"n_{suffix}_{reg}"]   = n_r

        # Regime divergence: how much does rho_dir vary across regimes?
        dir_rhos = [row.get(f"rho_dir_{r}") for r in ("bull", "bear", "chop")]
        dir_rhos = [r for r in dir_rhos if r is not None and not np.isnan(r)]
        row["regime_divergence"] = round(max(dir_rhos) - min(dir_rhos), 4) if len(dir_rhos) >= 2 else np.nan

        rows.append(row)

    return pd.DataFrame(rows)


def main(min_rho=0.0):
    print("[buildRegimeFeatures] loading features...")
    if not os.path.exists(FEAT_PATH):
        print(f"  ERROR: {FEAT_PATH} not found — run buildSpyFeatures.py first")
        sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df = df[df["next_dir_1d"].notna()].copy()
    print(f"  {len(df)} rows, {df['date'].min().date()} to {df['date'].max().date()}")

    if "regime" not in df.columns:
        print("  ERROR: 'regime' column missing — run buildSpyFeatures.py first")
        sys.exit(1)

    regime_counts = df["regime"].value_counts()
    print(f"  Regime distribution: {dict(regime_counts)}")

    feat_cols = [c for c in df.columns if c not in EXCLUDE]
    print(f"  Computing Spearman for {len(feat_cols)} features across 4 groups...")

    result = compute_regime_spearman(df, feat_cols)

    # Sort by abs(rho_dir_all) descending
    result["abs_rho_dir"] = result["rho_dir_all"].abs()
    result = result.sort_values("abs_rho_dir", ascending=False).reset_index(drop=True)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    result.to_csv(OUT_PATH, index=False)
    print(f"\n  Saved {len(result)} features -> {OUT_PATH}")

    # ── print summary tables ───────────────────────────────────────────────────
    print()
    print("=== TOP FEATURES BY abs(rho_dir_all) ===")
    print(f"  {'Feature':35s} {'all':>7} {'bull':>7} {'bear':>7} {'chop':>7} {'5d_all':>7} {'diverg':>7}")
    print("  " + "-" * 80)
    for _, row in result.head(25).iterrows():
        def fmt(v):
            return f"{v:+.3f}" if pd.notna(v) else "  NaN "
        print(f"  {row['feature']:35s} "
              f"{fmt(row['rho_dir_all']):>7} "
              f"{fmt(row['rho_dir_bull']):>7} "
              f"{fmt(row['rho_dir_bear']):>7} "
              f"{fmt(row['rho_dir_chop']):>7} "
              f"{fmt(row['rho_5d_all']):>7} "
              f"{fmt(row['regime_divergence']):>7}")

    print()
    print("=== HIGHEST REGIME DIVERGENCE (feature behaves differently across regimes) ===")
    print(f"  {'Feature':35s} {'bull':>7} {'bear':>7} {'chop':>7} {'divergence':>10}")
    print("  " + "-" * 68)
    div_sorted = result.dropna(subset=["regime_divergence"]).sort_values("regime_divergence", ascending=False)
    for _, row in div_sorted.head(20).iterrows():
        def fmt(v):
            return f"{v:+.3f}" if pd.notna(v) else "  NaN "
        print(f"  {row['feature']:35s} "
              f"{fmt(row['rho_dir_bull']):>7} "
              f"{fmt(row['rho_dir_bear']):>7} "
              f"{fmt(row['rho_dir_chop']):>7} "
              f"{fmt(row['regime_divergence']):>10}")

    print()
    print("=== BEAR-SPECIFIC SIGNALS (abs(rho_dir_bear) > 0.05, strongest in bear) ===")
    bear_strong = result[result["rho_dir_bear"].abs() > 0.05].sort_values("rho_dir_bear", key=abs, ascending=False)
    for _, row in bear_strong.head(15).iterrows():
        def fmt(v):
            return f"{v:+.3f}" if pd.notna(v) else "  NaN "
        print(f"  {row['feature']:35s} "
              f"bear={fmt(row['rho_dir_bear']):>7}  "
              f"bull={fmt(row['rho_dir_bull']):>7}  "
              f"chop={fmt(row['rho_dir_chop']):>7}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-rho", type=float, default=0.0,
                        help="Minimum abs(rho_dir_all) to include in output (default: 0.0 = all)")
    args = parser.parse_args()
    main(min_rho=args.min_rho)
