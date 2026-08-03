#!/usr/bin/env python3
"""
buildNflRookieTrainingPanel.py

Rookie prior training panel: predicts a player's ROOKIE-YEAR room40_pts using
only pre-NFL-usage information - draft capital, combine athletic testing, and
their team's landing-spot opportunity (vacated target/snap share). This is a
SEPARATE model from the main Phase 2 projection model, because rookies have
no lag1 NFL usage to feed the main model's features.

Usage: venv/bin/python3 python/buildNflRookieTrainingPanel.py
Output: outputs/sports/nfl/fantasy/nfl_rookie_training_panel.csv
"""
import os

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")


def main():
    career = pd.read_csv(os.path.join(OUT_DIR, "career_panel.csv"))
    rookie_combine = pd.read_csv(os.path.join(OUT_DIR, "rookie_draft_combine.csv"))
    landing_spot = pd.read_csv(os.path.join(OUT_DIR, "rookie_landing_spot.csv"))
    phase1_raw = pd.read_csv(os.path.join(OUT_DIR, "nfl_phase1_features.csv"))

    # collapse mid-season-trade duplicates
    phase1 = (
        phase1_raw.assign(_snaps=phase1_raw["games_with_snaps"].fillna(0))
        .sort_values("_snaps", ascending=False)
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .drop(columns=["_snaps"])
    )

    # rookie-year outcome: filter career panel to season == rookie_year
    rookie_outcomes = career[career["season"] == career["rookie_year"]][
        ["player_id", "player_display_name", "position", "season", "games",
         "room40_pts", "pts_per_game"]
    ].copy()

    # team each rookie landed on (their rookie-year team, from phase1 - only
    # covers 2018+ due to phase1's season range; earlier draft classes will
    # have no landing-spot feature, expected and handled via NaN)
    rookie_team = phase1[["player_id", "season", "team"]].drop_duplicates(
        subset=["player_id", "season"]
    )

    panel = rookie_outcomes.merge(rookie_team, on=["player_id", "season"], how="left")
    panel = panel.merge(
        rookie_combine.rename(columns={"gsis_id": "player_id"})[
            ["player_id", "round", "pick", "forty", "bench", "vertical",
             "broad_jump", "cone", "shuttle", "speed_score", "burst_score", "wt"]
        ],
        on="player_id", how="inner"  # inner: only players who were actually drafted
    )
    panel = panel.merge(landing_spot, on=["team", "season"], how="left")

    print(f"Rookie training panel rows: {len(panel)}")
    print(f"Season range: {panel['season'].min()} - {panel['season'].max()}")
    print("Position breakdown:")
    print(panel["position"].value_counts())
    print(f"\nMissing landing spot (rookie team pre-2019 or unmatched): "
          f"{panel['vacated_target_share'].isna().sum()}")
    print(f"Missing combine data (undrafted-combine attendee): {panel['forty'].isna().sum()}")

    out_path = os.path.join(OUT_DIR, "nfl_rookie_training_panel.csv")
    panel.to_csv(out_path, index=False)
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
