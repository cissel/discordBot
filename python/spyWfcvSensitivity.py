#!/usr/bin/env python3
"""
spyWfcvSensitivity.py
=======================
Sanity-checks the 1.855 Sharpe WFCV baseline (spyVolTargetWFCV.py) for
fold-boundary luck before building on top of it (Oct'26, James's request:
"do #5 before #4" -- confirm the baseline is stable before improving its
vol input).

Three independent stress tests, all reusing run_wfcv/apply_gates/
backtest_positions UNCHANGED from spyVolTargetWFCV.py (same classifier,
same sizing rules) so any differences are purely about fold geometry, not
a different pipeline:

  1. FOLD GRANULARITY: rerun with 8 x 126-day folds instead of 4 x 252,
     same total ~1008-day OOS window. If per-fold Sharpe swings wildly at
     finer granularity, the 4-fold number is smoothing over real
     instability.
  2. BOUNDARY SHIFT: trim the most recent 21/42/63 trading days off the
     full dataset before computing folds (same 4x252 scheme), so fold
     boundaries land on different calendar dates. If pooled Sharpe moves
     a lot just from shifting where folds start, the number is
     boundary-sensitive, not a stable estimate.
  3. BLOCK BOOTSTRAP CI: moving-block bootstrap (block=21 trading days,
     2000 resamples) on the original pooled 1008-day OOS daily_ret series
     to get a 95% CI for annualized Sharpe, respecting daily return
     autocorrelation (iid bootstrap would understate the CI width).

Usage:
  venv/bin/python3 python/spyWfcvSensitivity.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from trainSpyModel import SPY_FEATURES, prep_df
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from spyVolTargetWFCV import (attach_realized_vol, base_kelly_fraction, apply_gates,
                               backtest_positions, ann_sharpe, max_drawdown,
                               TX_COST, ANNUAL, KELLY_FRACTION)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
SEED = 42
np.random.seed(SEED)


def run_wfcv_general(df_raw, fold_size, n_folds, end_trim=0):
    """Same logic as spyVolTargetWFCV.run_wfcv but parameterized on
    fold_size/n_folds, and able to trim `end_trim` trading days off the
    END of the full dataset before computing fold boundaries (so folds
    shift to earlier calendar dates without changing fold_size/n_folds)."""
    df, avail = prep_df(df_raw, SPY_FEATURES, "next_dir_1d")
    dates_all = sorted(df["date"].unique())
    if end_trim > 0:
        cutoff = dates_all[-(end_trim + 1)]
        df = df[df["date"] <= cutoff].copy()

    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = fold_size * n_folds
    train_start_idx = n - total_window - fold_size
    if train_start_idx < 500:
        raise ValueError(f"Not enough history for fold_size={fold_size} n_folds={n_folds} "
                          f"end_trim={end_trim}: train_start_idx={train_start_idx}")

    oos_rows = []
    for i in range(n_folds):
        fold_start = dates[train_start_idx + fold_size + i * fold_size]
        fold_end_idx = min(train_start_idx + fold_size + (i + 1) * fold_size - 1, n - 1)
        fold_end = dates[fold_end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < 500 or len(va) < 20:
            continue

        model = Pipeline([("sc", StandardScaler()),
                           ("m", LogisticRegression(solver="liblinear", max_iter=500,
                                                     random_state=42))])
        model.fit(tr[avail].values, tr["next_dir_1d"].values)
        p = model.predict_proba(va[avail].values)[:, 1]

        for j in range(len(va)):
            row = va.iloc[j]
            oos_rows.append({
                "date": row["date"], "p": p[j], "y": row["next_dir_1d"],
                "next_ret_1d": row["next_ret_1d"],
                "regime": row.get("regime"),
                "vix_level": row.get("vix_level"),
                "vix_z21": row.get("vix_z21"),
                "gex_sign": row.get("gex_sign"),
                "fold": i + 1,
            })

    oos = pd.DataFrame(oos_rows).sort_values("date").reset_index(drop=True)
    oos = oos.dropna(subset=["next_ret_1d"]).reset_index(drop=True)
    return oos


def backtest_vol_targeted(oos, df_raw, target_ann_vol=None):
    oos = attach_realized_vol(oos, df_raw)
    if target_ann_vol is None:
        spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
        spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
        target_ann_vol = float(spy_hist["realized_vol_21d"].median())

    raw_kelly = base_kelly_fraction(oos["p"].values)
    pos_base = apply_gates(oos, raw_kelly)
    bt_base = backtest_positions(oos, pos_base)

    vol_scalar = target_ann_vol / oos["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    pos_vt = apply_gates(oos, raw_kelly * vol_scalar)
    bt_vt = backtest_positions(oos, pos_vt)
    return bt_base, bt_vt


def block_bootstrap_sharpe_ci(daily_ret, block_size=21, n_boot=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    daily_ret = np.asarray(daily_ret)
    n = len(daily_ret)
    n_blocks_needed = int(np.ceil(n / block_size))
    max_start = n - block_size
    sharpes = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks_needed)
        resampled = np.concatenate([daily_ret[s:s + block_size] for s in starts])[:n]
        sharpes[b] = ann_sharpe(resampled)
    return sharpes


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"target_ann_vol (fixed across all configs): {target_ann_vol:.4f}\n")

    # ── Test 1: fold granularity ──────────────────────────────────────────
    print("=" * 70)
    print("TEST 1: FOLD GRANULARITY (same ~1008-day OOS window, different carve-up)")
    print("=" * 70)
    configs = [(252, 4, "baseline (original)"), (126, 8, "8x126d"), (63, 16, "16x63d")]
    granularity_results = {}
    for fold_size, n_folds, label in configs:
        oos = run_wfcv_general(df_raw, fold_size, n_folds)
        bt_base, bt_vt = backtest_vol_targeted(oos, df_raw, target_ann_vol)
        print(f"\n  {label}: n_days={len(oos)}  "
              f"baseline Sharpe={bt_base['sharpe']:+.3f}  vol-tgt Sharpe={bt_vt['sharpe']:+.3f}")
        per_fold = []
        for fid in sorted(oos["fold"].unique()):
            sub = oos[oos["fold"] == fid]
            raw_kelly = base_kelly_fraction(sub["p"].values)
            sub2 = attach_realized_vol(sub.reset_index(drop=True), df_raw)
            vol_scalar = target_ann_vol / sub2["realized_vol_21d"].values
            vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
            raw_kelly2 = base_kelly_fraction(sub2["p"].values)
            pos_vt = apply_gates(sub2, raw_kelly2 * vol_scalar)
            bt = backtest_positions(sub2, pos_vt)
            per_fold.append(bt["sharpe"])
            print(f"    fold {fid}: {sub['date'].min().date()}-{sub['date'].max().date()}  "
                  f"n={len(sub)}  vol-tgt Sharpe={bt['sharpe']:+.3f}")
        per_fold = np.array(per_fold)
        print(f"    per-fold Sharpe: mean={per_fold.mean():+.3f}  std={per_fold.std():+.3f}  "
              f"min={per_fold.min():+.3f}  max={per_fold.max():+.3f}")
        granularity_results[label] = dict(pooled_vt_sharpe=bt_vt["sharpe"],
                                           per_fold_mean=per_fold.mean(),
                                           per_fold_std=per_fold.std())

    # ── Test 2: boundary shift ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("TEST 2: BOUNDARY SHIFT (same 4x252 scheme, fold start shifted earlier)")
    print("=" * 70)
    shift_results = {}
    for end_trim in [0, 21, 42, 63]:
        oos = run_wfcv_general(df_raw, 252, 4, end_trim=end_trim)
        bt_base, bt_vt = backtest_vol_targeted(oos, df_raw, target_ann_vol)
        print(f"\n  end_trim={end_trim:>3}d  OOS window: {oos['date'].min().date()}-{oos['date'].max().date()}  "
              f"baseline Sharpe={bt_base['sharpe']:+.3f}  vol-tgt Sharpe={bt_vt['sharpe']:+.3f}")
        shift_results[end_trim] = bt_vt["sharpe"]

    shifts = np.array(list(shift_results.values()))
    print(f"\n  Vol-tgt Sharpe across shifts: mean={shifts.mean():+.3f}  std={shifts.std():+.3f}  "
          f"range=[{shifts.min():+.3f}, {shifts.max():+.3f}]")

    # ── Test 3: block bootstrap CI ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("TEST 3: BLOCK BOOTSTRAP 95% CI (original 4x252 pooled OOS, block=21d, n=2000)")
    print("=" * 70)
    oos_orig = run_wfcv_general(df_raw, 252, 4)
    bt_base_orig, bt_vt_orig = backtest_vol_targeted(oos_orig, df_raw, target_ann_vol)
    print(f"\n  Point estimate: baseline Sharpe={bt_base_orig['sharpe']:+.3f}  "
          f"vol-tgt Sharpe={bt_vt_orig['sharpe']:+.3f}  (n={len(oos_orig)} days)")

    boot_sharpes_vt = block_bootstrap_sharpe_ci(bt_vt_orig["daily_ret"])
    boot_sharpes_base = block_bootstrap_sharpe_ci(bt_base_orig["daily_ret"])
    ci_lo_vt, ci_hi_vt = np.percentile(boot_sharpes_vt, [2.5, 97.5])
    ci_lo_base, ci_hi_base = np.percentile(boot_sharpes_base, [2.5, 97.5])
    print(f"\n  Baseline  95% CI: [{ci_lo_base:+.3f}, {ci_hi_base:+.3f}]  "
          f"(bootstrap mean={boot_sharpes_base.mean():+.3f}, std={boot_sharpes_base.std():.3f})")
    print(f"  Vol-tgt   95% CI: [{ci_lo_vt:+.3f}, {ci_hi_vt:+.3f}]  "
          f"(bootstrap mean={boot_sharpes_vt.mean():+.3f}, std={boot_sharpes_vt.std():.3f})")
    pct_positive = (boot_sharpes_vt > 0).mean() * 100
    print(f"  P(Sharpe > 0) under bootstrap resampling: {pct_positive:.1f}%")

    print("\n" + "=" * 70)
    print("SUMMARY / VERDICT")
    print("=" * 70)
    print(f"  Fold granularity: pooled Sharpe {granularity_results['baseline (original)']['pooled_vt_sharpe']:+.3f} "
          f"(4-fold) vs {granularity_results['8x126d']['pooled_vt_sharpe']:+.3f} (8-fold) vs "
          f"{granularity_results['16x63d']['pooled_vt_sharpe']:+.3f} (16-fold)")
    print(f"  Boundary shift range: [{shifts.min():+.3f}, {shifts.max():+.3f}] "
          f"(spread={shifts.max()-shifts.min():.3f})")
    print(f"  Bootstrap 95% CI: [{ci_lo_vt:+.3f}, {ci_hi_vt:+.3f}] "
          f"(width={ci_hi_vt-ci_lo_vt:.3f}), P(Sharpe>0)={pct_positive:.1f}%")


if __name__ == "__main__":
    main()
