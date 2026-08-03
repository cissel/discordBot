#!/usr/bin/env python3
"""
trainNflFantasyModel.py

Phase 2 of the NFL moneyball project: season-long fantasy point projection
model. Mirrors the MLB pipeline architecture (Ridge + LightGBM, Spearman as
primary metric, time-based train/val split) adapted for NFL's season-level
(not game-level) prediction granularity.

Target: room40_pts (season total, Room 40 exact scoring) in season T.
Features: season T-1 usage (target share, snap share, volume), season T
coaching situation (coach scheme signature - built from strictly-prior
seasons, no leakage), age + aging-curve combined_index for season T, draft
capital, years of experience.

Train/val split: time-based. Train on seasons <= cutoff, validate on the
final season in the panel (2024) - same convention as MLB (never random
split for time series data).

Usage:
  venv/bin/python3 python/trainNflFantasyModel.py --notes "first NFL model"
  venv/bin/python3 python/trainNflFantasyModel.py --position QB
"""
import argparse
import os
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb
import joblib

warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
MODEL_DIR = os.path.expanduser("~/discordBot/models/sports/nfl")
META_DIR = os.path.expanduser("~/discordBot/models/meta")
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(META_DIR, exist_ok=True)

PANEL_PATH = os.path.join(OUT_DIR, "nfl_training_panel.csv")
EXPERIMENT_LOG = os.path.join(META_DIR, "nfl_experiment_log.csv")

POSITIONS = ["QB", "RB", "WR", "TE"]
VAL_SEASON = 2025  # most recent complete season in the panel

# Sparse features: coach signature is imputed (league-avg fallback) for rookie
# coordinators - same SPARSE_FEATURES median-impute pattern as the MLB pipeline's
# day-of matchup features. Core lag1_* usage features use dropna (they're the
# real signal; imputing usage would hide "this guy barely played last year").
SPARSE_FEATURES = [
    "coach_proe_signature", "coach_pace_signature", "coach_rz_signature",
    "coach_n_prior_seasons",
]

CORE_FEATURES = [
    "age", "draft_number", "years_exp",
    "lag1_targets", "lag1_receptions", "lag1_rec_yards", "lag1_air_yards",
    "lag1_rush_attempts", "lag1_target_share", "lag1_air_yards_share",
    "lag1_games_with_snaps", "lag1_snap_share_avg",
    "lag1_pts_per_game", "lag1_games",
    "combined_index",
    "new_oc", "scheme_uncertainty_flag",
    "team_changed", "team_changed_x_target_share",
]
# INJURY-BIAS FIX (2026-08-XX): lag1_room40_pts (last season's fantasy
# point TOTAL) used to be in this list and was consistently the #1/#2 most
# important feature for QB/WR models (33-41% GBM importance) - see
# importance_qb.csv / importance_wr.csv from the pre-fix training run.
# Season total = rate x games played, so it silently conflates "this
# player's underlying performance declined" with "this player missed
# games to injury" - a healthy elite player who tore an ACL in week 6 gets
# the same deflated total as a player who was genuinely bad all season.
# This produced false fades on Burrow (-170 value_gap), Purdy (-220), and
# Godwin (-135) in the 2026 board despite stable/improved per-game rates -
# see injury_shortened_2025 flag and the CAVEAT footnote that used to be
# needed on draft_board_2026.png before this fix.
# FIX: drop the season-total feature. The rate signal already lives in
# lag1_pts_per_game (games-neutral by construction); durability/injury-risk
# signal already lives in lag1_games as ITS OWN independent feature - the
# model can learn "missed a lot of games last year" as a distinct, weaker
# risk signal instead of having it silently baked into the production
# number. This is NOT the same as adding a "pace-adjusted" total
# (lag1_pts_per_game * 17), which would just be a redundant linear
# rescaling of lag1_pts_per_game already in the list above.

FEATURE_COLS = CORE_FEATURES + SPARSE_FEATURES
TARGET = "room40_pts"


def load_panel():
    df = pd.read_csv(PANEL_PATH)
    # draft_number: undrafted players are NaN - impute with a high number (like
    # "drafted very late"), a common convention, rather than dropping the row.
    df["draft_number"] = df["draft_number"].fillna(300)
    df["new_oc"] = df["new_oc"].fillna(0).astype(int)
    df["scheme_uncertainty_flag"] = df["scheme_uncertainty_flag"].fillna(False).astype(int)
    df["team_changed"] = df["team_changed"].fillna(False).astype(int)
    return df


def prep_xy(df, position):
    sub = df[df["position"] == position].copy()
    # sparse features: median impute
    for c in SPARSE_FEATURES:
        if c in sub.columns:
            sub[c] = sub[c].fillna(sub[c].median())
    # core features: dropna (real signal, don't fabricate)
    core_present = [c for c in CORE_FEATURES if c in sub.columns]
    sub = sub.dropna(subset=core_present + [TARGET])
    X = sub[FEATURE_COLS].copy()
    X.columns = X.columns.astype(str)  # keep as DataFrame with named cols throughout
    y = sub[TARGET]
    return sub, X, y


