#!/usr/bin/env python3
"""
patchSpFeatures.py
==================
Run AFTER fetchDayOfLineups.py (noon ET) to patch today's SP matchup
features into batter_features.csv for same-day predictions.

Only modifies rows where game_date == today. Historical rows are untouched.
Writes back to the same CSV in place.

This keeps the 5am feature rebuild (buildSportsFeatures.py) fast and clean,
while giving the model real SP signal for same-day use.
"""

import os
import datetime
import numpy as np
import pandas as pd

DATA_DIR  = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/playerData")
FEAT_DIR  = os.path.expanduser("~/discordBot/outputs/features/sports")
FEAT_PATH = os.path.join(FEAT_DIR, "batter_features.csv")
LINEUP_PATH   = os.path.join(DATA_DIR, "day_of_lineups.csv")
PITCHER_PATH  = os.path.join(DATA_DIR, "probable_pitchers.csv")
PQ_PATH       = os.path.join(DATA_DIR, "pitcher_quality.csv")
HAND_PATH     = os.path.join(DATA_DIR, "player_handedness.csv")

TODAY = datetime.date.today().strftime("%Y-%m-%d")


def load_pitcher_quality():
    if not os.path.exists(PQ_PATH):
        return {}
    pq = pd.read_csv(PQ_PATH)
    lookup = {}
    for _, row in pq.iterrows():
        mid = row.get("mlbam_id")
        if pd.notna(mid):
            lookup[int(mid)] = {
                "sp_era":     float(row["era"])           if pd.notna(row.get("era"))           else np.nan,
                "sp_xera":    float(row["xera"])          if pd.notna(row.get("xera"))          else np.nan,
                "sp_k9":      float(row["k_per9"])        if pd.notna(row.get("k_per9"))        else np.nan,
                "sp_whip":    float(row["whip"])          if pd.notna(row.get("whip"))          else np.nan,
                "sp_quality": float(row["quality_score"]) if pd.notna(row.get("quality_score")) else np.nan,
            }
    return lookup


def load_handedness():
    if not os.path.exists(HAND_PATH):
        return {}
    df = pd.read_csv(HAND_PATH)
    return {
        int(row["mlbam_id"]): {
            "bat_side":   str(row.get("bat_side",  "")).strip(),
            "pitch_hand": str(row.get("pitch_hand","")).strip(),
        }
        for _, row in df.iterrows() if pd.notna(row.get("mlbam_id"))
    }


def main():
    print(f"[patchSpFeatures] date={TODAY}")

    if not os.path.exists(LINEUP_PATH):
        print("  day_of_lineups.csv not found — run fetchDayOfLineups.py first")
        return

    lineups = pd.read_csv(LINEUP_PATH)
    if lineups.empty or "batting_order" not in lineups.columns:
        print("  no confirmed lineups yet — nothing to patch")
        return

    pq     = load_pitcher_quality()
    hand   = load_handedness()
    feats  = pd.read_csv(FEAT_PATH, parse_dates=["game_date"])

    today_mask = feats["game_date"].dt.strftime("%Y-%m-%d") == TODAY
    n_today = today_mask.sum()
    print(f"  batter_features rows for today: {n_today}")
    if n_today == 0:
        print("  no today rows in features — run buildSportsFeatures.py first (5am cron)")
        return

    # Build SP lookup: player_name -> SP id for today
    # lineups has: mlbam_id (batter), player_name, batting_order, probable_sp_id, probable_sp_name, team, is_home
    # batter_features has: player_name, playerid (Fangraphs), game_date
    # Join on player_name (normalized)
    lineups["name_key"] = lineups["player_name"].str.strip().str.lower()
    lineup_lookup = {}  # name_key -> {batting_order, sp_id, is_home}
    for _, row in lineups.iterrows():
        if pd.notna(row.get("batting_order")):
            lineup_lookup[row["name_key"]] = {
                "batting_order_confirmed": int(row["batting_order"]),
                "sp_mlbam_id":            int(row["probable_sp_id"]) if pd.notna(row.get("probable_sp_id")) else None,
                "is_home_confirmed":      int(row["is_home"]),
            }

    print(f"  confirmed lineup entries: {len(lineup_lookup)}")

    # Ensure SP columns exist
    sp_cols = ["sp_era", "sp_xera", "sp_k9", "sp_whip", "sp_quality",
               "platoon_adv", "batting_order_confirmed"]
    for col in sp_cols:
        if col not in feats.columns:
            feats[col] = np.nan

    patched = 0
    for idx, row in feats[today_mask].iterrows():
        name_key = str(row.get("player_name", "")).strip().lower()
        lu = lineup_lookup.get(name_key)
        if not lu:
            continue

        # Confirmed batting order
        feats.at[idx, "batting_order_confirmed"] = lu["batting_order_confirmed"]

        # SP quality features
        sp_id = lu["sp_mlbam_id"]
        if sp_id and sp_id in pq:
            for col, val in pq[sp_id].items():
                feats.at[idx, col] = val

            # Platoon advantage: need batter handedness
            # batter_features uses Fangraphs playerid — cross-ref via name
            sp_hand_info = hand.get(sp_id, {})
            sp_hand      = sp_hand_info.get("pitch_hand", "")
            # Look up batter's mlbam_id from lineups table
            batter_mlbam = lineups.loc[
                lineups["name_key"] == name_key, "mlbam_id"
            ].values
            if len(batter_mlbam) > 0:
                bat_info = hand.get(int(batter_mlbam[0]), {})
                bat_side = bat_info.get("bat_side", "")
                if bat_side in ("L","R") and sp_hand in ("L","R"):
                    feats.at[idx, "platoon_adv"] = 1 if bat_side != sp_hand else 0

        patched += 1

    feats.to_csv(FEAT_PATH, index=False)
    print(f"  patched {patched} batter rows with SP matchup + platoon features")
    print(f"  SP coverage for today: {patched}/{n_today} ({patched/n_today*100:.0f}%)")
    print("[patchSpFeatures] done.")


if __name__ == "__main__":
    main()
