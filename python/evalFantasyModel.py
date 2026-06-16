#!/usr/bin/env python3
"""
evalFantasyModel.py
===================
Re-runs the train/val split for each model, generates val-set predictions,
and writes diagnostics CSVs for the R plotting script.

Outputs (one per model combo):
  outputs/features/sports/eval_{player_type}_{horizon}_{model_type}.csv
  Columns: player_name, game_date, actual, predicted, residual, season,
           fantasy_position, season_ppg, fantasy_pts_r7

Also writes:
  outputs/features/sports/eval_experiment_summary.csv
  Columns: model_id, player_type, horizon, model_type, train_rows, val_rows,
           val_rmse, val_mae, val_r2, val_spearman, train_date
  (pulled directly from experiment_log.csv - no recompute needed)
"""

import os, pickle, glob
import pandas as pd
import numpy as np
from scipy import stats
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

BASE      = os.path.expanduser("~/discordBot")
FEAT_DIR  = os.path.join(BASE, "outputs/features/sports")
MODEL_DIR = os.path.join(BASE, "models/sports")
LOG_PATH  = os.path.join(BASE, "models/meta/experiment_log.csv")
OUT_DIR   = FEAT_DIR

VAL_DAYS  = 14  # must match trainFantasyModel.py

CONFIGS = [
    ("batters",  "daily",  "ridge"),
    ("batters",  "daily",  "gbm"),
    ("batters",  "weekly", "ridge"),
    ("batters",  "weekly", "gbm"),
    ("pitchers", "daily",  "ridge"),
    ("pitchers", "daily",  "gbm"),
    ("pitchers", "weekly", "ridge"),
    ("pitchers", "weekly", "gbm"),
]

HORIZONS = {"daily": "next_game_pts", "weekly": "next7_pts"}

def load_latest_bundle(player_type, horizon, model_type):
    pattern = os.path.join(MODEL_DIR, player_type,
                           f"{player_type}_{horizon}_{model_type}_*.pkl")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    with open(files[-1], "rb") as f:
        return pickle.load(f)

def rebuild_val_predictions(bundle):
    """Re-split features, predict on val set, return df with actuals + preds."""
    player_type  = bundle["player_type"]
    horizon      = bundle["horizon"]
    target_col   = bundle["target_col"]
    feature_cols = bundle["feature_cols"]
    model        = bundle["model"]

    feat_file = "batter" if player_type == "batters" else "pitcher"
    feat_path = os.path.join(FEAT_DIR, f"{feat_file}_features.csv")
    df = pd.read_csv(feat_path, parse_dates=["game_date"])

    df = df.dropna(subset=feature_cols + [target_col])
    available = [f for f in feature_cols if f in df.columns]
    df = df.dropna(subset=available + [target_col])

    cutoff = df["game_date"].max() - pd.Timedelta(days=VAL_DAYS)
    val_df = df[df["game_date"] > cutoff].copy()

    if len(val_df) < 10:
        cutoff = df["game_date"].max() - pd.Timedelta(days=21)
        val_df = df[df["game_date"] > cutoff].copy()

    X_val  = val_df[available].values
    y_val  = val_df[target_col].values
    y_pred = model.predict(X_val)

    val_df = val_df.copy()
    val_df["predicted"] = y_pred
    val_df["actual"]    = y_val
    val_df["residual"]  = y_val - y_pred
    val_df["abs_error"] = np.abs(val_df["residual"])

    keep = ["player_name", "game_date", "season", "fantasy_position",
            "actual", "predicted", "residual", "abs_error",
            "season_ppg", "fantasy_pts_r7", "team"]
    keep = [c for c in keep if c in val_df.columns]
    return val_df[keep].reset_index(drop=True)

def main():
    print("[evalFantasyModel] generating validation diagnostics...\n")
    all_summaries = []

    for player_type, horizon, model_type in CONFIGS:
        label = f"{player_type}_{horizon}_{model_type}"
        print(f"  {label}...")

        bundle = load_latest_bundle(player_type, horizon, model_type)
        if bundle is None:
            print(f"    SKIP - no model found")
            continue

        try:
            val_df = rebuild_val_predictions(bundle)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        out_path = os.path.join(OUT_DIR, f"eval_{label}.csv")
        val_df.to_csv(out_path, index=False)

        # Recompute metrics from val predictions
        rmse     = np.sqrt(mean_squared_error(val_df["actual"], val_df["predicted"]))
        mae      = mean_absolute_error(val_df["actual"], val_df["predicted"])
        r2       = r2_score(val_df["actual"], val_df["predicted"])
        spearman = stats.spearmanr(val_df["actual"], val_df["predicted"]).statistic

        print(f"    val rows={len(val_df)}  RMSE={rmse:.4f}  MAE={mae:.4f}  "
              f"Spearman={spearman:.4f}  R2={r2:.4f}")

        all_summaries.append({
            "model_id":     bundle["model_id"],
            "player_type":  player_type,
            "horizon":      horizon,
            "model_type":   model_type,
            "train_date":   bundle["train_date"],
            "train_rows":   bundle["metrics"]["train_rows"],
            "val_rows":     len(val_df),
            "val_rmse":     round(rmse, 4),
            "val_mae":      round(mae, 4),
            "val_r2":       round(r2, 4),
            "val_spearman": round(spearman, 4),
        })

    # Write summary
    summary_df = pd.DataFrame(all_summaries)
    summary_path = os.path.join(OUT_DIR, "eval_experiment_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\n  summary -> {summary_path}")

    # Also copy full experiment log for trend chart
    if os.path.exists(LOG_PATH):
        log_df = pd.read_csv(LOG_PATH)
        log_out = os.path.join(OUT_DIR, "experiment_log_copy.csv")
        log_df.to_csv(log_out, index=False)
        print(f"  experiment log -> {log_out}")

    print("\n[evalFantasyModel] done.")

if __name__ == "__main__":
    main()
