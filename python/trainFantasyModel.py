#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")
warnings.filterwarnings("ignore", category=RuntimeWarning, message="Degrees of freedom")
warnings.filterwarnings("ignore", category=UserWarning, message="X does not have valid feature names")
"""
trainFantasyModel.py
====================
Trains fantasy point prediction models for batters and pitchers.
Supports two horizons:
  - daily  : predict next_game_pts
  - weekly : predict next7_pts

Two model types trained per run, results compared:
  - ridge          : Ridge regression (fast, interpretable baseline)
  - gbm            : GradientBoostingRegressor (main model)

Every run is logged to models/meta/experiment_log.csv with:
  model_id, domain, player_type, horizon, model_type, train_date,
  train_rows, val_rows, val_rmse, val_mae, val_spearman,
  val_r2, features_used, notes

Trained models saved as:
  models/sports/batters/{model_id}.pkl
  models/sports/pitchers/{model_id}.pkl

Usage:
  python trainFantasyModel.py                    # train all
  python trainFantasyModel.py --type batters     # batters only
  python trainFantasyModel.py --type pitchers    # pitchers only
  python trainFantasyModel.py --horizon daily    # daily only
  python trainFantasyModel.py --notes "added EV" # annotate the run
"""

import os, sys, argparse, json, pickle, uuid
from datetime import datetime, date

import pandas as pd
import numpy as np
from scipy import stats
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import lightgbm as lgb

# ── paths ─────────────────────────────────────────────────────────────────────
BASE        = os.path.expanduser("~/discordBot")
FEAT_DIR    = os.path.join(BASE, "outputs/features/sports")
MODEL_DIR   = os.path.join(BASE, "models/sports")
META_DIR    = os.path.join(BASE, "models/meta")
LOG_PATH    = os.path.join(META_DIR, "experiment_log.csv")
os.makedirs(os.path.join(MODEL_DIR, "batters"),  exist_ok=True)
os.makedirs(os.path.join(MODEL_DIR, "pitchers"), exist_ok=True)
os.makedirs(META_DIR, exist_ok=True)

# ── feature definitions ───────────────────────────────────────────────────────
BATTER_FEATURES = [
    # rolling pts
    "fantasy_pts_r7", "fantasy_pts_r14", "fantasy_pts_r30",
    "fantasy_pts_std_r7", "fantasy_pts_std_r14", "sharpe_r14",
    # statcast rolling
    "wOBA_r7", "wOBA_r14", "xwOBA_r7", "xwOBA_r14",
    "ISO_r7", "ISO_r14", "Hard_pct_r7", "Hard_pct_r14",
    "Barrel_pct_r7", "Barrel_pct_r14", "EV_r7", "EV_r14",
    "SwStr_pct_r7", "SwStr_pct_r14", "BB_pct_r7", "K_pct_r7",
    "BABIP_r7",
    # context
    "rest_days", "opp_diff_r14", "season_ppg",
    "hot_flag", "cold_flag",
    "BatOrder_r7", "bat_order_trend",
    # park & matchup
    "park_factor_dev",
    # home/away, schedule, streaks
    "is_home",
    "month", "is_midsummer",
    "games_since_hr", "games_since_sb",
    "multi_hit_r14",
    # SP matchup (NaN for historical rows; imputed at train time)
    "sp_era", "sp_xera", "sp_k9", "sp_whip", "sp_quality",
    # platoon advantage (NaN for historical; populated day-of)
    "platoon_adv",
    # pitch-level features (NaN for pre-statcast rows; populated after backfill)
    "bat_speed_r14", "swing_length_r14", "attack_angle_r14", "sweet_spot_pct_r14",
    "fastball_pct_r14", "breaking_pct_r14", "offspeed_pct_r14",
    "chase_rate_r14", "zone_contact_r14", "whiff_rate_r14", "first_pitch_strike_r14",
    "xba_r14", "xwoba_pitch_r14", "hard_contact_r14", "barrel_rate_r14",
    "times_thru_order_r14", "count_leverage_r14",
    # position dummies
    "pos_1B", "pos_2B", "pos_3B", "pos_C", "pos_OF", "pos_SS",
]

