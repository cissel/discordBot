#!/usr/bin/env python3
"""
trainBtcModel.py
================
Trains directional and return-magnitude models for BTC next-day and next-5-day prediction.

Models:
  1. Ridge regression      - next_ret_1d  (baseline linear)
  2. GBM regression        - next_ret_1d  (nonlinear regime interactions)
  3. Logistic regression   - next_dir_1d  (directional accuracy, primary eval metric)
  4. GBM classifier        - next_dir_1d  (nonlinear direction)
  5. GBM regression        - next_ret_5d  (weekly horizon, historically strongest)

Evaluation:
  Regression:     Spearman rank correlation, RMSE, directional accuracy
  Classification: Accuracy, ROC-AUC, Brier score
  Val window:     Last 365 days (BTC trades 365d/yr, not 252 trading days)

Outputs:
  models/markets/btc/btc_<model_type>_<target>_<date>.pkl
  models/meta/btc_experiment_log.csv

Usage:
  venv/bin/python3 python/trainBtcModel.py [--notes "..."]
"""

import os
import sys
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd
from scipy import stats

from sklearn.linear_model  import Ridge, LogisticRegression
from sklearn.ensemble      import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline      import Pipeline
from sklearn.metrics       import (mean_squared_error, mean_absolute_error,
                                   r2_score, accuracy_score, roc_auc_score,
                                   brier_score_loss)

warnings.filterwarnings("ignore")

FEAT_PATH  = os.path.expanduser("~/discordBot/outputs/features/markets/btc_features.csv")
MODEL_DIR  = os.path.expanduser("~/discordBot/models/markets/btc")
LOG_PATH   = os.path.expanduser("~/discordBot/models/meta/btc_experiment_log.csv")
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

TODAY = datetime.date.today().isoformat()

# ── feature list ───────────────────────────────────────────────────────────────
BTC_FEATURES = [
    # BTC price momentum & technicals (16)
    "btc_ret_r5", "btc_ret_r10", "btc_ret_r21", "btc_ret_r63",
    "btc_vol_r5", "btc_vol_r21", "btc_vol_r63",
    "btc_rsi_14", "btc_rsi_3", "btc_bbpct_20", "btc_drawdown_252",
    "btc_mom_63_21", "btc_z5", "btc_z21",
    "btc_consec_up", "btc_consec_down",
    # Volatility regime (4)
    "btc_rv_21", "btc_rv_z21", "btc_rv_z252", "btc_vol_regime",
    # On-chain metrics (14)
    "btc_mvrv", "btc_nupl", "btc_mvrv_z63", "btc_mvrv_z252", "btc_mvrv_chg_21",
    "btc_hashrate_growth_21", "btc_hashrate_growth_63", "btc_hashrate_z63",
    "btc_realized_mvrv", "btc_realized_mvrv_chg_21",
    "btc_blkcnt_ratio", "btc_blkcnt_z21",
    "btc_dominance", "btc_dominance_chg_21",
    # S2F / cycle position (4)
    "btc_s2f_dev", "btc_s2f_dev_z63",
    "btc_halving_cycle_pct", "btc_days_since_halving",
    # Cross-asset (11)
    "spy_ret_1d", "spy_ret_5d", "spy_z21",
    "gld_ret_1d", "gld_ret_5d", "gld_z21",
    "uso_ret_1d", "uso_z21",
    "dxy_ret_1d", "dxy_z21", "dxy_level",
    # Macro (5)
    "fedfunds", "fedfunds_chg_21",
    "yield_curve", "yield_curve_chg_5", "yield_inverted",
    # Calendar (7)
    "dow", "month", "month_sin", "month_cos",
    "is_weekend", "is_monday", "days_into_month",
    # Optional (sparse when ETH data not present)
    "eth_ret_1d", "eth_ret_5d", "btc_eth_rs_5d", "btc_gld_corr_21",
]

# Features that may be NaN for early rows or missing data sources.
# These get fillna(median) before dropna on core features.
SPARSE_FEATURES = [
    # On-chain only available from 2010-2011 onward
    "btc_mvrv", "btc_nupl", "btc_mvrv_z63", "btc_mvrv_z252", "btc_mvrv_chg_21",
    "btc_realized_mvrv", "btc_realized_mvrv_chg_21",
    "btc_blkcnt_ratio", "btc_blkcnt_z21",
    "btc_dominance", "btc_dominance_chg_21", "btc_dominance_z63",
    "btc_s2f_dev", "btc_s2f_dev_z63",
    # ETH features (optional - only if ETH_max_bars.csv exists)
    "eth_ret_1d", "eth_ret_5d", "btc_eth_rs_5d",
    # Cross-asset: may be missing before 2016
    "spy_ret_1d", "spy_ret_5d", "spy_z21",
    "gld_ret_1d", "gld_ret_5d", "gld_z21",
    "uso_ret_1d", "uso_z21",
    # GLD/BTC corr - needs 21 rows
    "btc_gld_corr_21",
]

