#!/usr/bin/env python3
"""
spyFullBlendWfcvCompare.py
=============================
Final check before calling the LightGBM swap + causal-regime-label fix
"deployed": bootstrap-validated Sharpe comparison of the FULL production
blend (regime-routed logistic + GBM-ensemble blend, same mechanics as
predictSpy.py) -- not just the GBM-ensemble component in isolation --
old pipeline (sklearn GBM + lookahead regime labels) vs new pipeline
(LightGBM + causal regime labels), walk-forward across the same 4 WFCV
folds used throughout this month's work.

Blend logic replicated from predictSpy.py (simplified but faithful to the
core mechanics that drive position sizing):
  - For each eval day, route to the regime-specific logistic model for
    TODAY's regime if one was trained with enough rows this fold,
    otherwise fall back to the universal logistic (same fallback
    predictSpy.py uses live).
  - Blend regime-routed logistic prob with the GBM-ensemble prob, 50/50
    (matches predictSpy's default blend path; the dedicated-chop-GBM
    sub-blend is NOT replicated here since it requires per-fold Platt
    recalibration that isn't practical to reproduce exactly across folds
    -- this means both OLD and NEW use the same slightly-simplified blend,
    so the comparison is still apples-to-apples, just not pixel-identical
    to live predictSpy output).
  - Apply the SAME sizing/gates as every other spike this month
    (spyVolTargetWFCV.apply_gates: fixed 50% Kelly + bear-halving +
    kill-switch + chop-VIX-gate), vol-targeted.

Usage:
  venv/bin/python3 python/spyFullBlendWfcvCompare.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.ensemble import GradientBoostingClassifier

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from trainSpyModel import prep_df, SPY_FEATURES
from spyVolTargetWFCV import (attach_realized_vol, base_kelly_fraction, apply_gates,
                               backtest_positions, ann_sharpe, max_drawdown, ANNUAL)

FEAT_PATH_NEW = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
FEAT_PATH_OLD = "/tmp/spy_features_backup_pre_regime_fix.csv"
SEED = 42
N_SEEDS_FAST = 5   # reduced from production's 15 for backtest speed; same architecture
BULL_START = pd.Timestamp("2021-01-04")
MIN_REGIME_ROWS = 200


def build_gbm_ensemble(tr, avail, seeds, use_lgbm):
    models = []
    for seed in seeds:
        if use_lgbm:
            from lightgbm import LGBMClassifier
            inner = LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=7,
                                    max_depth=3, min_child_samples=50, subsample=0.7,
                                    subsample_freq=1, colsample_bytree=0.8,
                                    reg_alpha=0.1, reg_lambda=0.1, random_state=seed, verbose=-1)
        else:
            inner = GradientBoostingClassifier(n_estimators=300, learning_rate=0.03,
                                                max_depth=2, min_samples_leaf=50,
                                                subsample=0.7, random_state=seed)
        m = Pipeline([("sc", StandardScaler()), ("m", inner)])
        m.fit(tr[avail].values, tr["next_dir_1d"].values)
        models.append(m)
    return models


def ensemble_predict(models, X):
    return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)


def train_regime_logistic(tr, avail, regime_name):
    regime_df = tr[tr["regime"] == regime_name]
    if regime_name == "bull":
        regime_df = regime_df[regime_df["date"] >= BULL_START]
    if len(regime_df) < MIN_REGIME_ROWS:
        return None
    m = Pipeline([("sc", StandardScaler()),
                  ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
    m.fit(regime_df[avail].values, regime_df["next_dir_1d"].values)
    return m


def run_pipeline(feat_path, use_lgbm, label):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    df_raw = pd.read_csv(feat_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df, avail = prep_df(df_raw, SPY_FEATURES, "next_dir_1d")

    dates = sorted(df["date"].unique())
    n = len(dates)
    FOLD_SIZE, N_FOLDS = 252, 4
    total_window = FOLD_SIZE * N_FOLDS
    train_start_idx = n - total_window - FOLD_SIZE

    oos_rows = []
    for i in range(N_FOLDS):
        fold_start = dates[train_start_idx + FOLD_SIZE + i * FOLD_SIZE]
        fold_end_idx = min(train_start_idx + FOLD_SIZE + (i + 1) * FOLD_SIZE - 1, n - 1)
        fold_end = dates[fold_end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < 500 or len(va) < 20:
            continue

        # Universal logistic (fallback)
        universal_log = Pipeline([("sc", StandardScaler()),
                                   ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
        universal_log.fit(tr[avail].values, tr["next_dir_1d"].values)

        # Regime-specific logistics (bull/bear/chop), fallback to universal if too few rows
        regime_models = {r: train_regime_logistic(tr, avail, r) for r in ("bull", "bear", "chop")}

        # GBM ensemble (sklearn or lightgbm per flag)
        seeds = list(range(0, N_SEEDS_FAST * 7, 7))
        gbm_models = build_gbm_ensemble(tr, avail, seeds, use_lgbm)

        X_va = va[avail].values
        prob_universal = universal_log.predict_proba(X_va)[:, 1]
        prob_gbm = ensemble_predict(gbm_models, X_va)

        va_regimes = va["regime"].values
        prob_logistic_routed = prob_universal.copy()
        for r in ("bull", "bear", "chop"):
            if regime_models[r] is not None:
                mask = va_regimes == r
                if mask.sum() > 0:
                    prob_logistic_routed[mask] = regime_models[r].predict_proba(X_va[mask])[:, 1]

        prob_blend = (prob_logistic_routed + prob_gbm) / 2.0

        for j in range(len(va)):
            row = va.iloc[j]
            oos_rows.append({
                "date": row["date"], "p": prob_blend[j], "y": row["next_dir_1d"],
                "next_ret_1d": row["next_ret_1d"], "regime": row.get("regime"),
                "vix_level": row.get("vix_level"), "vix_z21": row.get("vix_z21"),
                "gex_sign": row.get("gex_sign"), "fold": i + 1,
            })
        n_bull_trained = int(regime_models["bull"] is not None)
        n_bear_trained = int(regime_models["bear"] is not None)
        n_chop_trained = int(regime_models["chop"] is not None)
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  tr={len(tr)} va={len(va)}  "
              f"regime models trained: bull={n_bull_trained} bear={n_bear_trained} chop={n_chop_trained}")

    oos = pd.DataFrame(oos_rows).sort_values("date").reset_index(drop=True)
    oos = oos.dropna(subset=["next_ret_1d"]).reset_index(drop=True)
    return oos


def backtest(oos, df_raw, target_ann_vol):
    oos = attach_realized_vol(oos, df_raw)
    raw_kelly = base_kelly_fraction(oos["p"].values)
    pos_base = apply_gates(oos, raw_kelly)
    bt_base = backtest_positions(oos, pos_base)
    vol_scalar = np.nan_to_num(target_ann_vol / oos["realized_vol_21d"].values, nan=1.0, posinf=1.0, neginf=1.0)
    pos_vt = apply_gates(oos, raw_kelly * vol_scalar)
    bt_vt = backtest_positions(oos, pos_vt)
    return bt_base, bt_vt, oos


def paired_block_bootstrap(ret_a, ret_b, block=21, n_boot=5000, seed=SEED):
    rng = np.random.default_rng(seed)
    n = min(len(ret_a), len(ret_b))
    ret_a, ret_b = ret_a[:n], ret_b[:n]
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        deltas[b] = ann_sharpe(ret_b[idx]) - ann_sharpe(ret_a[idx])
    return deltas


def main():
    df_raw_new = pd.read_csv(FEAT_PATH_NEW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    spy_hist = df_raw_new[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"target_ann_vol: {target_ann_vol:.4f}")

    configs = [
        ("OLD", FEAT_PATH_OLD, False, "OLD: sklearn GBM + lookahead regime labels (true baseline)"),
        ("REGIME_FIX_ONLY", FEAT_PATH_NEW, False, "REGIME-FIX ONLY: sklearn GBM + causal regime labels"),
        ("LGBM_ONLY", FEAT_PATH_OLD, True, "LIGHTGBM ONLY: LightGBM + lookahead regime labels"),
        ("BOTH", FEAT_PATH_NEW, True, "BOTH: LightGBM + causal regime labels (full new pipeline)"),
    ]

    oos_results = {}
    bt_results = {}
    for key, feat_path, use_lgbm, label in configs:
        oos = run_pipeline(feat_path, use_lgbm=use_lgbm, label=label)
        df_raw = pd.read_csv(feat_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        bt_base, bt_vt, oos_f = backtest(oos, df_raw, target_ann_vol)
        oos_results[key] = oos_f
        bt_results[key] = bt_vt
        print(f"\n  [{key}] n_days={len(oos_f)}  Sharpe={bt_vt['sharpe']:+.3f}  "
              f"MaxDD={bt_vt['max_dd']*100:+.2f}%  TotalRet={bt_vt['total_return']*100:+.2f}%")

    print(f"\n{'='*70}\nATTRIBUTION SUMMARY\n{'='*70}")
    base_sharpe = bt_results["OLD"]["sharpe"]
    print(f"  OLD (true baseline):                     Sharpe={base_sharpe:+.3f}")
    print(f"  REGIME_FIX_ONLY (causal labels, sklearn): Sharpe={bt_results['REGIME_FIX_ONLY']['sharpe']:+.3f}  "
          f"(delta vs OLD: {bt_results['REGIME_FIX_ONLY']['sharpe']-base_sharpe:+.3f})")
    print(f"  LGBM_ONLY (LightGBM, lookahead labels):   Sharpe={bt_results['LGBM_ONLY']['sharpe']:+.3f}  "
          f"(delta vs OLD: {bt_results['LGBM_ONLY']['sharpe']-base_sharpe:+.3f})")
    print(f"  BOTH (full new pipeline):                 Sharpe={bt_results['BOTH']['sharpe']:+.3f}  "
          f"(delta vs OLD: {bt_results['BOTH']['sharpe']-base_sharpe:+.3f})")

    print(f"\n{'='*70}\nBootstrap significance for each isolated change (block=21d, n=5000)\n{'='*70}")
    for key in ["REGIME_FIX_ONLY", "LGBM_ONLY", "BOTH"]:
        deltas = paired_block_bootstrap(bt_results["OLD"]["daily_ret"], bt_results[key]["daily_ret"])
        ci = np.percentile(deltas, [2.5, 97.5])
        pct_pos = (deltas > 0).mean() * 100
        print(f"  {key} vs OLD: mean_delta={deltas.mean():+.3f}  95%CI=[{ci[0]:+.3f},{ci[1]:+.3f}]  "
              f"P({key} beats OLD)={pct_pos:.1f}%")


if __name__ == "__main__":
    main()