PITCHER_FEATURES = [
    # rolling pts
    "fantasy_pts_r3", "fantasy_pts_r5", "fantasy_pts_r10",
    "fantasy_pts_std_r5", "fantasy_pts_std_r10", "sharpe_r5",
    # rolling box score
    "IP_r3", "IP_r5", "K_r3", "K_r5",
    "ER_r3", "ER_r5", "BB_r3", "BB_r5",
    "ERA_r5", "WHIP_r5",
    # context
    "rest_days", "opp_diff_r14", "season_ppg",
    # park
    "park_factor_dev",
    # opponent quality (rolling wOBA of batters the pitcher faced)
    "opp_woba_r14",
]

HORIZONS = {
    "daily":  "next_game_pts",
    "weekly": "next7_pts",
}

# ── logging ───────────────────────────────────────────────────────────────────
LOG_COLS = [
    "model_id", "domain", "player_type", "horizon", "model_type",
    "train_date", "train_rows", "val_rows",
    "val_rmse", "val_mae", "val_spearman", "val_r2",
    "features_used", "notes",
]

def load_log():
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH)
    return pd.DataFrame(columns=LOG_COLS)

def append_log(row: dict):
    log = load_log()
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)
    print(f"  logged -> {LOG_PATH}")

# ── recency weights ───────────────────────────────────────────────────────────
RECENCY_WEIGHTS = {
    2026: 1.0,
    2025: 0.7,
    2024: 0.4,
    2023: 0.15,
    2022: 0.1,
    2021: 0.1,
}

def compute_sample_weights(train_df):
    """Return a weight array based on season recency."""
    years = pd.to_datetime(train_df["game_date"]).dt.year
    return years.map(lambda y: RECENCY_WEIGHTS.get(int(y), 0.1)).values

