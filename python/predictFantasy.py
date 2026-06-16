#!/usr/bin/env python3
"""
predictFantasy.py
=================
Loads the most recent trained model and scores all current players,
returning ranked predictions.

Outputs:
  outputs/features/sports/batter_predictions_{horizon}.csv
  outputs/features/sports/pitcher_predictions_{horizon}.csv

Each row: player_name, fantasy_position, team, predicted_pts,
          confidence_band_lo, confidence_band_hi, model_id

Usage:
  python predictFantasy.py                         # score all, both horizons
  python predictFantasy.py --type batters          # batters only
  python predictFantasy.py --horizon daily         # daily only
  python predictFantasy.py --model_type gbm        # use GBM instead of ridge
  python predictFantasy.py --position OF           # filter output to one position
  python predictFantasy.py --top 20                # return top N
  python predictFantasy.py --fa_only               # free agents only (reads freeagents.csv)
"""

import os, sys, argparse, pickle, glob
import pandas as pd
import numpy as np

BASE       = os.path.expanduser("~/discordBot")
FEAT_DIR   = os.path.join(BASE, "outputs/features/sports")
MODEL_DIR  = os.path.join(BASE, "models/sports")
FA_CSV     = os.path.join(BASE, "outputs/sports/mlb/fantasy/freeagents.csv")
OUT_DIR    = FEAT_DIR  # predictions go alongside features

def load_latest_model(player_type, horizon, model_type="ridge"):
    """Find the most recently saved model for this combo."""
    pattern = os.path.join(MODEL_DIR, player_type,
                           f"{player_type}_{horizon}_{model_type}_*.pkl")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No model found for {player_type}/{horizon}/{model_type}. "
            f"Run trainFantasyModel.py first."
        )
    path = files[-1]  # most recent by date suffix
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    print(f"  loaded model: {os.path.basename(path)}")
    print(f"  trained:      {bundle.get('train_date', '?')}")
    print(f"  val Spearman: {bundle['metrics']['val_spearman']}")
    print(f"  val RMSE:     {bundle['metrics']['val_rmse']}")
    return bundle

def get_current_player_features(df, feature_cols):
    """
    For each player, take their most recent row (latest game_date).
    This represents their current form going into the next prediction.
    """
    df["game_date"] = pd.to_datetime(df["game_date"])
    current = (
        df.sort_values("game_date")
          .groupby("playerid")
          .last()
          .reset_index()
    )
    return current

def predict(player_type="batters", horizon="daily", model_type="ridge",
            position=None, top=None, fa_only=False):

    print(f"\n{'='*55}")
    print(f"Predicting: {player_type.upper()} | {horizon} | {model_type}")
    print(f"{'='*55}")

    # Load model
    try:
        bundle = load_latest_model(player_type, horizon, model_type)
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        return None

    model       = bundle["model"]
    feature_cols = bundle["feature_cols"]
    model_id    = bundle["model_id"]

    # Load features
    feat_file = "batter" if player_type == "batters" else "pitcher"
    feat_path = os.path.join(FEAT_DIR, f"{feat_file}_features.csv")
    if not os.path.exists(feat_path):
        print(f"  ERROR: {feat_path} not found. Run buildSportsFeatures.py first.")
        return None

    df = pd.read_csv(feat_path, parse_dates=["game_date"])

    # Get most recent row per player (current form)
    current = get_current_player_features(df, feature_cols)

    # Filter to rows with enough feature data
    available_features = [f for f in feature_cols if f in current.columns]
    current = current.dropna(subset=available_features)

    if len(current) == 0:
        print("  ERROR: no players with complete feature data")
        return None

    # Filter by position if requested
    if position:
        current = current[current["fantasy_position"] == position.upper()]
        if len(current) == 0:
            print(f"  No players found at position {position}")
            return None

    # FA filter
    if fa_only and os.path.exists(FA_CSV):
        fa_df = pd.read_csv(FA_CSV)
        fa_names = set(fa_df["player_name"].str.strip().str.lower())
        current = current[current["player_name"].str.strip().str.lower().isin(fa_names)]
        print(f"  FA filter: {len(current)} free agents remaining")

    # Predict
    X = current[available_features].values
    preds = model.predict(X)
    current = current.copy()
    current["predicted_pts"] = preds.round(2)

    # Confidence band: use val MAE as a simple ± band
    mae = bundle["metrics"]["val_mae"]
    current["pred_lo"] = (preds - mae).round(2)
    current["pred_hi"] = (preds + mae).round(2)
    current["model_id"] = model_id

    # Sort by predicted pts descending
    current = current.sort_values("predicted_pts", ascending=False)

    # Top N
    if top:
        current = current.head(top)

    # Select output columns
    out_cols = ["player_name", "fantasy_position", "team",
                "predicted_pts", "pred_lo", "pred_hi",
                "fantasy_pts_r7", "season_ppg",
                "game_date", "model_id"]
    if player_type == "batters":
        out_cols += ["wOBA_r7", "EV_r7", "Barrel_pct_r7"]
    else:
        out_cols += ["IP_r3", "K_r3", "ERA_r5"]
    out_cols = [c for c in out_cols if c in current.columns]

    result = current[out_cols].reset_index(drop=True)

    # Save
    out_path = os.path.join(OUT_DIR, f"{feat_file}_predictions_{horizon}.csv")
    result.to_csv(out_path, index=False)
    print(f"\n  Top predictions ({horizon}):")
    print(result.head(15).to_string(index=False))
    print(f"\n  saved -> {out_path}")

    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type",       choices=["batters", "pitchers", "all"], default="all")
    parser.add_argument("--horizon",    choices=["daily", "weekly", "all"],     default="all")
    parser.add_argument("--model_type", choices=["ridge", "gbm"],               default="ridge")
    parser.add_argument("--position",   default=None)
    parser.add_argument("--top",        type=int, default=25)
    parser.add_argument("--fa_only",    action="store_true")
    args = parser.parse_args()

    player_types = ["batters", "pitchers"] if args.type == "all" else [args.type]
    horizons     = ["daily", "weekly"]     if args.horizon == "all" else [args.horizon]

    for pt in player_types:
        for hz in horizons:
            predict(
                player_type=pt,
                horizon=hz,
                model_type=args.model_type,
                position=args.position,
                top=args.top,
                fa_only=args.fa_only,
            )
