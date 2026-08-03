#!/usr/bin/env python3
"""
scoreNfl2026Rookies.py

Scores the 2026 rookie class (players with real ADP but no 2025 NFL usage -
the 80-player gap in the main Phase 3 pipeline) using the rookie prior model
(draft capital + combine + landing spot).

Usage: venv/bin/python3 python/scoreNfl2026Rookies.py
Output: outputs/sports/nfl/fantasy/projections_2026_rookies.csv
"""
import glob
import os
import re
import unicodedata

import joblib
import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
MODEL_DIR = os.path.expanduser("~/discordBot/models/sports/nfl")
TEAM_MAP = {"LAR": "LA"}
SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_rookie_model(position):
    pattern = os.path.join(MODEL_DIR, f"nfl_rookie_{position.lower()}_*.pkl")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    return joblib.load(files[-1])


def main():
    adp = pd.read_csv(os.path.join(OUT_DIR, "adp_2026.csv"))
    proj = pd.read_csv(os.path.join(OUT_DIR, "projections_2026.csv"))

    # rookies = players with ADP but no projection from the main model
    scored_names = set(proj.dropna(subset=["projected_room40_pts"])["full_name"])
    rookies = adp[~adp["full_name"].isin(scored_names)].copy()
    print(f"Unscored players (candidates for rookie model): {len(rookies)}")

    rookies["norm_name"] = rookies["full_name"].map(norm_name)
    rookies["team_nflverse"] = rookies["team"].map(lambda t: TEAM_MAP.get(t, t))

    draft = pd.read_csv(os.path.join(OUT_DIR, "rookie_draft_combine.csv"))
    draft["norm_name"] = draft["pfr_player_name"].map(norm_name)
    draft_2026 = draft[draft["season"] == 2026].drop_duplicates(subset=["norm_name"])

    landing_spot = pd.read_csv(os.path.join(OUT_DIR, "rookie_landing_spot.csv"))
    landing_2026 = landing_spot[landing_spot["season"] == 2026]
    if len(landing_2026) == 0:
        # 2026 landing spot needs 2025 phase1 data as "prior season" - already
        # computed in buildNflRookieLandingSpot.py if season 2026 existed in
        # phase1. If not present (season hasn't started, no post-2025 roster
        # snapshot yet), fall back to 2025's vacated shares as the best guess.
        print("No 2026 landing-spot data yet (season hasn't started) - "
              "using 2025 vacated share as fallback proxy")
        landing_2026 = landing_spot[landing_spot["season"] == 2025].copy()
        landing_2026["season"] = 2026

    merged = rookies.merge(draft_2026[["norm_name", "round", "pick", "forty", "bench",
                                         "vertical", "broad_jump", "cone", "shuttle",
                                         "speed_score", "burst_score", "wt"]],
                            on="norm_name", how="left")
    merged = merged.merge(landing_2026[["team", "vacated_target_share", "vacated_snap_share"]],
                           left_on="team_nflverse", right_on="team", how="left",
                           suffixes=("", "_land"))

    has_draft_data = merged["pick"].notna()
    print(f"Matched to 2026 draft class: {has_draft_data.sum()} / {len(merged)}")

    FEATURE_COLS = [
        "pick", "round", "forty", "bench", "vertical", "broad_jump", "cone",
        "shuttle", "speed_score", "burst_score", "wt",
        "vacated_target_share", "vacated_snap_share",
    ]
    predictions = pd.Series(np.nan, index=merged.index)

    for position in ["QB", "RB", "WR", "TE"]:
        bundle = load_rookie_model(position)
        if bundle is None:
            continue
        pos_mask = (merged["position"] == position) & has_draft_data
        if pos_mask.sum() == 0:
            continue
        X = merged.loc[pos_mask, FEATURE_COLS].copy()
        for c in FEATURE_COLS[2:]:
            med = X[c].median()
            X[c] = X[c].fillna(med if pd.notna(med) else 0.0)
        preds = bundle["model"].predict(X)
        predictions.loc[X.index] = preds
        print(f"{position}: {pos_mask.sum()} rookies scored "
              f"(val_spearman={bundle['metrics']['val_spearman']:.3f})")

    merged["projected_room40_pts_rookie"] = predictions
    out_cols = ["full_name", "position", "team", "adp_overall", "adp_position",
                "round", "pick", "vacated_target_share", "vacated_snap_share",
                "projected_room40_pts_rookie"]
    out = merged[out_cols].sort_values("adp_overall")
    out_path = os.path.join(OUT_DIR, "projections_2026_rookies.csv")
    out.to_csv(out_path, index=False)

    print(f"\nWrote: {out_path}")
    print(f"Rows: {len(out)}, scored: {out['projected_room40_pts_rookie'].notna().sum()}")
    print("\n=== Top 10 rookies by projected points ===")
    print(out.sort_values("projected_room40_pts_rookie", ascending=False).head(10)
          [["full_name", "position", "team", "adp_overall", "round", "pick",
            "projected_room40_pts_rookie"]].to_string(index=False))


if __name__ == "__main__":
    main()
