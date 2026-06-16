#!/usr/bin/env python3
"""
evalBtcModel.py
===============
Re-runs the train/val time split for BTC models, generates val-set predictions,
and writes diagnostic CSVs for the R plotting script.

Mirrors the pattern of evalSpyModel.py exactly.

Outputs (one per model combo):
  outputs/features/markets/eval_btc_{target}_{model_type}.csv
  Columns: date, actual, predicted, residual, prob_up,
           btc_ret, btc_mvrv, btc_vol_regime, btc_ret_r5, btc_nupl

Also writes:
  outputs/features/markets/eval_btc_experiment_summary.csv
  outputs/features/markets/btc_experiment_log_copy.csv
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

BASE      = os.path.expanduser("~/discordBot")
FEAT_PATH = os.path.join(BASE, "outputs/features/markets/btc_features.csv")
MODEL_DIR = os.path.join(BASE, "models/markets/btc")
LOG_PATH  = os.path.join(BASE, "models/meta/btc_experiment_log.csv")
OUT_DIR   = os.path.join(BASE, "outputs/features/markets")

os.makedirs(OUT_DIR, exist_ok=True)

VAL_DAYS = 365

CONFIGS = [
    ("next_ret_1d", "ridge"),
    ("next_ret_1d", "gbm"),
    ("next_dir_1d", "logistic"),
    ("next_dir_1d", "gbm"),
    ("next_ret_5d", "gbm"),
]

# Must match trainBtcModel.py SPARSE_FEATURES
SPARSE_FEATURES = [
    "btc_mvrv", "btc_nupl", "btc_mvrv_z63", "btc_mvrv_z252", "btc_mvrv_chg_21",
    "btc_realized_mvrv", "btc_realized_mvrv_chg_21",
    "btc_blkcnt_ratio", "btc_blkcnt_z21",
    "btc_dominance", "btc_dominance_chg_21", "btc_dominance_z63",
    "btc_s2f_dev", "btc_s2f_dev_z63",
    "eth_ret_1d", "eth_ret_5d", "btc_eth_rs_5d",
    "spy_ret_1d", "spy_ret_5d", "spy_z21",
    "gld_ret_1d", "gld_ret_5d", "gld_z21",
    "uso_ret_1d", "uso_z21",
    "btc_gld_corr_21",
]

CONTEXT_COLS = ["btc_ret", "btc_mvrv", "btc_vol_regime", "btc_ret_r5", "btc_nupl",
                "btc_halving_cycle_pct", "btc_days_since_halving", "btc_dominance",
                "btc_hashrate_growth_21"]


def load_latest_bundle(target, model_type):
    pattern = os.path.join(MODEL_DIR, f"btc_{model_type}_{target}_*.pkl")
    files   = sorted(glob.glob(pattern))
    if not files:
        return None
    with open(files[-1], "rb") as fh:
        return pickle.load(fh)


def prepare_dataframe(df_raw, bundle_features, target):
    avail = [f for f in bundle_features if f in df_raw.columns]
    # Only select avail + target + date + context cols that exist
    context_keep = [c for c in CONTEXT_COLS if c in df_raw.columns and c not in avail]
    select_cols  = list(dict.fromkeys(avail + [target, "date"] + context_keep))
    df = df_raw[select_cols].copy()

    sparse_avail = [f for f in SPARSE_FEATURES if f in avail]
    for col in sparse_avail:
        med = df[col].median()
        df[col] = df[col].fillna(med)

    core_avail = [f for f in avail if f not in SPARSE_FEATURES]
    df = df.dropna(subset=core_avail + [target])
    df = df.sort_values("date").reset_index(drop=True)
    return df, avail


def main():
    print(f"Loading {FEAT_PATH}...")
    if not os.path.exists(FEAT_PATH):
        print("ERROR: btc_features.csv not found. Run buildBtcFeatures.py first.")
        import sys; sys.exit(1)

    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    print(f"Loaded {len(df_raw)} rows")

    summary_rows = []

    for target, model_type in CONFIGS:
        print(f"\n{target} / {model_type}")
        bundle = load_latest_bundle(target, model_type)
        if bundle is None:
            print(f"  No saved model found for {target}/{model_type} in {MODEL_DIR}")
            continue

        bundle_features = bundle.get("features", [])
        model           = bundle["model"]
        trained_on      = bundle.get("trained_on", "unknown")

        df, avail = prepare_dataframe(df_raw, bundle_features, target)

        cutoff   = df["date"].max() - pd.Timedelta(days=VAL_DAYS)
        train_df = df[df["date"] <= cutoff].copy()
        val_df   = df[df["date"] >  cutoff].copy()

        if len(val_df) == 0:
            print(f"  No val rows. Skipping.")
            continue

        X_val = val_df[avail].values
        y_val = val_df[target].values

        is_clf = model_type == "logistic" or (model_type == "gbm" and "dir" in target)

        if is_clf:
            y_pred = model.predict(X_val)
            y_prob = model.predict_proba(X_val)[:, 1]
        else:
            y_pred = model.predict(X_val)
            y_prob = np.clip((y_pred - y_pred.min()) / (y_pred.max() - y_pred.min() + 1e-9), 0, 1)

        residuals = y_val - y_pred

        # Build val-only eval CSV (for held-out accuracy metrics)
        eval_df = val_df[["date"]].copy()
        eval_df["actual"]    = y_val
        eval_df["predicted"] = y_pred
        eval_df["residual"]  = residuals
        eval_df["prob_up"]   = y_prob
        eval_df["split"]     = "val"
        for c in CONTEXT_COLS:
            if c in val_df.columns:
                eval_df[c] = val_df[c].values

        out_csv = os.path.join(OUT_DIR, f"eval_btc_{target}_{model_type}.csv")
        eval_df.to_csv(out_csv, index=False)
        print(f"  Wrote {len(eval_df)} rows to {os.path.basename(out_csv)}")

        # Also write full-history predictions (train + val) for time-series diagnostics
        # This lets the R plot show the full 2014-present history
        X_all = df[avail].values
        y_all = df[target].values
        if is_clf:
            y_pred_all = model.predict(X_all)
            y_prob_all = model.predict_proba(X_all)[:, 1]
        else:
            y_pred_all = model.predict(X_all)
            y_prob_all = np.clip(
                (y_pred_all - y_pred_all.min()) / (y_pred_all.max() - y_pred_all.min() + 1e-9), 0, 1
            )
        full_df = df[["date"]].copy()
        full_df["actual"]    = y_all
        full_df["predicted"] = y_pred_all
        full_df["residual"]  = y_all - y_pred_all
        full_df["prob_up"]   = y_prob_all
        full_df["split"]     = np.where(df["date"] > cutoff, "val", "train")
        for c in CONTEXT_COLS:
            if c in df.columns:
                full_df[c] = df[c].values
        full_csv = os.path.join(OUT_DIR, f"eval_btc_{target}_{model_type}_full.csv")
        full_df.to_csv(full_csv, index=False)
        print(f"  Wrote {len(full_df)} rows (full history) to {os.path.basename(full_csv)}")

        # Metrics
        spearman  = stats.spearmanr(y_val, y_pred if not is_clf else y_prob).statistic
        dir_acc   = accuracy_score(y_val, y_pred) if is_clf else np.mean(np.sign(y_pred) == np.sign(y_val))
        auc       = roc_auc_score(y_val, y_prob) if is_clf else np.nan
        rmse      = np.sqrt(mean_squared_error(y_val, y_pred)) if not is_clf else np.nan
        mae       = mean_absolute_error(y_val, y_pred) if not is_clf else np.nan
        r2        = r2_score(y_val, y_pred) if not is_clf else np.nan

        inner = model.named_steps["model"]
        if hasattr(inner, "feature_importances_"):
            imps = inner.feature_importances_
            top5 = sorted(zip(avail, imps), key=lambda x: -x[1])[:5]
            top5_str = ", ".join(f"{n}({v:.3f})" for n, v in top5)
        elif hasattr(inner, "coef_"):
            coefs = np.abs(inner.coef_.flatten())
            top5  = sorted(zip(avail, coefs), key=lambda x: -x[1])[:5]
            top5_str = ", ".join(f"{n}({v:.3f})" for n, v in top5)
        else:
            top5_str = ""

        summary_rows.append({
            "model_id":    f"btc_{model_type}_{target}_{trained_on}",
            "target":      target,
            "model_type":  model_type,
            "train_date":  trained_on,
            "train_rows":  len(train_df),
            "val_rows":    len(val_df),
            "val_spearman": round(spearman, 4),
            "val_rmse":     round(rmse, 5) if not np.isnan(rmse) else None,
            "val_mae":      round(mae, 5)  if not np.isnan(mae)  else None,
            "val_r2":       round(r2, 4)   if not np.isnan(r2)   else None,
            "val_dir_acc":  round(dir_acc, 4),
            "val_auc":      round(auc, 4)  if not np.isnan(auc)  else None,
            "top5_features": top5_str,
        })
        print(f"  dir_acc={dir_acc:.3f}  "
              f"{'AUC=' + str(round(auc, 3)) + '  ' if not np.isnan(auc) else ''}"
              f"Spearman={spearman:.3f}")

    # Write summary
    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        summary_path = os.path.join(OUT_DIR, "eval_btc_experiment_summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print(f"\nWrote summary to {summary_path}")

    # Copy experiment log
    if os.path.exists(LOG_PATH):
        copy_path = os.path.join(OUT_DIR, "btc_experiment_log_copy.csv")
        shutil.copy2(LOG_PATH, copy_path)
        print(f"Copied experiment log to {copy_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
