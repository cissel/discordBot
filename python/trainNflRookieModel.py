#!/usr/bin/env python3
"""
trainNflRookieModel.py

Trains the rookie-year prior model per position: predicts rookie-season
room40_pts using draft capital (round/pick), combine athletic testing, and
landing-spot opportunity (vacated target/snap share on their new team).

Much smaller sample than the main Phase 2 model (712 rookies total across
2015-2025 vs thousands of veteran player-seasons) - Ridge only, GBM/LightGBM
would overfit badly at this sample size per position (as few as ~80 QB
rookies over 11 draft classes). Time-based split: train on 2015-2023 draft
classes, validate on 2024-2025 (2 seasons combined for a large enough val set
given the small sample).

Usage: venv/bin/python3 python/trainNflRookieModel.py
"""
import os
import warnings
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=RuntimeWarning)

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
MODEL_DIR = os.path.expanduser("~/discordBot/models/sports/nfl")
META_DIR = os.path.expanduser("~/discordBot/models/meta")
os.makedirs(MODEL_DIR, exist_ok=True)

PANEL_PATH = os.path.join(OUT_DIR, "nfl_rookie_training_panel.csv")
EXPERIMENT_LOG = os.path.join(META_DIR, "nfl_rookie_experiment_log.csv")

POSITIONS = ["QB", "RB", "WR", "TE"]
VAL_SEASONS = [2024, 2025]

# draft capital is the single strongest predictor across all positions per
# public research - always include pick/round. Combine metrics are sparse
# (67.7% coverage) - median impute. Landing spot only covers 2019+ rookies
# (phase1 features start 2018, need prior season = 2018 minimum) - median
# impute for pre-2019 rookies (small number, mostly out of training relevance
# anyway given how much the league has changed).
FEATURE_COLS = [
    "pick", "round",
    "forty", "bench", "vertical", "broad_jump", "cone", "shuttle",
    "speed_score", "burst_score", "wt",
    "vacated_target_share", "vacated_snap_share",
]
SPARSE_FEATURES = [
    "forty", "bench", "vertical", "broad_jump", "cone", "shuttle",
    "speed_score", "burst_score", "wt",
    "vacated_target_share", "vacated_snap_share",
]
TARGET = "room40_pts"


def prep_xy(df, position):
    sub = df[df["position"] == position].copy()
    for c in SPARSE_FEATURES:
        if c in sub.columns:
            med = sub[c].median()
            if pd.isna(med):
                # entire position has no data for this metric (e.g. QBs almost
                # never do bench press at the combine) - fall back to 0 rather
                # than leaving NaN, which would crash Ridge's fit()
                med = 0.0
            sub[c] = sub[c].fillna(med)
    sub = sub.dropna(subset=["pick", "round", TARGET])
    X = sub[FEATURE_COLS].copy()
    X.columns = X.columns.astype(str)
    y = sub[TARGET]
    return sub, X, y


def train_one(df, position):
    sub, X, y = prep_xy(df, position)
    if len(sub) < 20:
        print(f"[{position}] Skipping - only {len(sub)} rows")
        return None

    train_mask = ~sub["season"].isin(VAL_SEASONS)
    val_mask = sub["season"].isin(VAL_SEASONS)
    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]

    if len(X_val) < 5:
        print(f"[{position}] Skipping - only {len(X_val)} val rows")
        return None

    ridge = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1.0))])
    ridge.fit(X_train, y_train)
    pred = ridge.predict(X_val)
    spearman = spearmanr(y_val, pred).correlation
    rmse = float(np.sqrt(np.mean((y_val - pred) ** 2)))
    mae = float(np.mean(np.abs(y_val - pred)))

    print(f"\n=== ROOKIE {position} (train={len(X_train)}, val={len(X_val)}) ===")
    print(f"  ridge  Spearman={spearman:.4f}  RMSE={rmse:.2f}  MAE={mae:.2f}")

    coefs = ridge.named_steps["model"].coef_
    imp_df = pd.DataFrame({"feature": FEATURE_COLS, "coef": coefs}).sort_values(
        "coef", key=abs, ascending=False)
    print(f"  Top 3 features (|coef|): {', '.join(imp_df.head(3)['feature'].tolist())}")

    train_date = datetime.now().strftime("%Y-%m-%d")
    model_id = f"nfl_rookie_{position.lower()}_ridge_{train_date}"
    bundle = {
        "model": ridge, "model_id": model_id, "position": position,
        "model_type": "ridge_rookie", "target_col": TARGET,
        "feature_cols": FEATURE_COLS, "train_date": train_date,
        "metrics": {"val_spearman": spearman, "val_rmse": rmse, "val_mae": mae,
                    "train_rows": len(X_train), "val_rows": len(X_val)},
    }
    joblib.dump(bundle, os.path.join(MODEL_DIR, f"{model_id}.pkl"))

    log_row = pd.DataFrame([{
        "model_id": model_id, "domain": "nfl_rookie", "position": position,
        "model_type": "ridge_rookie", "train_date": train_date,
        "train_rows": len(X_train), "val_rows": len(X_val),
        "val_rmse": rmse, "val_mae": mae, "val_spearman": spearman,
        "features_used": ",".join(FEATURE_COLS), "notes": "rookie prior model",
    }])
    if os.path.exists(EXPERIMENT_LOG):
        log_row.to_csv(EXPERIMENT_LOG, mode="a", header=False, index=False)
    else:
        log_row.to_csv(EXPERIMENT_LOG, index=False)

    return {"position": position, "spearman": spearman, "rmse": rmse, "mae": mae,
            "n_train": len(X_train), "n_val": len(X_val)}


def main():
    df = pd.read_csv(PANEL_PATH)
    results = []
    for pos in POSITIONS:
        r = train_one(df, pos)
        if r:
            results.append(r)

    print("\n" + "=" * 60)
    print("ROOKIE MODEL SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"  {r['position']:3s}  Spearman={r['spearman']:.4f}  "
              f"(train={r['n_train']}, val={r['n_val']})")


if __name__ == "__main__":
    main()
