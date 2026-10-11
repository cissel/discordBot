#!/usr/bin/env python3
"""
chopVixSubRegimeWFCV.py
========================
Follow-up to exploreChopSubRegimes.py's finding: within chop, dir_accuracy/
Sharpe diverges sharply by vix_z21 tercile (low-vol chop good, high-vol chop
bad). That first pass fit tercile cutoffs on the FULL sample, which is a form
of lookahead (the cutoffs "knew" the whole history's VIX distribution).

This script re-tests the same idea the honest way: pool out-of-fold
predictions from chop_wfcv's 4 valid walk-forward folds (already trained
with no lookahead), then bucket those OOS predictions using FIXED,
economically-motivated VIX thresholds decided in advance (not fit from data):
  calm:     VIX < 16
  normal:   16 <= VIX < 22
  elevated: VIX >= 22

If the split still holds under this stricter test, it's a real, deployable
gate (apply it to predictSpy.py's live threshold logic). If it washes out,
the first pass was overfit to the sample's own VIX distribution.

Usage:
  venv/bin/python3 python/chopVixSubRegimeWFCV.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from trainChopModel import CHOP_FEATURES, prep_chop, make_pipeline, dir_accuracy, TX_COST, ANNUAL

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")

VIX_BUCKETS = [(0, 16, "calm"), (16, 22, "normal"), (22, 999, "elevated")]


def ann_sharpe(returns):
    returns = np.asarray(returns)
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(ANNUAL))


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df, avail = prep_chop(df_raw, CHOP_FEATURES)
    print(f"[chopVixSubRegimeWFCV] {len(df)} chop rows, {df.date.min().date()}-{df.date.max().date()}")

    dates = sorted(df["date"].unique())
    n = len(dates)
    fold_size = 252
    n_folds = 5
    total_window = fold_size * n_folds
    train_start_idx = n - total_window - fold_size
    min_train_rows = 500

    oos_rows = []  # (date, p, y_true, spy_ret, vix_level, fold)

    for i in range(n_folds):
        fold_start = dates[train_start_idx + fold_size + i * fold_size]
        fold_end = dates[min(train_start_idx + fold_size + (i + 1) * fold_size - 1, n - 1)]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < min_train_rows or len(va) < 20:
            print(f"  fold {i+1}: skipped (tr={len(tr)}, va={len(va)})")
            continue
        avail_f = [f for f in avail if f in tr.columns]
        m = make_pipeline()
        m.fit(tr[avail_f].values, tr["next_dir_1d"].values)
        p = m.predict_proba(va[avail_f].values)[:, 1]
        for j in range(len(va)):
            oos_rows.append({
                "date": va["date"].iloc[j], "p": p[j], "y": va["next_dir_1d"].iloc[j],
                "spy_ret": va["SPY_ret"].iloc[j] if "SPY_ret" in va.columns else 0.0,
                "vix_level": va["vix_level"].iloc[j] if "vix_level" in va.columns else np.nan,
                "fold": i + 1,
            })
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  tr={len(tr)} va={len(va)}  pooled OOS rows so far: {len(oos_rows)}")

    oos = pd.DataFrame(oos_rows)
    print(f"\nTotal pooled OOS rows (true walk-forward, zero lookahead): {len(oos)}")
    print(f"Pooled overall: acc={dir_accuracy(oos['y'].values, oos['p'].values):.4f}  "
          f"auc={roc_auc_score(oos['y'].values, oos['p'].values):.4f}")
    pos_all = (oos["p"] > 0.51).astype(float)
    dr_all = pos_all.values * oos["spy_ret"].values - np.abs(np.diff(np.append(0, pos_all.values))) * TX_COST
    print(f"Pooled Sharpe @0.51: {ann_sharpe(dr_all):+.3f}  n_signals={int(pos_all.sum())}  "
          f"BH_Sharpe={ann_sharpe(oos['spy_ret'].values):+.3f}")

    print("\n" + "=" * 78)
    print("FIXED (pre-decided, non-fit) VIX-level buckets on pooled OOS predictions:")
    print("=" * 78)
    print(f"{'bucket':>10} {'n':>6} {'up_rate':>8} {'acc':>7} {'auc':>7} {'Sharpe@.51':>11} {'BH_Sharpe':>10} {'n_sig':>6}")
    for lo, hi, label in VIX_BUCKETS:
        mask = (oos["vix_level"] >= lo) & (oos["vix_level"] < hi)
        sub = oos.loc[mask]
        if len(sub) < 30:
            print(f"{label:>10} {len(sub):>6}  too few rows")
            continue
        up_rate = sub["y"].mean()
        acc = dir_accuracy(sub["y"].values, sub["p"].values)
        auc = (roc_auc_score(sub["y"].values, sub["p"].values)
               if len(np.unique(sub["y"].values)) > 1 else float("nan"))
        pos = (sub["p"] > 0.51).astype(float).values
        dr = pos * sub["spy_ret"].values - np.abs(np.diff(np.append(0, pos))) * TX_COST
        sh = ann_sharpe(dr)
        bh = ann_sharpe(sub["spy_ret"].values)
        print(f"{label:>10} {len(sub):>6} {up_rate:>8.3f} {acc:>7.3f} {auc:>7.3f} {sh:>+11.3f} {bh:>+10.3f} {int(pos.sum()):>6}")

    print("\nPer-fold breakdown within each VIX bucket (checks the split isn't one lucky fold):")
    for lo, hi, label in VIX_BUCKETS:
        print(f"\n  -- {label} (VIX {lo}-{hi}) --")
        mask = (oos["vix_level"] >= lo) & (oos["vix_level"] < hi)
        sub = oos.loc[mask]
        for fold_n in sorted(sub["fold"].unique()):
            fsub = sub[sub["fold"] == fold_n]
            if len(fsub) < 15:
                print(f"    fold {fold_n}: n={len(fsub)} too few")
                continue
            acc = dir_accuracy(fsub["y"].values, fsub["p"].values)
            pos = (fsub["p"] > 0.51).astype(float).values
            dr = pos * fsub["spy_ret"].values - np.abs(np.diff(np.append(0, pos))) * TX_COST
            sh = ann_sharpe(dr)
            print(f"    fold {fold_n}: n={len(fsub):<4} acc={acc:.3f}  Sharpe@.51={sh:+.3f}")


if __name__ == "__main__":
    main()