def train_one(df, position, notes):
    sub, X, y = prep_xy(df, position)
    if len(sub) < 30:
        print(f"[{position}] Skipping - only {len(sub)} rows after dropna (need >=30)")
        return None

    train_mask = sub["season"] < VAL_SEASON
    val_mask = sub["season"] == VAL_SEASON
    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]

    if len(X_val) < 5:
        print(f"[{position}] Skipping - only {len(X_val)} val rows in season {VAL_SEASON}")
        return None

    results = {}

    # ---- Ridge baseline ----
    ridge = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1.0))])
    ridge.fit(X_train, y_train)
    pred_ridge = ridge.predict(X_val)
    results["ridge"] = {
        "model": ridge,
        "spearman": spearmanr(y_val, pred_ridge).correlation,
        "rmse": float(np.sqrt(np.mean((y_val - pred_ridge) ** 2))),
        "mae": float(np.mean(np.abs(y_val - pred_ridge))),
    }

    # ---- GBM ----
    gbm = Pipeline([
        ("scaler", StandardScaler()),
        ("model", GradientBoostingRegressor(
            n_estimators=200, learning_rate=0.05, max_depth=4,
            min_samples_leaf=10, subsample=0.8, random_state=42))
    ])
    gbm.fit(X_train, y_train)
    pred_gbm = gbm.predict(X_val)
    results["gbm"] = {
        "model": gbm,
        "spearman": spearmanr(y_val, pred_gbm).correlation,
        "rmse": float(np.sqrt(np.mean((y_val - pred_gbm) ** 2))),
        "mae": float(np.mean(np.abs(y_val - pred_gbm))),
    }

    # ---- LightGBM (small dataset - conservative hyperparams, early stopping) ----
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)
    lgbm_model = lgb.LGBMRegressor(
        n_estimators=300, learning_rate=0.03, max_depth=4,
        num_leaves=15, min_child_samples=10, subsample=0.8,
        colsample_bytree=0.8, random_state=42, verbosity=-1)
    lgbm_model.fit(
        X_train_s, y_train,
        eval_set=[(X_val_s, y_val)],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(-1)],
    )
    pred_lgbm = lgbm_model.predict(X_val_s)
    lgbm_pipeline = Pipeline([("scaler", scaler), ("model", lgbm_model)])
    results["lgbm"] = {
        "model": lgbm_pipeline,
        "spearman": spearmanr(y_val, pred_lgbm).correlation,
        "rmse": float(np.sqrt(np.mean((y_val - pred_lgbm) ** 2))),
        "mae": float(np.mean(np.abs(y_val - pred_lgbm))),
    }

    print(f"\n=== {position} (train={len(X_train)}, val={len(X_val)}) ===")
    for name, r in results.items():
        print(f"  {name:6s}  Spearman={r['spearman']:.4f}  RMSE={r['rmse']:.2f}  MAE={r['mae']:.2f}")

    # feature importance from GBM (interpretable tree splits) - sanity check that
    # the model is leaning on real signal (usage/role) not noise
    gbm_importances = results["gbm"]["model"].named_steps["model"].feature_importances_
    imp_df = pd.DataFrame({"feature": FEATURE_COLS, "importance": gbm_importances})
    imp_df = imp_df.sort_values("importance", ascending=False)
    print(f"  Top 5 features (GBM importance): {', '.join(imp_df.head(5)['feature'].tolist())}")
    imp_df["position"] = position
    imp_df.to_csv(os.path.join(OUT_DIR, f"importance_{position.lower()}.csv"), index=False)

    # log every model to experiment log
    log_rows = []
    train_date = datetime.now().strftime("%Y-%m-%d")
    for model_type, r in results.items():
        model_id = f"nfl_{position.lower()}_{model_type}_{train_date}"
        bundle = {
            "model": r["model"],
            "model_id": model_id,
            "position": position,
            "model_type": model_type,
            "target_col": TARGET,
            "feature_cols": FEATURE_COLS,
            "train_date": train_date,
            "metrics": {
                "val_spearman": r["spearman"], "val_rmse": r["rmse"], "val_mae": r["mae"],
                "train_rows": len(X_train), "val_rows": len(X_val),
            },
        }
        joblib.dump(bundle, os.path.join(MODEL_DIR, f"{model_id}.pkl"))
        log_rows.append({
            "model_id": model_id, "domain": "nfl", "position": position,
            "model_type": model_type, "train_date": train_date,
            "train_rows": len(X_train), "val_rows": len(X_val),
            "val_rmse": r["rmse"], "val_mae": r["mae"], "val_spearman": r["spearman"],
            "features_used": ",".join(FEATURE_COLS), "notes": notes,
        })

    log_df = pd.DataFrame(log_rows)
    if os.path.exists(EXPERIMENT_LOG):
        log_df.to_csv(EXPERIMENT_LOG, mode="a", header=False, index=False)
    else:
        log_df.to_csv(EXPERIMENT_LOG, index=False)

    best_model_type = max(results, key=lambda k: results[k]["spearman"])

    # write per-position eval CSV (actual vs predicted, best model) for diagnostics
    best_pred = {"ridge": pred_ridge, "gbm": pred_gbm, "lgbm": pred_lgbm}[best_model_type]
    eval_df = sub[val_mask][["player_display_name", "season", TARGET]].copy()
    eval_df["predicted"] = best_pred
    eval_df["residual"] = eval_df[TARGET] - eval_df["predicted"]
    eval_df["best_model"] = best_model_type
    eval_df.to_csv(os.path.join(OUT_DIR, f"eval_{position.lower()}.csv"), index=False)

    return {"position": position, "results": results, "best": best_model_type,
            "n_train": len(X_train), "n_val": len(X_val), "importances": imp_df}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--position", choices=POSITIONS, default=None)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    df = load_panel()
    positions = [args.position] if args.position else POSITIONS

    summary = []
    for pos in positions:
        res = train_one(df, pos, args.notes)
        if res:
            summary.append(res)

    print("\n" + "=" * 60)
    print("SUMMARY - best model per position (by val Spearman)")
    print("=" * 60)
    for s in summary:
        best = s["results"][s["best"]]
        print(f"  {s['position']:3s}  best={s['best']:6s}  Spearman={best['spearman']:.4f}  "
              f"(train={s['n_train']}, val={s['n_val']})")


if __name__ == "__main__":
    main()