# ── train one model ───────────────────────────────────────────────────────────
def train_one(df, feature_cols, target_col, model_type,
              use_recency_weights=False, batter_seasons=None,
              fixed_val_window=None):
    """
    Time-based train/val split.

    fixed_val_window: tuple (start_month_day, end_month_day) as (MM-DD, MM-DD)
                      e.g. ("05-01", "05-15") — always val on May 1-15 of the
                      latest season in the data.  Falls back to rolling 14-day
                      holdout when the window has < 10 rows.
    batter_seasons:   list of int years to KEEP for batters.  None = keep all.
    use_recency_weights: bool — weight training rows by season recency.
    Returns: (fitted_pipeline, metrics_dict, train_df, val_df)
    """
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])

    # Filter to specified seasons (batters only — pitchers use all seasons)
    if batter_seasons is not None:
        df = df[df["game_date"].dt.year.isin(batter_seasons)]

    # Drop rows where target is NaN (end of season - no future games)
    df = df.dropna(subset=[target_col])

    available_features = [f for f in feature_cols if f in df.columns]

    # Sparse features — impute with median (or 0 if fully NaN)
    SPARSE_FEATURES = [
        "sp_era", "sp_xera", "sp_k9", "sp_whip", "sp_quality", "platoon_adv",
        "opp_woba_r14",
        "bat_speed_r14", "swing_length_r14", "attack_angle_r14", "sweet_spot_pct_r14",
        "fastball_pct_r14", "breaking_pct_r14", "offspeed_pct_r14",
        "chase_rate_r14", "zone_contact_r14", "whiff_rate_r14", "first_pitch_strike_r14",
        "xba_r14", "xwoba_pitch_r14", "hard_contact_r14", "barrel_rate_r14",
        "times_thru_order_r14", "count_leverage_r14",
    ]
    for col in SPARSE_FEATURES:
        if col in df.columns:
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val if pd.notna(median_val) else 0.0)

    core_features = [f for f in available_features if f not in SPARSE_FEATURES]
    df = df.dropna(subset=core_features + [target_col])

    if len(df) < 50:
        raise ValueError(f"Not enough rows after dropping NaN: {len(df)}")

    # ── val split ──────────────────────────────────────────────────────────────
    val_df = pd.DataFrame()
    if fixed_val_window is not None:
        # Fixed calendar window in the latest year present
        latest_year = df["game_date"].dt.year.max()
        start = pd.Timestamp(f"{latest_year}-{fixed_val_window[0]}")
        end   = pd.Timestamp(f"{latest_year}-{fixed_val_window[1]}")
        val_df = df[(df["game_date"] >= start) & (df["game_date"] <= end)]

    if len(val_df) < 10:
        # Fallback: rolling 14-day holdout
        cutoff = df["game_date"].max() - pd.Timedelta(days=14)
        val_df   = df[df["game_date"] >  cutoff]
        train_df = df[df["game_date"] <= cutoff]
        if len(val_df) < 10:
            cutoff   = df["game_date"].max() - pd.Timedelta(days=21)
            val_df   = df[df["game_date"] >  cutoff]
            train_df = df[df["game_date"] <= cutoff]
    else:
        # Exclude fixed val window from training
        start = pd.Timestamp(f"{df['game_date'].dt.year.max()}-{fixed_val_window[0]}")
        end   = pd.Timestamp(f"{df['game_date'].dt.year.max()}-{fixed_val_window[1]}")
        train_df = df[~((df["game_date"] >= start) & (df["game_date"] <= end))]

    X_train = train_df[available_features].values
    y_train = train_df[target_col].values
    X_val   = val_df[available_features].values
    y_val   = val_df[target_col].values

    # Recency sample weights
    sample_weight = None
    if use_recency_weights:
        sample_weight = compute_sample_weights(train_df)

    # ── build & fit model ──────────────────────────────────────────────────────
    if model_type == "ridge":
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("model",  Ridge(alpha=1.0)),
        ])
        model.fit(X_train, y_train,
                  **{"model__sample_weight": sample_weight} if sample_weight is not None else {})

    elif model_type == "gbm":
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("model",  GradientBoostingRegressor(
                n_estimators=200,
                learning_rate=0.05,
                max_depth=4,
                min_samples_leaf=10,
                subsample=0.8,
                random_state=42,
            )),
        ])
        fit_params = {}
        if sample_weight is not None:
            fit_params["model__sample_weight"] = sample_weight
        model.fit(X_train, y_train, **fit_params)

    elif model_type == "lgbm":
        # LightGBM with early stopping — fit scaler separately so we can pass eval_set
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_val_s   = scaler.transform(X_val)
        lgbm_model = lgb.LGBMRegressor(
            n_estimators=400,
            learning_rate=0.03,
            max_depth=5,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1,
        )
        lgbm_model.fit(
            X_train_s, y_train,
            eval_set=[(X_val_s, y_val)],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
            sample_weight=sample_weight,
        )
        # Wrap as picklable sklearn-compatible pipeline
        model = Pipeline([
            ("scaler", scaler),
            ("model",  lgbm_model),
        ])

    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    y_pred = model.predict(X_val)

    rmse     = np.sqrt(mean_squared_error(y_val, y_pred))
    mae      = mean_absolute_error(y_val, y_pred)
    r2       = r2_score(y_val, y_pred)
    spearman = stats.spearmanr(y_val, y_pred).statistic

    metrics = {
        "val_rmse":      round(rmse, 4),
        "val_mae":       round(mae,  4),
        "val_r2":        round(r2,   4),
        "val_spearman":  round(spearman, 4),
        "train_rows":    len(train_df),
        "val_rows":      len(val_df),
        "features_used": available_features,
    }

    return model, metrics, train_df, val_df