# GBM feature set: exclude ETH pair features (optional)
# GBM is more prone to overfitting on optional/sparse features
GBM_FEATURES = [f for f in BTC_FEATURES if f not in [
    "eth_ret_1d", "eth_ret_5d",  # ETH may not be present
]]

# ── val window (BTC trades 365d/year, not 252 trading days) ───────────────────
VAL_DAYS = 365

# ── training runs ─────────────────────────────────────────────────────────────
#  (target, model_type, val_days, description, feature_set)
RUNS = [
    ("next_ret_1d", "ridge",    VAL_DAYS, "Linear baseline", BTC_FEATURES),
    ("next_ret_1d", "gbm",      VAL_DAYS, "Nonlinear return", GBM_FEATURES),
    ("next_dir_1d", "logistic", VAL_DAYS, "Direction logistic", BTC_FEATURES),
    ("next_dir_1d", "gbm",      VAL_DAYS, "Direction GBM", GBM_FEATURES),
    ("next_ret_5d", "gbm",      VAL_DAYS, "5d return GBM", GBM_FEATURES),
]


def make_model(model_type, target):
    """Build sklearn Pipeline for a given model type and target."""
    if model_type == "ridge":
        return Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1.0))])
    elif model_type == "gbm" and "dir" in target:
        # GBM Classifier - tightened regularization for crypto noise
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model", GradientBoostingClassifier(
                n_estimators=300, learning_rate=0.03,
                max_depth=2, min_samples_leaf=50,
                subsample=0.7, random_state=42
            ))
        ])
    elif model_type == "gbm":
        # GBM Regressor
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model", GradientBoostingRegressor(
                n_estimators=200, learning_rate=0.05,
                max_depth=3, min_samples_leaf=20,
                subsample=0.8, random_state=42
            ))
        ])
    elif model_type == "logistic":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=0.1, max_iter=1000, random_state=42))
        ])
    raise ValueError(f"Unknown model type: {model_type}")


def train_one(df_raw, target, model_type, feat_cols, val_days, notes=""):
    """
    Train a single model. Returns (model, metrics, train_df, val_df, avail_features).
    Uses time-based split: last val_days rows = val set.
    """
    # Intersect with available columns
    avail = [f for f in feat_cols if f in df_raw.columns]
    missing = [f for f in feat_cols if f not in df_raw.columns]
    if missing:
        print(f"  [info] {len(missing)} requested features not in CSV (skipping): {missing[:5]}{'...' if len(missing) > 5 else ''}")

    # Sparse feature imputation (before dropna)
    df = df_raw[avail + [target, "date"]].copy()
    sparse_avail = [f for f in SPARSE_FEATURES if f in avail]
    for col in sparse_avail:
        med = df[col].median()
        df[col] = df[col].fillna(med)

    # Drop rows with NaN in non-sparse features or target
    core_avail = [f for f in avail if f not in SPARSE_FEATURES]
    df = df.dropna(subset=core_avail + [target])
    df = df.sort_values("date").reset_index(drop=True)

    # Time-based split: last val_days rows = val
    cutoff = df["date"].max() - pd.Timedelta(days=val_days)
    train_df = df[df["date"] <= cutoff].copy()
    val_df   = df[df["date"] >  cutoff].copy()

    if len(train_df) < 100:
        print(f"  [warn] Only {len(train_df)} training rows for {target}/{model_type}. Skipping.")
        return None, None, None, None, avail

    print(f"  {target}/{model_type}: train={len(train_df)}, val={len(val_df)}, feats={len(avail)}")

    X_train = train_df[avail].values
    y_train = train_df[target].values
    X_val   = val_df[avail].values
    y_val   = val_df[target].values

    model = make_model(model_type, target)
    model.fit(X_train, y_train)

    # Metrics
    metrics = {"train_rows": len(train_df), "val_rows": len(val_df)}

    if model_type == "logistic" or (model_type == "gbm" and "dir" in target):
        y_pred = model.predict(X_val)
        y_prob = model.predict_proba(X_val)[:, 1]
        metrics["val_dir_acc"]  = float(accuracy_score(y_val, y_pred))
        metrics["val_auc"]      = float(roc_auc_score(y_val, y_prob))
        metrics["val_brier"]    = float(brier_score_loss(y_val, y_prob))
        metrics["val_spearman"] = float(stats.spearmanr(y_val, y_prob).statistic)
        metrics["val_rmse"]     = float(np.nan)
        metrics["val_mae"]      = float(np.nan)
        metrics["val_r2"]       = float(np.nan)
    else:
        y_pred = model.predict(X_val)
        metrics["val_rmse"]     = float(np.sqrt(mean_squared_error(y_val, y_pred)))
        metrics["val_mae"]      = float(mean_absolute_error(y_val, y_pred))
        metrics["val_r2"]       = float(r2_score(y_val, y_pred))
        metrics["val_spearman"] = float(stats.spearmanr(y_val, y_pred).statistic)
        metrics["val_dir_acc"]  = float(np.mean(np.sign(y_pred) == np.sign(y_val)))
        metrics["val_auc"]      = float(np.nan)
        metrics["val_brier"]    = float(np.nan)

    # Feature importances
    inner = model.named_steps["model"]
    if hasattr(inner, "feature_importances_"):
        imps = inner.feature_importances_
        top5 = sorted(zip(avail, imps), key=lambda x: -x[1])[:5]
        metrics["top5_features"] = ", ".join(f"{n}({v:.3f})" for n, v in top5)
    elif hasattr(inner, "coef_"):
        coefs = np.abs(inner.coef_.flatten())
        top5  = sorted(zip(avail, coefs), key=lambda x: -x[1])[:5]
        metrics["top5_features"] = ", ".join(f"{n}({v:.3f})" for n, v in top5)
    else:
        metrics["top5_features"] = ""

    return model, metrics, train_df, val_df, avail


