#!/usr/bin/env python3
"""
spyIntradayV2WfcvCompare.py
==============================
Direct A/B comparison of the existing macro-only SPY_FEATURES baseline
vs. macro+intraday-V2 (1-min-bar derived features, Oct'26 addition) on
IDENTICAL WFCV folds and sizing rules (spyVolTargetWFCV.py), so any Sharpe
delta is attributable purely to the new features.

Reuses run_wfcv's exact logic but parameterized on a feature list, so
"baseline" and "baseline+intraday" train/validate on the SAME fold
boundaries, same classifier family (logistic), same sizing/gating.

Usage:
  venv/bin/python3 python/spyIntradayV2WfcvCompare.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from trainSpyModel import SPY_FEATURES, prep_df
from spyVolTargetWFCV import (attach_realized_vol, base_kelly_fraction, apply_gates,
                               backtest_positions, ann_sharpe, max_drawdown, ANNUAL)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
SEED = 42

INTRADAY_V2_FEATS = [
    "rv_1min_full_ann", "bpv_1min_ann", "jump_ratio", "jump_ratio_r5",
    "overnight_ret_v2", "session_only_ret",
    "bucket1_ret", "bucket6_ret",
    "or_15m_range", "or_15m_breakout_up", "or_15m_breakout_down",
    "vol_entropy_30m", "signed_vol_imbalance",
    "vwap_dev_close_full", "session_runup", "session_drawdown",
    "bpv_vix_spread",
]
BASELINE_FEATS = [f for f in SPY_FEATURES if f not in INTRADAY_V2_FEATS]


def run_wfcv_with_features(df_raw, features, fold_size=252, n_folds=4):
    df, avail = prep_df(df_raw, features, "next_dir_1d")
    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = fold_size * n_folds
    train_start_idx = n - total_window - fold_size

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
    return oos, len(avail)


def backtest_vol_targeted(oos, df_raw, target_ann_vol):
    oos = attach_realized_vol(oos, df_raw)
    raw_kelly = base_kelly_fraction(oos["p"].values)
    pos_base = apply_gates(oos, raw_kelly)
    bt_base = backtest_positions(oos, pos_base)
    vol_scalar = target_ann_vol / oos["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    pos_vt = apply_gates(oos, raw_kelly * vol_scalar)
    bt_vt = backtest_positions(oos, pos_vt)
    return bt_base, bt_vt, oos


def dir_acc_auc(oos):
    from sklearn.metrics import accuracy_score, roc_auc_score
    y_pred = (oos["p"].values >= 0.5).astype(int)
    acc = accuracy_score(oos["y"].values, y_pred)
    auc = roc_auc_score(oos["y"].values, oos["p"].values)
    return acc, auc


def paired_block_bootstrap_sharpe_delta(ret_a, ret_b, block_size=21, n_boot=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    n = len(ret_a)
    block = min(block_size, n - 1) if n > 1 else 1
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        deltas[b] = ann_sharpe(ret_b[idx]) - ann_sharpe(ret_a[idx])
    return deltas


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())

    print(f"BASELINE_FEATS: {len(BASELINE_FEATS)} features (macro only)")
    print(f"+INTRADAY_V2:   {len(BASELINE_FEATS) + len(INTRADAY_V2_FEATS)} features "
          f"(macro + {len(INTRADAY_V2_FEATS)} 1-min-derived)")

    print("\nRunning WFCV: baseline (macro only)...")
    oos_base, n_avail_base = run_wfcv_with_features(df_raw, BASELINE_FEATS)
    print(f"  n_features_avail={n_avail_base}  n_oos_days={len(oos_base)}")

    print("\nRunning WFCV: macro + intraday V2...")
    oos_v2, n_avail_v2 = run_wfcv_with_features(df_raw, SPY_FEATURES)
    print(f"  n_features_avail={n_avail_v2}  n_oos_days={len(oos_v2)}")

    acc_base, auc_base = dir_acc_auc(oos_base)
    acc_v2, auc_v2 = dir_acc_auc(oos_v2)
    print(f"\nDirection accuracy: baseline={acc_base:.4f}  +intraday={acc_v2:.4f}  delta={acc_v2-acc_base:+.4f}")
    print(f"ROC-AUC:            baseline={auc_base:.4f}  +intraday={auc_v2:.4f}  delta={auc_v2-auc_base:+.4f}")

    bt_base_b, bt_base_vt, oos_base_full = backtest_vol_targeted(oos_base, df_raw, target_ann_vol)
    bt_v2_b, bt_v2_vt, oos_v2_full = backtest_vol_targeted(oos_v2, df_raw, target_ann_vol)

    print("\n" + "=" * 70)
    print("SHARPE COMPARISON (vol-targeted sizing, identical folds/rules)")
    print("=" * 70)
    print(f"  Baseline (macro only):      Sharpe={bt_base_vt['sharpe']:+.3f}  "
          f"MaxDD={bt_base_vt['max_dd']*100:+.2f}%  TotalRet={bt_base_vt['total_return']*100:+.2f}%")
    print(f"  +Intraday V2 (macro+1min): Sharpe={bt_v2_vt['sharpe']:+.3f}  "
          f"MaxDD={bt_v2_vt['max_dd']*100:+.2f}%  TotalRet={bt_v2_vt['total_return']*100:+.2f}%")
    print(f"\n  Delta Sharpe: {bt_v2_vt['sharpe']-bt_base_vt['sharpe']:+.3f}")

    # per-fold accuracy/auc breakdown
    print("\n" + "=" * 70)
    print("Per-fold direction accuracy / AUC")
    print("=" * 70)
    for fid in sorted(oos_base["fold"].unique()):
        sb = oos_base[oos_base["fold"] == fid]
        sv = oos_v2[oos_v2["fold"] == fid]
        ab, aucb = dir_acc_auc(sb)
        av, aucv = dir_acc_auc(sv)
        print(f"  fold {fid}: baseline acc={ab:.3f} auc={aucb:.3f}  |  "
              f"+intraday acc={av:.3f} auc={aucv:.3f}  (delta acc={av-ab:+.3f} auc={aucv-aucb:+.3f})")

    # bootstrap significance on Sharpe delta (align on common dates in case
    # fold boundaries shifted differently -- they shouldn't since both use
    # the same dates array length, but confirm)
    n_common = min(len(bt_base_vt["daily_ret"]), len(bt_v2_vt["daily_ret"]))
    deltas = paired_block_bootstrap_sharpe_delta(
        bt_base_vt["daily_ret"][:n_common], bt_v2_vt["daily_ret"][:n_common])
    ci_lo, ci_hi = np.percentile(deltas, [2.5, 97.5])
    pct_pos = (deltas > 0).mean() * 100
    print(f"\nBootstrap Sharpe delta (+intraday - baseline): mean={deltas.mean():+.3f}  "
          f"95%CI=[{ci_lo:+.3f},{ci_hi:+.3f}]  P(+intraday wins)={pct_pos:.1f}%")


if __name__ == "__main__":
    main()