# ── main training loop ────────────────────────────────────────────────────────
def run_training(player_type="batters", horizon="daily", notes="", model_types=None,
                 use_recency_weights=False, batter_seasons=None, fixed_val_window=None):
    if model_types is None:
        model_types = ["ridge", "gbm", "lgbm"]

    horizon_col  = HORIZONS[horizon]
    feat_path    = os.path.join(FEAT_DIR, f"{'batter' if player_type == 'batters' else 'pitcher'}_features.csv")
    feature_cols = BATTER_FEATURES if player_type == "batters" else PITCHER_FEATURES
    save_dir     = os.path.join(MODEL_DIR, player_type)

    print(f"\n{'='*60}")
    print(f"Training: {player_type.upper()} | horizon={horizon} | target={horizon_col}")
    if batter_seasons:
        print(f"  seasons: {batter_seasons}")
    if use_recency_weights:
        print(f"  recency weights: ON")
    if fixed_val_window:
        print(f"  fixed val window: {fixed_val_window[0]} → {fixed_val_window[1]}")
    print(f"{'='*60}")

    if not os.path.exists(feat_path):
        print(f"  ERROR: feature file not found: {feat_path}")
        return

    df = pd.read_csv(feat_path, parse_dates=["game_date"])
    print(f"  Loaded {len(df)} rows, {df['player_name'].nunique()} players")

    results = {}
    for mt in model_types:
        print(f"\n  --- {mt.upper()} ---")
        try:
            model, metrics, train_df, val_df = train_one(
                df, feature_cols, horizon_col, mt,
                use_recency_weights=use_recency_weights,
                batter_seasons=batter_seasons,
                fixed_val_window=fixed_val_window,
            )
        except ValueError as e:
            print(f"  SKIP: {e}")
            continue

        model_id   = f"{player_type}_{horizon}_{mt}_{date.today().isoformat()}"
        model_path = os.path.join(save_dir, f"{model_id}.pkl")

        bundle = {
            "model":        model,
            "model_id":     model_id,
            "player_type":  player_type,
            "horizon":      horizon,
            "target_col":   horizon_col,
            "feature_cols": metrics["features_used"],
            "train_date":   date.today().isoformat(),
            "metrics":      metrics,
        }
        with open(model_path, "wb") as f:
            pickle.dump(bundle, f)
        print(f"  saved  -> {model_path}")

        print(f"  train rows  : {metrics['train_rows']}")
        print(f"  val rows    : {metrics['val_rows']}")
        print(f"  val RMSE    : {metrics['val_rmse']}")
        print(f"  val MAE     : {metrics['val_mae']}")
        print(f"  val R2      : {metrics['val_r2']}")
        print(f"  val Spearman: {metrics['val_spearman']}  ← ranking accuracy")

        # Feature importance for tree models
        if mt in ("gbm", "lgbm"):
            inner = model.named_steps["model"]
            feat_names = metrics["features_used"]
            importances = inner.feature_importances_
            top = sorted(zip(feat_names, importances), key=lambda x: -x[1])[:10]
            print(f"\n  Top 10 feature importances:")
            for fname, imp in top:
                print(f"    {fname:<30} {imp:.4f}")

        log_row = {
            "model_id":     model_id,
            "domain":       "sports",
            "player_type":  player_type,
            "horizon":      horizon,
            "model_type":   mt,
            "train_date":   date.today().isoformat(),
            "train_rows":   metrics["train_rows"],
            "val_rows":     metrics["val_rows"],
            "val_rmse":     metrics["val_rmse"],
            "val_mae":      metrics["val_mae"],
            "val_spearman": metrics["val_spearman"],
            "val_r2":       metrics["val_r2"],
            "features_used": json.dumps(metrics["features_used"]),
            "notes":        notes,
        }
        append_log(log_row)
        results[mt] = metrics

    # Compare models
    if len(results) >= 2:
        print(f"\n  {'─'*50}")
        print(f"  COMPARISON: {player_type} | {horizon}")
        print(f"  {'Model':<10} {'RMSE':>8} {'MAE':>8} {'Spearman':>10} {'R2':>8}")
        for mt, m in results.items():
            print(f"  {mt:<10} {m['val_rmse']:>8.4f} {m['val_mae']:>8.4f} {m['val_spearman']:>10.4f} {m['val_r2']:>8.4f}")
        winner = min(results.items(), key=lambda x: x[1]["val_spearman"] * -1)
        print(f"  Winner by Spearman: {winner[0].upper()}")

    return results

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type",    choices=["batters", "pitchers", "all"], default="all")
    parser.add_argument("--horizon", choices=["daily", "weekly", "all"],     default="all")
    parser.add_argument("--notes",   default="")
    parser.add_argument("--recency_weights", action="store_true",
                        help="Weight training rows by season recency (2026=1.0, 2025=0.7, ...)")
    parser.add_argument("--batter_seasons", type=int, nargs="+", default=None,
                        help="Seasons to include for batters e.g. --batter_seasons 2024 2025 2026")
    parser.add_argument("--fixed_val", type=str, nargs=2, default=None,
                        metavar=("START_MM-DD", "END_MM-DD"),
                        help="Fixed calendar val window e.g. --fixed_val 05-01 05-15")
    args = parser.parse_args()

    player_types = ["batters", "pitchers"] if args.type == "all" else [args.type]
    horizons     = ["daily", "weekly"]     if args.horizon == "all" else [args.horizon]

    for pt in player_types:
        for hz in horizons:
            run_training(
                player_type=pt,
                horizon=hz,
                notes=args.notes,
                use_recency_weights=args.recency_weights,
                batter_seasons=args.batter_seasons if pt == "batters" else None,
                fixed_val_window=tuple(args.fixed_val) if args.fixed_val else None,
            )

    print("\n[trainFantasyModel] done.")
    print(f"Experiment log: {LOG_PATH}")
