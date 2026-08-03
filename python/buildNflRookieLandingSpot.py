#!/usr/bin/env python3
"""
buildNflRookieLandingSpot.py

Computes a "landing spot opportunity" feature for rookies: how much target
share / snap share is VACATED on the team they're drafted to, based on that
team's departing veterans (players who were on the roster the prior season
but are gone this season - retired, cut, traded away, or simply aged out of
the role). A rookie WR walking into a offense that just lost its WR1 to free
agency has a much clearer path to touches than one buried on a stacked depth
chart, independent of the rookie's own talent level - this is exactly the
kind of context draft capital alone doesn't capture.

Method: for team T in season S, sum target_share/snap_share_avg of every
player who logged >= 10% target share OR >= 20% snap share on that team in
season S-1, then flag how much of that departed player's share is now
"vacated" (their team in season S is different from season S-1, or they
have no season-S row in the league at all = retired/out of the league).

Usage: venv/bin/python3 python/buildNflRookieLandingSpot.py
Output: outputs/sports/nfl/fantasy/rookie_landing_spot.csv (team, season ->
        vacated_target_share, vacated_snap_share)
"""
import os

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")


def main():
    phase1_raw = pd.read_csv(os.path.join(OUT_DIR, "nfl_phase1_features.csv"))

    # collapse mid-season-trade duplicates (same convention as training panel)
    phase1 = (
        phase1_raw.assign(_snaps=phase1_raw["games_with_snaps"].fillna(0))
        .sort_values("_snaps", ascending=False)
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .drop(columns=["_snaps"])
    )

    seasons = sorted(phase1["season"].unique())
    rows = []

    for season in seasons:
        if season - 1 not in seasons:
            continue
        prev = phase1[phase1["season"] == season - 1]
        curr = phase1[phase1["season"] == season]
        curr_players_by_team = curr.groupby("team")["player_id"].apply(set).to_dict()

        for team in prev["team"].unique():
            prev_team_players = prev[prev["team"] == team]
            vacated_target = 0.0
            vacated_snap = 0.0
            for _, p in prev_team_players.iterrows():
                ts = p["target_share"] if pd.notna(p["target_share"]) else 0.0
                ss = p["snap_share_avg"] if pd.notna(p["snap_share_avg"]) else 0.0
                if ts < 0.10 and ss < 0.20:
                    continue  # not a meaningful role, don't count as "vacated"
                still_here = p["player_id"] in curr_players_by_team.get(team, set())
                if not still_here:
                    vacated_target += ts
                    vacated_snap += ss
            rows.append({
                "team": team, "season": season,
                "vacated_target_share": vacated_target,
                "vacated_snap_share": vacated_snap,
            })

    out = pd.DataFrame(rows)
    out_path = os.path.join(OUT_DIR, "rookie_landing_spot.csv")
    out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    print(f"Rows: {len(out)}")
    print("\n=== Sample: 2025 biggest vacated target share (best landing spots) ===")
    print(out[out["season"] == 2025].sort_values("vacated_target_share", ascending=False)
          .head(8).to_string(index=False))


if __name__ == "__main__":
    main()
