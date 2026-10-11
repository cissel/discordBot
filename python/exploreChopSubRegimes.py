#!/usr/bin/env python3
"""
exploreChopSubRegimes.py
=========================
Diagnostic: is "chop" one regime or several averaged together?

Motivation: trainChopModel.py's own 5-fold WFCV on chop rows shows wildly
unstable Sharpe across folds (fold2=-0.23, fold3=1.08, fold4=2.31, fold5=1.42
as of the 2026-08-17 retrain). That instability means the live autopilot
(which has spent 66/68 days in chop) is getting a blended average of good
and bad sub-periods it can't tell apart in real time.

This script does two things:
  1. Re-runs chop WFCV (reusing trainChopModel's exact fold logic) and prints,
     for each fold's date window, the mean/median of several candidate
     "sub-regime" state variables -- to see which one tracks the Sharpe swings.
  2. For the single most promising candidate(s), buckets ALL chop history by
     that variable and re-evaluates (train/val split per bucket) to see
     whether the edge concentrates in one bucket.

Candidates tested (all already exist in spy_features.csv, so any gate found
here is wireable into predictSpy.py with zero new data collection):
  - gex_sign          : dealer gamma positioning (+1 long/stabilizing, -1 short/destabilizing)
  - vix_rv_ratio       : implied/realized vol risk premium (rich vs cheap vol)
  - vix_z21            : vol level z-score (calm vs elevated chop)
  - vvix_z21           : vol-of-vol (complacent vs jumpy chop)
  - days_since_regime_change : fresh chop (just flipped) vs mature/stable chop

Usage:
  venv/bin/python3 python/exploreChopSubRegimes.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, accuracy_score

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from trainChopModel import CHOP_FEATURES, prep_chop, chop_wfcv, make_pipeline, dir_accuracy, TX_COST, ANNUAL

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")

CANDIDATES = ["gex_sign", "vix_rv_ratio", "vix_z21", "vvix_z21", "days_since_regime_change"]


def ann_sharpe(returns):
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(ANNUAL))


def main():
    print("[exploreChopSubRegimes] loading features...")
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df, avail = prep_chop(df_raw, CHOP_FEATURES)
    print(f"  chop rows after dropna: {len(df)}  ({df.date.min().date()} to {df.date.max().date()})")
    print()

    # ── Step 1: WFCV folds + candidate variable means per fold window ─────────
    print("=" * 72)
    print("STEP 1: WFCV fold Sharpe vs candidate sub-regime variable means")
    print("=" * 72)
    wfcv = chop_wfcv(df, avail, min_train_rows=500)
    if not wfcv:
        print("WFCV failed, aborting")
        return

    print()
    header = f"{'fold':>4} {'window':>23} {'Sharpe':>8}"
    for c in CANDIDATES:
        header += f" {c[:14]:>14}"
    print(header)
    for fd in wfcv["fold_details"]:
        mask = (df["date"] >= fd["fold_start"]) & (df["date"] <= fd["fold_end"])
        win = df.loc[mask]
        row = f"{fd['fold']:>4} {fd['fold_start']}-{fd['fold_end'][5:]:>5} {fd['bt_sharpe']:>8.3f}"
        for c in CANDIDATES:
            if c in win.columns:
                row += f" {win[c].mean():>14.3f}"
            else:
                row += f" {'n/a':>14}"
        print(row)
    print()
    print("  (compare the Sharpe column against each candidate's fold-mean --")
    print("   a variable that moves in the same direction as Sharpe across folds")
    print("   is a live-computable proxy for 'which kind of chop are we in')")
    print()

    # ── Step 2: full-history correlation of candidate vars with local outcome ──
    print("=" * 72)
    print("STEP 2: bucket ALL chop history by each candidate, single 80/20 holdout")
    print("=" * 72)
    split = int(len(df) * 0.8)

    for cand in CANDIDATES:
        if cand not in df.columns:
            continue
        vals = df[cand]
        try:
            terciles = pd.qcut(vals.rank(method="first"), 3, labels=["low", "mid", "high"])
        except Exception:
            continue
        print(f"\n--- bucketed by {cand} (terciles) ---")
        for bucket in ["low", "mid", "high"]:
            bmask = terciles == bucket
            bdf = df.loc[bmask].reset_index(drop=True)
            if len(bdf) < 150:
                print(f"  {bucket:>5}: n={len(bdf):<5} too few rows, skipped")
                continue
            bsplit = int(len(bdf) * 0.8)
            tr, va = bdf.iloc[:bsplit], bdf.iloc[bsplit:]
            if len(va) < 30 or len(tr) < 100:
                print(f"  {bucket:>5}: n={len(bdf):<5} insufficient train/val split")
                continue
            feats = [f for f in avail if f in bdf.columns]
            m = make_pipeline()
            m.fit(tr[feats].values, tr["next_dir_1d"].values)
            p = m.predict_proba(va[feats].values)[:, 1]
            acc = dir_accuracy(va["next_dir_1d"].values, p)
            auc = (roc_auc_score(va["next_dir_1d"].values, p)
                   if len(np.unique(va["next_dir_1d"].values)) > 1 else float("nan"))
            pos = (p > 0.51).astype(float)
            rets = va["SPY_ret"].values if "SPY_ret" in va.columns else np.zeros(len(va))
            dr = pos * rets - np.abs(np.diff(np.append(0, pos))) * TX_COST
            sh = ann_sharpe(pd.Series(dr))
            bh_sh = ann_sharpe(pd.Series(rets))
            up_rate = va["next_dir_1d"].mean()
            print(f"  {bucket:>5}: n={len(bdf):<5} val_n={len(va):<4} "
                  f"up_rate={up_rate:.3f} acc={acc:.3f} auc={auc:.3f} "
                  f"model_Sharpe={sh:+.2f} BH_Sharpe={bh_sh:+.2f} range=[{vals[bmask].min():.2f},{vals[bmask].max():.2f}]")

    print()
    print("=" * 72)
    print("Done. Look for: (a) a candidate whose fold-means in Step 1 track the")
    print("Sharpe swings, and (b) a candidate in Step 2 where one bucket's")
    print("model_Sharpe is clearly better/worse than the others with decent n.")
    print("That combination = a live-computable gate worth wiring into predictSpy.py.")
    print("=" * 72)


if __name__ == "__main__":
    main()
