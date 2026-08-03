#!/usr/bin/env python3
"""
buildNflFlexSplit.py

Derives Room 40's FLEX allocation split (RB/WR/TE) EMPIRICALLY from real
historical fantasy scoring, instead of assuming a fixed hand-picked split.

Why this matters: VORP's replacement-level baseline for RB/WR/TE depends on
how many of the league's 12 shared FLEX slots (1 per team, RB/WR/TE eligible)
get "used up" by each position. A wrong split shifts every position's
replacement level and therefore every VORP score - see nfl-room40-draft-prep
skill notes for the mechanics.

Method (per season, then averaged):
  1. Lock in the guaranteed starters per Room 40's actual roster rules:
     top 24 RB, top 24 WR, top 12 TE (2/2/1 x 12 teams) by REAL room40_pts
     scored that season (not projections - this is retrospective/empirical).
  2. Pool everyone left over (RB25+, WR25+, TE13+) into one list, ranked
     purely by points scored, position-blind.
  3. Take the top 12 off that pooled list (1 FLEX slot x 12 teams) - however
     many are RB/WR/TE in that top 12 IS the empirical flex split for that
     season.
  4. Average the ratios across seasons 2019-2025 (matches the training
     panel's season window - see buildNflTrainingPanel.R) for stability
     rather than relying on any single year's idiosyncratic depth.

Usage: venv/bin/python3 python/buildNflFlexSplit.py
Output: outputs/sports/nfl/fantasy/flex_split_empirical.csv
"""
import os

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")

N_TEAMS = 12
STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 1
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
SEASON_MIN, SEASON_MAX = 2019, 2025  # matches training panel window


def season_flex_split(season_df):
    """Given one season's player-season rows (RB/WR/TE only), return the
    empirical flex allocation counts for that season."""
    leftover_frames = []
    for pos in FLEX_ELIGIBLE:
        pos_df = season_df[season_df["position"] == pos].sort_values(
            "room40_pts", ascending=False
        )
        cutoff = STARTERS[pos] * N_TEAMS
        leftover_frames.append(pos_df.iloc[cutoff:])  # everyone past guaranteed starters

    pool = pd.concat(leftover_frames).sort_values("room40_pts", ascending=False)
    flex_winners = pool.head(FLEX_SLOTS * N_TEAMS)
    counts = flex_winners["position"].value_counts().reindex(list(FLEX_ELIGIBLE), fill_value=0)
    return counts


def main():
    career = pd.read_csv(os.path.join(OUT_DIR, "career_panel.csv"))
    career = career[
        (career["season"] >= SEASON_MIN)
        & (career["season"] <= SEASON_MAX)
        & (career["games"] >= 1)
        & (career["position"].isin(FLEX_ELIGIBLE))
    ].copy()

    per_season = {}
    for season, season_df in career.groupby("season"):
        counts = season_flex_split(season_df)
        per_season[season] = counts

    per_season_df = pd.DataFrame(per_season).T
    per_season_df.index.name = "season"
    per_season_pct = per_season_df.div(per_season_df.sum(axis=1), axis=0)

    print("=== Empirical FLEX allocation by season (12 flex slots, RB/WR/TE eligible) ===")
    print(per_season_df.astype(int).to_string())
    print("\n=== As percentage of the 12 flex slots ===")
    print((per_season_pct * 100).round(1).to_string())

    avg_counts = per_season_df.mean()
    avg_pct = per_season_pct.mean()
    std_pct = per_season_pct.std()

    print("\n=== Averaged across", SEASON_MIN, "-", SEASON_MAX, "===")
    for pos in FLEX_ELIGIBLE:
        print(f"  {pos}: {avg_pct[pos]*100:.1f}% (avg {avg_counts[pos]:.2f} of 12 slots, "
              f"std {std_pct[pos]*100:.1f} pts)")

    # Sanity check against the prior fixed assumptions used in buildNflVorp.py
    prior_docstring = {"RB": 0.60, "WR": 0.35, "TE": 0.05}
    prior_code_const = {"RB": 0.55, "WR": 0.40, "TE": 0.05}
    print("\n=== Comparison vs prior assumptions in buildNflVorp.py ===")
    print("  (docstring said 60/35/5, but the actual FLEX_SPLIT constant coded "
          "was 55/40/5 - these two never matched each other, let alone the data)")
    for pos in FLEX_ELIGIBLE:
        print(f"  {pos}: empirical {avg_pct[pos]*100:.1f}%  |  docstring said {prior_docstring[pos]*100:.0f}%  "
              f"|  code used {prior_code_const[pos]*100:.0f}%")

    out = per_season_pct.reset_index()
    out["source"] = "per_season"
    avg_row = pd.DataFrame([{"season": "AVERAGE_2019_2025", **avg_pct.to_dict(), "source": "average"}])
    out = pd.concat([out, avg_row], ignore_index=True)
    out_path = os.path.join(OUT_DIR, "flex_split_empirical.csv")
    out.to_csv(out_path, index=False)
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
