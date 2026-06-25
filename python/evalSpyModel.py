#!/usr/bin/env python3
"""
evalSpyModel.py
===============
Re-runs the train/val time split for SPY models, generates val-set predictions,
and writes diagnostic CSVs for the R plotting script.

Mirrors the pattern of evalFantasyModel.py exactly.

Outputs (one per model combo):
  outputs/features/markets/eval_spy_{target}_{model_type}.csv
  Columns: date, actual, predicted, residual, prob_up,
           SPY_ret, vix_level, vol_regime, spy_ret_r5

Also writes:
  outputs/features/markets/eval_spy_experiment_summary.csv
  Columns: model_id, target, model_type, train_date, train_rows, val_rows,
           val_rmse, val_mae, val_r2, val_spearman, val_dir_acc, val_auc,
           top5_features

Also copies:
  models/meta/spy_experiment_log.csv -> outputs/features/markets/spy_experiment_log_copy.csv
"""

import os
import glob
import pickle
import shutil

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    mean_squared_error, mean_absolute_error, r2_score,
    accuracy_score, roc_auc_score,
)

# ── paths ──────────────────────────────────────────────────────────────────────
BASE      = os.path.expanduser("~/discordBot")
FEAT_PATH = os.path.join(BASE, "outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.join(BASE, "models/markets/spy")
LOG_PATH  = os.path.join(BASE, "models/meta/spy_experiment_log.csv")
OUT_DIR   = os.path.join(BASE, "outputs/features/markets")

os.makedirs(OUT_DIR, exist_ok=True)

# ── val split size — must match trainSpyModel.py ──────────────────────────────
VAL_DAYS = 252

# ── model configs ─────────────────────────────────────────────────────────────
# (target, model_type)
CONFIGS = [
    ("next_ret_1d",  "ridge"),
    ("next_ret_1d",  "gbm"),
    ("next_dir_1d",  "logistic"),
    ("next_dir_1d",  "gbm_ensemble"),
    ("next_ret_5d",  "gbm"),
]

# Sparse features: impute with column median before dropna on core features.
# Must match SPARSE_FEATURES in trainSpyModel.py exactly.
SPARSE_FEATURES = [
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    "XLB_ret", "XLC_ret", "XLE_ret", "XLF_ret", "XLI_ret",
    "XLK_ret", "XLP_ret", "XLRE_ret", "XLU_ret", "XLV_ret", "XLY_ret",
    "sector_risk_on_r5", "sector_risk_off_r5", "sector_rotation_r5",
    "xlf_spy_rs_5d", "xle_spy_rs_5d",
    "sector_dispersion_1d", "sector_dispersion_r5",
    # VWAP / intraday — 52% coverage (pre-2021 gap)
    "vwap_dev_close", "vwap_dev_open",
    "vwap_cross_count", "vwap_time_above_pct",
    "high_vol_above_vwap", "vol_concentration",
    "vwap_dev_z21", "vwap_dev_r5", "vol_vwap_corr_5d",
    "vwap_dev_am", "open_drive_flag", "late_reversal_flag",
    "am_range", "pm_range", "gap_fill_flag", "vol_am_pct",
    "premarket_ret", "premarket_vol_ratio", "overnight_gap",
    # Block signals
    "block_active_flag", "block_dollar_flow_5d",
    "block_net_direction_5d", "block_dev_mean_5d",
    "block_dark_pool_5d", "days_since_last_block",
    "block_highdev_5d",
    "first_hour_ret", "last_hour_ret",
    # Regime interaction features (inherit sparsity from intraday parents)
    "open_drive_x_bear", "open_drive_x_bull",
    "dix_chg_x_bear", "dix_chg_x_bull",
    "sector_riskoff_x_bear",
    "mom_x_regime_stability", "mom63_x_regime_stability",
    "vix_rv_x_bear", "vix_rv_x_chop",
    # Moon phase (dense, listed for safety)
    "moon_phase", "moon_phase_sin", "moon_phase_cos",
    "days_to_full_moon", "days_to_new_moon",
    "moon_full_flag", "moon_new_flag",
    # Weather — occasional missing days
    "tavg_nyc", "temp_range_nyc", "prcp_flag_nyc",
    "snow_flag_nyc", "sunshine_proxy_nyc",
    "tavg_chi", "temp_range_chi", "prcp_flag_chi", "sunshine_proxy_chi",
    # Wikipedia — missing on market holidays
    "wiki_stock_market_views", "wiki_sp500_views", "wiki_economy_views",
    "wiki_total_views", "wiki_views_z21",
    # Holidays — dense but listed for safety
    "is_pre_holiday", "is_post_holiday", "days_to_next_holiday", "holiday_week",
    # MTA — only from 2020-03-01
    "mta_subway_riders", "mta_bus_riders", "mta_total_riders", "mta_riders_z21",
    # Reddit WSB — 2018+, sparse before 2020
    "wsb_avg_score", "wsb_avg_comments", "wsb_score_z21",
    # Alt data 2
    "daylight_hours_nyc", "daylight_chg_7d", "daylight_z365",
    "earnings_season_flag", "days_to_earnings_season",
    "pres_approval", "pres_approval_chg_21d",
    "cboe_pcr_equity",
    # Google Trends (weekly interpolated, +2bd lag)
    "trends_spy", "trends_crash", "trends_recession", "trends_buy_stocks",
    "trends_volatility", "trends_fear_index", "trends_fear_z21",
    "trends_bankruptcy", "trends_unemployment", "trends_payday_loan",
    "trends_margin_call", "trends_how_invest",
    "trends_distress_index", "trends_risk_appetite_index",
    # Sentiment
    "aaii_bullish", "aaii_bearish", "aaii_bull_bear_spread", "aaii_bull_z8",
    "cfg_value", "cfg_z21", "cfg_extreme_fear", "cfg_greed",
    "icsa_z52", "icsa_chg_4w", "ccsa_z52", "umcsent_z12", "umcsent_chg_3m",
    # Warmup-window NaN features
    "vix_z252", "vvix_z21", "vvix_chg_5d", "vix_term_slope", "vix_term_z21",
    "vix_spike_flag", "vix_ma20_ratio", "vwap_dev_close",
    "gld_corr_x_vol", "gld_spy_corr_63", "gld_corr63_x_era",
    # Rate shock / dist regime / interaction features
    "rate_chg_63d", "rate_chg_252d", "rate_shock_flag", "rate_easing_flag",
    "rate_speed_z63", "rate_shock_x_bear", "rate_speed_x_bear",
    "ret_skew_21", "ret_skew_63", "ret_kurt_21", "ret_skew_z21",
    "drawdown_63d", "regime_age_z",
    "fear_z21_x_bear", "fear_z21_x_bull", "fear_z21_x_chop",
    "crash_x_bear", "recession_x_bear",
    "vol_x_bear", "buy_x_bull",
    "distress_x_bear", "distress_x_chop",
    "aaii_spread_x_bear", "aaii_spread_x_bull",
    "cfg_z21_x_bear", "cfg_z21_x_bull",
    "icsa_z52_x_bear", "icsa_z52_x_chop",
    "fear_z21_x_rate_shock", "recession_x_rate_shock", "icsa_x_rate_shock",
]

# Context columns included in each eval CSV (when present in spy_features.csv)
CONTEXT_COLS = ["SPY_ret", "vix_level", "vol_regime", "spy_ret_r5"]


# ── helpers ────────────────────────────────────────────────────────────────────

def load_latest_bundle(target, model_type):
    """Load the most recent pkl for a given (target, model_type) combo."""
    pattern = os.path.join(MODEL_DIR, f"spy_{model_type}_{target}_*.pkl")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    with open(files[-1], "rb") as fh:
        return pickle.load(fh)


def prepare_dataframe(df_raw, bundle_features, target):
    """
    Apply the same pre-processing as trainSpyModel.train_one():
      1. Restrict to features the model knows about (avail).
      2. Impute sparse features with column median.
      3. Drop rows with NaN in core (non-sparse) features or target.
      4. Sort by date.
    Returns (df_clean, avail) where avail is the intersection of
    bundle_features and df columns.
    """
    df = df_raw.copy()

    # Features the model was actually trained on that exist in the current CSV
    avail = [f for f in bundle_features if f in df.columns]

    # Impute sparse features with median
    for col in SPARSE_FEATURES:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

    # Drop rows with NaN in core features or target
    core = [f for f in avail if f not in SPARSE_FEATURES]
    df   = df.dropna(subset=core + [target])

    df = df.sort_values("date").reset_index(drop=True)
    return df, avail


def rebuild_val_split(df, avail, target):
    """
    Replicate trainSpyModel.train_one() split logic exactly:
      val  = last VAL_DAYS rows
      train = everything before
    Returns (train_df, val_df).
    """
    split = len(df) - VAL_DAYS
    if split < 200:
        split = int(len(df) * 0.8)
    train_df = df.iloc[:split].copy()
    val_df   = df.iloc[split:].copy()
    return train_df, val_df


def is_classifier(target, model_type):
    return target == "next_dir_1d"


def run_inference(bundle, val_df, avail, target):
    """
    Run model inference on val set.
    Handles both single model (bundle['model']) and ensemble (bundle['models']).
    Classifiers: predicted = predict_proba[:,1], actual = binary label (0/1).
    Regressors:  predicted = predict(),          actual = continuous return.
    Returns a dict with keys: actual, predicted, prob_up (NaN for regressors).
    """
    X_val = val_df[avail].values
    y_val = val_df[target].values

    # Support both single model and ensemble (list of models)
    if "models" in bundle:
        models = bundle["models"]
        if target == "next_dir_1d":
            prob_up   = np.mean([m.predict_proba(X_val)[:, 1] for m in models], axis=0)
            predicted = prob_up
            actual    = y_val.astype(float)
        else:
            predicted = np.mean([m.predict(X_val) for m in models], axis=0)
            actual    = y_val.astype(float)
            prob_up   = np.full(len(actual), np.nan)
    else:
        model = bundle["model"]
        if target == "next_dir_1d":
            prob_up   = model.predict_proba(X_val)[:, 1]
            predicted = prob_up
            actual    = y_val.astype(float)
        else:
            predicted = model.predict(X_val)
            actual    = y_val.astype(float)
            prob_up   = np.full(len(actual), np.nan)

    return {
        "actual":    actual,
        "predicted": predicted,
        "prob_up":   prob_up,
    }


def compute_metrics(actual, predicted, prob_up, target):
    """Compute the full metric set appropriate for the model type."""
    m = {}
    if target == "next_dir_1d":
        pred_label = (predicted >= 0.5).astype(int)
        m["val_dir_acc"]  = round(float(accuracy_score(actual, pred_label)), 4)
        m["val_auc"]      = round(float(roc_auc_score(actual, predicted)), 4)
        m["val_spearman"] = round(float(stats.spearmanr(actual, predicted).statistic), 4)
        # Compute RMSE/MAE/R2 on proba vs binary label for completeness
        m["val_rmse"] = round(float(np.sqrt(mean_squared_error(actual, predicted))), 6)
        m["val_mae"]  = round(float(mean_absolute_error(actual, predicted)), 6)
        m["val_r2"]   = round(float(r2_score(actual, predicted)), 4)
    else:
        m["val_rmse"]     = round(float(np.sqrt(mean_squared_error(actual, predicted))), 6)
        m["val_mae"]      = round(float(mean_absolute_error(actual, predicted)), 6)
        m["val_r2"]       = round(float(r2_score(actual, predicted)), 4)
        m["val_spearman"] = round(float(stats.spearmanr(actual, predicted).statistic), 4)
        m["val_dir_acc"]  = round(float(np.mean(np.sign(actual) == np.sign(predicted))), 4)
        m["val_auc"]      = None
    return m


def get_top5_features(bundle, avail):
    """Extract top-5 feature names from model coefficients/importances."""
    # Handle ensemble (list of models) — average importances
    if "models" in bundle:
        models = bundle["models"]
        inners = [m.named_steps.get("m") if hasattr(m, "named_steps") else m for m in models]
        if hasattr(inners[0], "feature_importances_"):
            imps    = np.mean([m.feature_importances_ for m in inners], axis=0)
            top_idx = np.argsort(imps)[::-1][:5]
            return "; ".join([f"{avail[i]}={imps[i]:.3f}" for i in top_idx])
        return ""
    model = bundle["model"]
    inner = model.named_steps.get("m") if hasattr(model, "named_steps") else model
    top5  = []
    if hasattr(inner, "feature_importances_"):
        imps    = inner.feature_importances_
        top_idx = np.argsort(imps)[::-1][:5]
        top5    = [f"{avail[i]}={imps[i]:.3f}" for i in top_idx]
    elif hasattr(inner, "coef_"):
        coefs   = np.abs(inner.coef_.ravel())
        top_idx = np.argsort(coefs)[::-1][:5]
        top5    = [f"{avail[i]}={coefs[i]:.3f}" for i in top_idx]
    return "; ".join(top5)


def get_model_id_from_log(target, model_type):
    """Try to pull the most recent model_id from the experiment log."""
    if not os.path.exists(LOG_PATH):
        return f"spy_{model_type}_{target}_unknown"
    log = pd.read_csv(LOG_PATH)
    mask = (log["target"] == target) & (log["model_type"] == model_type)
    if not mask.any():
        return f"spy_{model_type}_{target}_unknown"
    row = log[mask].sort_values("train_date").iloc[-1]
    return row["model_id"]


def get_train_date_from_log(target, model_type):
    if not os.path.exists(LOG_PATH):
        return "unknown"
    log = pd.read_csv(LOG_PATH)
    mask = (log["target"] == target) & (log["model_type"] == model_type)
    if not mask.any():
        return "unknown"
    row = log[mask].sort_values("train_date").iloc[-1]
    return row["train_date"]


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("[evalSpyModel] generating validation diagnostics...\n")

    # Load feature matrix once
    if not os.path.exists(FEAT_PATH):
        print(f"  ERROR: {FEAT_PATH} not found -- run buildSpyFeatures.py first")
        return

    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    print(f"  loaded {len(df_raw)} rows, "
          f"{df_raw['date'].min().date()} to {df_raw['date'].max().date()}\n")

    all_summaries = []

    for target, model_type in CONFIGS:
        label = f"spy_{model_type}_{target}"
        print(f"  {label}...")

        # ── 1. Load model bundle ───────────────────────────────────────────────
        bundle = load_latest_bundle(target, model_type)
        if bundle is None:
            print(f"    SKIP - no pkl found in {MODEL_DIR}")
            continue

        # ── 2. Prepare dataframe (same pre-proc as train_one) ──────────────────
        try:
            df_clean, avail = prepare_dataframe(df_raw, bundle["features"], target)
        except Exception as e:
            print(f"    ERROR preparing data: {e}")
            continue

        if len(df_clean) < 300:
            print(f"    SKIP - only {len(df_clean)} clean rows (need >= 300)")
            continue

        # ── 3. Rebuild train/val split ─────────────────────────────────────────
        train_df, val_df = rebuild_val_split(df_clean, avail, target)

        # ── 4. Run inference on val set ────────────────────────────────────────
        try:
            preds = run_inference(bundle, val_df, avail, target)
        except Exception as e:
            print(f"    ERROR during inference: {e}")
            continue

        actual    = preds["actual"]
        predicted = preds["predicted"]
        prob_up   = preds["prob_up"]
        residual  = actual - predicted

        # ── 5. Build output dataframe ──────────────────────────────────────────
        out = val_df[["date"]].copy().reset_index(drop=True)
        out["actual"]    = actual
        out["predicted"] = predicted
        out["residual"]  = residual
        out["prob_up"]   = prob_up

        # Append context columns that exist in the val slice
        for col in CONTEXT_COLS:
            if col in val_df.columns:
                out[col] = val_df[col].values

        out_path = os.path.join(OUT_DIR, f"eval_spy_{target}_{model_type}.csv")
        out.to_csv(out_path, index=False)

        # ── 6. Compute metrics ─────────────────────────────────────────────────
        m = compute_metrics(actual, predicted, prob_up, target)

        # ── 7. Print diagnostics ───────────────────────────────────────────────
        if target == "next_dir_1d":
            print(f"    train rows={len(train_df)}  val rows={len(val_df)}")
            print(f"    dir_acc={m['val_dir_acc']}  AUC={m['val_auc']}  "
                  f"Spearman={m['val_spearman']}")
        else:
            print(f"    train rows={len(train_df)}  val rows={len(val_df)}")
            print(f"    Spearman={m['val_spearman']}  dir_acc={m['val_dir_acc']}  "
                  f"RMSE={m['val_rmse']}  R2={m['val_r2']}")

        print(f"    -> {out_path}")

        # ── 8. Accumulate summary row ──────────────────────────────────────────
        model_id   = get_model_id_from_log(target, model_type)
        train_date = get_train_date_from_log(target, model_type)
        top5       = get_top5_features(bundle, avail)

        all_summaries.append({
            "model_id":      model_id,
            "target":        target,
            "model_type":    model_type,
            "train_date":    train_date,
            "train_rows":    len(train_df),
            "val_rows":      len(val_df),
            "val_rmse":      m["val_rmse"],
            "val_mae":       m["val_mae"],
            "val_r2":        m["val_r2"],
            "val_spearman":  m["val_spearman"],
            "val_dir_acc":   m["val_dir_acc"],
            "val_auc":       m["val_auc"],
            "top5_features": top5,
        })

    # ── 9. Write experiment summary ────────────────────────────────────────────
    summary_df   = pd.DataFrame(all_summaries)
    summary_path = os.path.join(OUT_DIR, "eval_spy_experiment_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\n  summary -> {summary_path}")

    # ── 10. Copy experiment log for R trend chart ──────────────────────────────
    if os.path.exists(LOG_PATH):
        log_copy_path = os.path.join(OUT_DIR, "spy_experiment_log_copy.csv")
        shutil.copy2(LOG_PATH, log_copy_path)
        print(f"  experiment log -> {log_copy_path}")

    print("\n[evalSpyModel] done.")


if __name__ == "__main__":
    main()
