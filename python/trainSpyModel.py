#!/usr/bin/env python3
"""
trainSpyModel.py
================
Trains directional and return-magnitude models for SPY next-day and next-5-day prediction.

Models:
  1. Ridge regression      — next_ret_1d  (baseline linear)
  2. GBM regression        — next_ret_1d  (captures nonlinear regime interactions)
  3. Logistic regression   — next_dir_1d  (directional accuracy, primary eval metric)
  4. GBM classifier        — next_dir_1d  (nonlinear direction)
  5. GBM regression        — next_ret_5d  (weekly horizon)

Evaluation:
  Regression:     Spearman rank correlation, RMSE, directional accuracy (sign)
  Classification: Accuracy, ROC-AUC, precision/recall, Brier score
  Walk-forward:   Expanding window (train on t, validate on t+1..t+252)
                  Primary: last 252 trading days (1 year) as val set
                  Secondary: last 504 days (2 years) — checks for regime shift

Outputs:
  models/markets/spy_<model_type>_<target>_<date>.pkl
  models/meta/spy_experiment_log.csv

Usage:
  venv/bin/python3 python/trainSpyModel.py [--notes "..."]
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

FEAT_PATH  = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR  = os.path.expanduser("~/discordBot/models/markets/spy")
LOG_PATH   = os.path.expanduser("~/discordBot/models/meta/spy_experiment_log.csv")
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

TODAY = datetime.date.today().isoformat()

# ── feature list ──────────────────────────────────────────────────────────────
SPY_FEATURES = [
    # SPY momentum & technicals
    "spy_ret_r5", "spy_ret_r10", "spy_ret_r21", "spy_ret_r63",
    "spy_vol_r5", "spy_vol_r21", "spy_vol_r63",
    "spy_rsi_14", "spy_rsi_3",
    "spy_bbpct_20",
    "spy_drawdown_252",
    "spy_mom_63_21",
    "spy_z5", "spy_z21",
    "spy_consec_up", "spy_consec_down",
    # Vol regime
    "vix_level", "vix_chg_1d", "vix_chg_5d",
    "vix_z21", "vix_z252", "vix_ma20_ratio",
    "rv_21", "vix_rv_ratio", "vol_regime",
    # Options (sparse — imputed when unavailable)
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    # Cross-asset
    "qqq_ret_1d", "qqq_ret_5d", "qqq_z21",
    "gld_ret_1d", "gld_ret_5d", "gld_z21",
    "uso_ret_1d", "uso_ret_5d", "uso_z21",
    "dxy_ret_1d", "dxy_ret_5d", "dxy_z21",
    "spy_qqq_rs_5d", "spy_qqq_rs_21d",
    "gld_spy_corr_21",
    # Macro
    "fedfunds", "fedfunds_chg_21d",
    "yield_curve", "yield_curve_chg_5", "yield_curve_z63",
    "yield_inverted",
    "dxy_level", "dxy_z63",
    # Macro event flags (deterministic, no leakage)
    "fomc_day", "fomc_before", "fomc_after", "fomc_window", "days_to_fomc",
    "cpi_day",  "cpi_before",  "cpi_after",  "cpi_window",  "days_to_cpi",
    "nfp_day",  "nfp_before",  "nfp_after",  "nfp_window",  "days_to_nfp",
    "macro_event_day", "macro_event_window", "days_to_any_macro",
    "fomc_hist_ret", "cpi_hist_ret", "nfp_hist_ret",
    # Calendar
    "dow", "month", "is_monday", "is_friday",
    "month_sin", "month_cos",
    "days_into_month", "is_qtr_end",
    # Sector rotation — composites only (individual ETF rets too noisy for GBM)
    "sector_risk_on_r5", "sector_risk_off_r5", "sector_rotation_r5",
    "xlf_spy_rs_5d", "xle_spy_rs_5d",
    "sector_dispersion_1d", "sector_dispersion_r5",
    # Individual sector ETF returns (useful for Logistic, kept but regularized)
    "XLB_ret", "XLC_ret", "XLE_ret", "XLF_ret", "XLI_ret",
    "XLK_ret", "XLP_ret", "XLRE_ret", "XLU_ret", "XLV_ret", "XLY_ret",
    # VWAP features (from fetchVwapDaily.py — dense once backfill runs, sparse before 2021)
    "vwap_dev_close", "vwap_dev_open",
    "vwap_cross_count", "vwap_time_above_pct",
    "high_vol_above_vwap", "vol_concentration",
    "vwap_dev_z21", "vwap_dev_r5", "vol_vwap_corr_5d",
    # Block order signals (sparse — only ~45 events since Jun 2025)
    "block_active_flag", "block_dollar_flow_5d",
    "block_net_direction_5d", "block_dev_mean_5d",
    "block_dark_pool_5d", "days_since_last_block",
    "block_highdev_5d",
    # Intraday aggregated features (dense after 1-min backfill 2021+)
    "first_hour_ret", "last_hour_ret", "am_range", "pm_range",
    "gap_fill_flag", "vwap_dev_am", "open_drive_flag", "vol_am_pct",
    "late_reversal_flag", "premarket_ret", "premarket_vol_ratio", "overnight_gap",
    # Order flow / CVD features (dense after trade backfill 2021+)
    "cvd_total", "cvd_normalized", "cvd_first_hour", "cvd_last_hour",
    "cvd_direction_flip", "large_cvd_total", "large_cvd_ratio",
    "cvd_z21", "large_cvd_z21",
    "cvd_momentum_ratio", "cvd_peak_hour",
    "buy_intensity", "sell_intensity", "intensity_ratio",
]

# Sparse features — imputed with median when unavailable
SPARSE_FEATURES = [
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    "XLB_ret", "XLC_ret", "XLE_ret", "XLF_ret", "XLI_ret",
    "XLK_ret", "XLP_ret", "XLRE_ret", "XLU_ret", "XLV_ret", "XLY_ret",
    "sector_risk_on_r5", "sector_risk_off_r5", "sector_rotation_r5",
    "xlf_spy_rs_5d", "xle_spy_rs_5d",
    "sector_dispersion_1d", "sector_dispersion_r5",
    # VWAP features — sparse pre-backfill, dense after
    "vwap_dev_close", "vwap_dev_open",
    "vwap_cross_count", "vwap_time_above_pct",
    "high_vol_above_vwap", "vol_concentration",
    "vwap_dev_z21", "vwap_dev_r5", "vol_vwap_corr_5d",
    # Block signals — sparse (45 events since Jun 2025, growing daily)
    "block_active_flag", "block_dollar_flow_5d",
    "block_net_direction_5d", "block_dev_mean_5d",
    "block_dark_pool_5d", "days_since_last_block",
    "block_highdev_5d",
    # Intraday features — sparse until 1-min backfill completes, dense after (2021+)
    "first_hour_ret", "last_hour_ret", "am_range", "pm_range",
    "gap_fill_flag", "vwap_dev_am", "open_drive_flag", "vol_am_pct",
    "late_reversal_flag", "premarket_ret", "premarket_vol_ratio", "overnight_gap",
    # Order flow / CVD features (sparse until backfill, dense after 2021+)
    "cvd_total", "cvd_normalized", "cvd_first_hour", "cvd_last_hour",
    "cvd_direction_flip", "large_cvd_total", "large_cvd_ratio",
    "cvd_z21", "large_cvd_z21",
    "cvd_momentum_ratio", "cvd_peak_hour",
    "buy_intensity", "sell_intensity", "intensity_ratio",
]

# GBM-specific feature list — drops individual sector ETF returns to reduce overfitting
GBM_FEATURES = [f for f in SPY_FEATURES if f not in
    ["XLB_ret","XLC_ret","XLE_ret","XLF_ret","XLI_ret",
     "XLK_ret","XLP_ret","XLRE_ret","XLU_ret","XLV_ret","XLY_ret"]]

LOG_COLS = [
    "model_id", "target", "model_type", "train_date",
    "train_rows", "val_rows", "val_window_days",
    "val_spearman", "val_rmse", "val_mae", "val_r2",
    "val_dir_acc", "val_auc", "val_brier",
    "top5_features", "notes",
]


def load_log():
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH)
    return pd.DataFrame(columns=LOG_COLS)


def append_log(row):
    log = load_log()
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)


def dir_accuracy(y_true, y_pred):
    """Fraction of correct sign predictions."""
    return np.mean(np.sign(y_true) == np.sign(y_pred))


def train_one(df, features, target, model_type, val_days=252, notes=""):
    """
    Expanding-window train/val split.
    Train: everything before last val_days trading days.
    Val: last val_days trading days.
    """
    df = df.copy()

    # Available features (gracefully handle missing columns)
    avail = [f for f in features if f in df.columns]

    # Impute sparse features with column median
    for col in SPARSE_FEATURES:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

    # Drop rows with NaN in core features or target
    core = [f for f in avail if f not in SPARSE_FEATURES]
    df   = df.dropna(subset=core + [target])

    if len(df) < 300:
        raise ValueError(f"Not enough rows after dropping NaN: {len(df)}")

    df   = df.sort_values("date")
    split = len(df) - val_days
    if split < 200:
        split = int(len(df) * 0.8)

    train_df = df.iloc[:split]
    val_df   = df.iloc[split:]

    X_train = train_df[avail].values
    y_train = train_df[target].values
    X_val   = val_df[avail].values
    y_val   = val_df[target].values

    is_clf = target == "next_dir_1d"

    if model_type == "ridge":
        model = Pipeline([("sc", StandardScaler()), ("m", Ridge(alpha=1.0))])
    elif model_type == "logistic":
        model = Pipeline([("sc", StandardScaler()),
                          ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
    elif model_type == "gbm" and is_clf:
        model = Pipeline([("sc", StandardScaler()),
                          ("m", GradientBoostingClassifier(
                              n_estimators=300, learning_rate=0.03,
                              max_depth=2, min_samples_leaf=50,
                              subsample=0.7, random_state=42))])
    elif model_type == "gbm":
        model = Pipeline([("sc", StandardScaler()),
                          ("m", GradientBoostingRegressor(
                              n_estimators=200, learning_rate=0.05,
                              max_depth=3, min_samples_leaf=20,
                              subsample=0.8, random_state=42))])
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    model.fit(X_train, y_train)

    # ── metrics ───────────────────────────────────────────────────────────────
    metrics = {
        "train_rows": len(train_df),
        "val_rows":   len(val_df),
        "val_window_days": val_days,
    }

    if is_clf:
        y_pred     = model.predict(X_val)
        y_prob     = model.predict_proba(X_val)[:, 1] if hasattr(model, "predict_proba") else y_pred
        metrics["val_dir_acc"] = round(accuracy_score(y_val, y_pred), 4)
        metrics["val_auc"]     = round(roc_auc_score(y_val, y_prob), 4)
        metrics["val_brier"]   = round(brier_score_loss(y_val, y_prob), 4)
        metrics["val_spearman"] = round(stats.spearmanr(y_val, y_prob).statistic, 4)
        metrics["val_rmse"] = metrics["val_mae"] = metrics["val_r2"] = None
    else:
        y_pred = model.predict(X_val)
        rmse   = np.sqrt(mean_squared_error(y_val, y_pred))
        metrics["val_rmse"]     = round(rmse, 6)
        metrics["val_mae"]      = round(mean_absolute_error(y_val, y_pred), 6)
        metrics["val_r2"]       = round(r2_score(y_val, y_pred), 4)
        metrics["val_spearman"] = round(stats.spearmanr(y_val, y_pred).statistic, 4)
        metrics["val_dir_acc"]  = round(dir_accuracy(y_val, y_pred), 4)
        metrics["val_auc"] = metrics["val_brier"] = None

    # Feature importances (GBM only)
    top5 = []
    inner = model.named_steps.get("m")
    if hasattr(inner, "feature_importances_"):
        imps = inner.feature_importances_
        top_idx = np.argsort(imps)[::-1][:5]
        top5 = [f"{avail[i]}={imps[i]:.3f}" for i in top_idx]
    elif hasattr(inner, "coef_"):
        coefs = np.abs(inner.coef_.ravel())
        top_idx = np.argsort(coefs)[::-1][:5]
        top5 = [f"{avail[i]}={coefs[i]:.3f}" for i in top_idx]

    metrics["top5_features"] = "; ".join(top5)

    return model, metrics, train_df, val_df, avail


def save_model(model, model_type, target, trained_features=None):
    fname = f"spy_{model_type}_{target}_{TODAY}.pkl"
    path  = os.path.join(MODEL_DIR, fname)
    with open(path, "wb") as f:
        pickle.dump({
            "model":       model,
            "features":    trained_features or SPY_FEATURES,  # store exact features used
            "trained_on":  TODAY,
        }, f)
    return path


def run_training(notes=""):
    print("[trainSpyModel] loading features...")
    if not os.path.exists(FEAT_PATH):
        print(f"  ERROR: {FEAT_PATH} not found — run buildSpyFeatures.py first")
        sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    print(f"  loaded {len(df)} rows, {df['date'].min().date()} to {df['date'].max().date()}")

    runs = [
        # (target,         model_type,   val_days, label,          feature_set)
        ("next_ret_1d",  "ridge",      252,  "1d return | Ridge",        SPY_FEATURES),
        ("next_ret_1d",  "gbm",        252,  "1d return | GBM",          GBM_FEATURES),
        ("next_dir_1d",  "logistic",   252,  "1d direction | Logistic",  SPY_FEATURES),
        ("next_dir_1d",  "gbm",        252,  "1d direction | GBM Clf",   GBM_FEATURES),
        ("next_ret_5d",  "gbm",        252,  "5d return | GBM",          GBM_FEATURES),
    ]

    results = []
    for target, model_type, val_days, label, feat_set in runs:
        print(f"\n{'='*56}")
        print(f"  {label}")
        print(f"{'='*56}")

        try:
            model, metrics, train_df, val_df, trained_feats = train_one(
                df, feat_set, target, model_type, val_days, notes
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        path = save_model(model, model_type, target, trained_features=trained_feats)
        print(f"  saved -> {path}")
        print(f"  train rows : {metrics['train_rows']} | val rows: {metrics['val_rows']}")

        if target == "next_dir_1d":
            print(f"  dir accuracy : {metrics['val_dir_acc']}  ← primary metric")
            print(f"  ROC-AUC      : {metrics['val_auc']}")
            print(f"  Brier score  : {metrics['val_brier']}")
            print(f"  Spearman     : {metrics['val_spearman']}")
        else:
            print(f"  Spearman     : {metrics['val_spearman']}  ← ranking accuracy")
            print(f"  Dir accuracy : {metrics['val_dir_acc']}")
            print(f"  RMSE         : {metrics['val_rmse']}")
            print(f"  R²           : {metrics['val_r2']}")

        if metrics["top5_features"]:
            print(f"  Top features : {metrics['top5_features']}")

        model_id = f"spy_{model_type}_{target}_{TODAY}"
        log_row  = {
            "model_id":        model_id,
            "target":          target,
            "model_type":      model_type,
            "train_date":      TODAY,
            "notes":           notes,
            **metrics,
        }
        append_log(log_row)
        results.append((label, metrics))

    # ── summary table ─────────────────────────────────────────────────────────
    print(f"\n{'='*56}")
    print("SUMMARY")
    print(f"{'='*56}")
    print(f"{'Model':<30} {'Dir Acc':>8} {'AUC':>8} {'Spearman':>10}")
    print("-" * 58)
    for label, m in results:
        da  = f"{m['val_dir_acc']:.4f}" if m.get('val_dir_acc') is not None else "  -   "
        auc = f"{m['val_auc']:.4f}"     if m.get('val_auc')     is not None else "  -   "
        sp  = f"{m['val_spearman']:.4f}"if m.get('val_spearman')is not None else "  -   "
        print(f"  {label:<28} {da:>8} {auc:>8} {sp:>10}")

    print(f"\n[trainSpyModel] done. Log: {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", type=str, default="")
    args = parser.parse_args()
    run_training(notes=args.notes)