def save_model(model, model_type, target, trained_features=None):
    fname = f"btc_{model_type}_{target}_{TODAY}.pkl"
    path  = os.path.join(MODEL_DIR, fname)
    with open(path, "wb") as fh:
        pickle.dump({
            "model":       model,
            "model_type":  model_type,
            "target":      target,
            "features":    trained_features or BTC_FEATURES,
            "trained_on":  TODAY,
        }, fh)
    return path


def log_run(target, model_type, metrics, notes):
    row = {
        "model_id":        f"btc_{model_type}_{target}_{TODAY}",
        "target":          target,
        "model_type":      model_type,
        "train_date":      TODAY,
        "train_rows":      metrics.get("train_rows", ""),
        "val_rows":        metrics.get("val_rows", ""),
        "val_window_days": VAL_DAYS,
        "val_spearman":    metrics.get("val_spearman", ""),
        "val_rmse":        metrics.get("val_rmse", ""),
        "val_mae":         metrics.get("val_mae", ""),
        "val_r2":          metrics.get("val_r2", ""),
        "val_dir_acc":     metrics.get("val_dir_acc", ""),
        "val_auc":         metrics.get("val_auc", ""),
        "val_brier":       metrics.get("val_brier", ""),
        "top5_features":   metrics.get("top5_features", ""),
        "notes":           notes,
    }
    if os.path.exists(LOG_PATH):
        log = pd.read_csv(LOG_PATH)
        log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    else:
        log = pd.DataFrame([row])
    log.to_csv(LOG_PATH, index=False)
    return row


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", default="", help="Describe what changed in this run")
    args = parser.parse_args()

    print(f"Loading features from {FEAT_PATH}...")
    if not os.path.exists(FEAT_PATH):
        print("ERROR: btc_features.csv not found. Run buildBtcFeatures.py first.")
        sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    print(f"Loaded {len(df)} rows x {len(df.columns)} columns")
    print(f"Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    print()

    results = []
    for target, model_type, val_days, desc, feat_cols in RUNS:
        print(f"Training: {desc} ({target} / {model_type})")
        model, metrics, train_df, val_df, avail = train_one(
            df, target, model_type, feat_cols, val_days, args.notes
        )
        if model is None:
            print("  Skipped.\n")
            continue

        path = save_model(model, model_type, target, trained_features=avail)
        row  = log_run(target, model_type, metrics, args.notes)
        results.append(row)

        # Summary line
        if model_type in ("logistic",) or (model_type == "gbm" and "dir" in target):
            print(f"  -> dir_acc={metrics['val_dir_acc']:.3f}  AUC={metrics['val_auc']:.3f}  "
                  f"Spearman={metrics['val_spearman']:.3f}")
        else:
            print(f"  -> dir_acc={metrics['val_dir_acc']:.3f}  "
                  f"Spearman={metrics['val_spearman']:.3f}  RMSE={metrics['val_rmse']:.4f}")
        print(f"  Top features: {metrics['top5_features']}")
        print(f"  Saved: {path}")
        print()

    # Summary table
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Target':<18} {'Model':<12} {'Dir Acc':<10} {'AUC':<8} {'Spearman':<12} {'Top feature'}")
    print("-" * 80)
    for r in results:
        da  = f"{r['val_dir_acc']:.3f}" if r['val_dir_acc'] != "" else "N/A"
        auc = f"{r['val_auc']:.3f}" if str(r['val_auc']) not in ("", "nan") else "-"
        sp  = f"{r['val_spearman']:.3f}" if r['val_spearman'] != "" else "N/A"
        top = r['top5_features'].split(",")[0] if r['top5_features'] else "-"
        print(f"{r['target']:<18} {r['model_type']:<12} {da:<10} {auc:<8} {sp:<12} {top}")

    print(f"\nExperiment log: {LOG_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
