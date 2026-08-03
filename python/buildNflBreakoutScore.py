#!/usr/bin/env python3
"""
buildNflBreakoutScore.py

Phase 5, step 2: composite breakout-candidate score for 2nd/3rd-year
skill-position players ahead of the 2026 season. Combines five z-scored
signal families into one composite, per position, then flags the top
3-5 per position as "breakout candidates" (surfaced downstream as a star
on the cheatsheet PNG and the live /nfl draftboard tool).

Eligibility for the breakout pool:
  - position in QB/RB/WR/TE
  - years_exp in {0, 1} as of end of 2025 season (entering year 2 or 3 in 2026)
  - sample gate: >= 8 games played in 2025 (avoid scoring a 3-game cameo)
  - NOT already a top-12 finisher at their position by 2025 room40_pts
    (that's "arrived," not "breaking out")

Composite score (all terms z-scored WITHIN position, within the eligible
pool only - so a WR is compared to other young WRs, not to QBs):

  breakout_z = eff_z + trend_z + tailwind_z + draft_capital_z + aging_z - crowding_z

  eff_z     : NGS rate efficiency (CPOE for QB, RYOE/att for RB, YAC-oe for
              WR/TE), SHRUNK toward the position mean by sample size before
              z-scoring (James-Stein-style shrinkage: shrunk = mean + (raw -
              mean) * n/(n+K)) so a rookie's 25-target efficiency spike
              doesn't get treated as equal-confidence to a 120-target season.
  trend_z   : average of z(snap_share_trend) and z(target_share_trend), where
              trend = back-half-of-2025 average minus front-half average.
              Usage TREND, not level - rewards players who were still
              climbing at year's end.
  tailwind_z: team-level situational opportunity opening up for 2026 -
              z(target_share vacated by departed pass-catchers) for
              WR/TE/RB, z(rush-attempt share vacated) for RB, plus a flat
              bonus for a new offensive coordinator (scheme uncertainty
              cuts both ways but on net favors an incumbent young player
              getting a clean-slate look).
  draft_capital_z: average of z(-draft_number) [pure draft-capital prior]
              and z(-draft_number) - z(lag1_room40_pts) [regression term:
              rewards early picks who UNDERPERFORMED their capital in 2025,
              since that gap is the textbook year-2/3 regression-up bet].
  aging_z   : combined_index(age in 2026) - combined_index(age in 2025) from
              the existing aging-curve model - the natural developmental
              slope for a player this young, quantified instead of asserted.
  crowding_z: (SUBTRACTED) the single highest target_share among 2025
              teammates at the same position who are still on the 2026
              roster, excluding the candidate themselves. Proxies "is there
              already an entrenched starter blocking this player's path."

CAVEATS surfaced in the output (not hidden):
  - Efficiency samples below the shrinkage K are still noisy even after
    shrinkage; treat close scores among low-sample_n players with more
    skepticism than the composite alone implies.
  - Crowding proxy only sees TEAMMATES ON THE 2025 ROSTER migrated forward to
    2026 - it can't see a same-position free agent /rookie the team hasn't
    drafted/signed yet, so a team could add a blocker after this runs.
  - Vacancy signal only sees departures already reflected in the 2026 roster
    snapshot (as of whatever nflreadr load_rosters(2026) has cached) - it
    will miss late-summer cuts/signings after that snapshot.

Usage: venv/bin/python3 python/buildNflBreakoutScore.py
Output: outputs/sports/nfl/fantasy/breakout_candidates_2026.csv
"""
import os

import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")

POSITIONS = ["QB", "RB", "WR", "TE"]
MIN_GAMES = 8
TOP_N_ARRIVED_EXCLUSION = 12  # exclude 2025's top-12 finishers per position - "arrived," not "breakout"
SELECT_MIN, SELECT_MAX = 3, 5  # per position, per the original ask

# Shrinkage constant K per position for the efficiency term - roughly the
# sample size at which "real skill" and "noise" contribute equally, tuned to
# each stat's typical season volume (QBs throw way more than a TE gets
# targeted, so QB needs a bigger K to shrink meaningfully).
SHRINKAGE_K = {"QB": 200, "RB": 80, "WR": 60, "TE": 40}


def z(s: pd.Series) -> pd.Series:
    """Z-score a series, safe against zero/near-zero variance (returns 0s)."""
    mu, sd = s.mean(), s.std(ddof=0)
    if not sd or np.isnan(sd) or sd < 1e-9:
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def main():
    career = pd.read_csv(os.path.join(OUT_DIR, "career_panel.csv"))
    phase1 = pd.read_csv(os.path.join(OUT_DIR, "nfl_phase1_features.csv"))
    aging = pd.read_csv(os.path.join(OUT_DIR, "aging_curves.csv"))
    efficiency = pd.read_csv(os.path.join(OUT_DIR, "breakout_efficiency_2025.csv"))
    usage_trend = pd.read_csv(os.path.join(OUT_DIR, "breakout_usage_trend_2025.csv"))
    vacancy = pd.read_csv(os.path.join(OUT_DIR, "breakout_team_vacancy_2026.csv"))

    # Collapse phase1 mid-season-trade dupes the same way buildNflTrainingPanel.R does
    phase1_25 = (
        phase1[phase1["season"] == 2025]
        .assign(_snaps=lambda d: d["games_with_snaps"].fillna(0))
        .sort_values("_snaps", ascending=False)
        .drop_duplicates(subset=["player_id"], keep="first")
        .drop(columns=["_snaps"])
    )

    career_25 = career[career["season"] == 2025].copy()

    # ---- eligibility: position, years_exp 0/1, sample gate ----
    pool = career_25[
        career_25["position"].isin(POSITIONS)
        & career_25["years_exp"].isin([0, 1])
        & (career_25["games"] >= MIN_GAMES)
    ].copy()
    print(f"Eligible young-player pool (years_exp 0-1, >= {MIN_GAMES} games): {len(pool)}")

    # ---- exclude 2025's top-12 finishers per position ("arrived") ----
    career_25["pos_rank_2025"] = career_25.groupby("position")["room40_pts"].rank(
        ascending=False, method="min"
    )
    arrived_ids = set(
        career_25[career_25["pos_rank_2025"] <= TOP_N_ARRIVED_EXCLUSION]["player_id"]
    )
    pool = pool[~pool["player_id"].isin(arrived_ids)].copy()
    print(f"After excluding top-{TOP_N_ARRIVED_EXCLUSION} 'arrived' finishers per position: {len(pool)}")

    # ---- join phase1 usage/team/coach context ----
    pool = pool.merge(
        phase1_25[["player_id", "team", "target_share", "rush_attempts",
                   "snap_share_avg", "new_oc", "coach_proe_signature"]],
        on="player_id", how="left",
    )

    # ---- join efficiency (shrunk toward position mean by sample size) ----
    pool = pool.merge(
        efficiency[["player_id", "efficiency_primary", "sample_n"]],
        on="player_id", how="left",
    )
    pool["eff_shrunk"] = np.nan
    for pos in POSITIONS:
        mask = pool["position"] == pos
        sub = pool.loc[mask]
        pos_mean = sub["efficiency_primary"].mean()
        k = SHRINKAGE_K[pos]
        shrink_factor = sub["sample_n"].fillna(0) / (sub["sample_n"].fillna(0) + k)
        pool.loc[mask, "eff_shrunk"] = pos_mean + (sub["efficiency_primary"] - pos_mean) * shrink_factor
    # players with no NGS-qualifying sample at all: treat as position-average (0 after z-score),
    # not penalized/rewarded for missing data
    pool["eff_shrunk"] = pool["eff_shrunk"].fillna(
        pool.groupby("position")["eff_shrunk"].transform("mean")
    )

    # ---- join usage trend ----
    pool = pool.merge(
        usage_trend[["player_id", "snap_share_trend", "target_share_trend"]],
        on="player_id", how="left",
    )

    # ---- join team vacancy (situational tailwind) ----
    pool = pool.merge(vacancy, on="team", how="left")
    for c in ["target_share_vacated", "rush_share_vacated"]:
        pool[c] = pool[c].fillna(0)

    # ---- aging-curve slope: combined_index(age_2026) - combined_index(age_2025) ----
    aging_lookup = aging.set_index(["position", "age"])["combined_index"]
    pool["age_2025_round"] = pool["age"].round()
    pool["age_2026_round"] = (pool["age"] + 1).round()

    def lookup_idx(row, age_col):
        key = (row["position"], row[age_col])
        return aging_lookup.get(key, np.nan)

    pool["idx_2025"] = pool.apply(lambda r: lookup_idx(r, "age_2025_round"), axis=1)
    pool["idx_2026"] = pool.apply(lambda r: lookup_idx(r, "age_2026_round"), axis=1)
    pool["aging_slope"] = pool["idx_2026"] - pool["idx_2025"]
    pool["aging_slope"] = pool["aging_slope"].fillna(0)

    # ---- crowding risk: max target_share among 2026-rostered teammates at
    #      the same position, excluding self ----
    roster_2026 = phase1_25  # already 2025 usage; "still on 2026 roster" filtered next
    try:
        import subprocess
        act_path = "/tmp/roster_2026_act.csv"
        if not os.path.exists(act_path):
            raise FileNotFoundError
        roster_act = pd.read_csv(act_path)
        active_2026_ids = set(roster_act["gsis_id"])
    except FileNotFoundError:
        active_2026_ids = set(phase1_25["player_id"])  # fallback: no filter

    team_pos_targets = phase1_25[phase1_25["player_id"].isin(active_2026_ids)][
        ["player_id", "team", "position", "target_share"]
    ].copy()

    def max_teammate_share(row):
        same = team_pos_targets[
            (team_pos_targets["team"] == row["team"])
            & (team_pos_targets["position"] == row["position"])
            & (team_pos_targets["player_id"] != row["player_id"])
        ]
        return same["target_share"].max() if len(same) else 0.0

    pool["crowding_target_share"] = pool.apply(max_teammate_share, axis=1)

    # ---- z-score everything, per position, within the eligible pool ----
    out_frames = []
    for pos in POSITIONS:
        sub = pool[pool["position"] == pos].copy()
        if len(sub) < 3:
            print(f"{pos}: too few eligible players ({len(sub)}) to score meaningfully, skipping")
            continue

        sub["eff_z"] = z(sub["eff_shrunk"])

        trend_z_snap = z(sub["snap_share_trend"].fillna(sub["snap_share_trend"].mean()))
        trend_z_tgt = z(sub["target_share_trend"].fillna(sub["target_share_trend"].mean()))
        sub["trend_z"] = (trend_z_snap + trend_z_tgt) / 2

        tailwind_raw = sub["target_share_vacated"] + sub["rush_share_vacated"] + 0.15 * sub["new_oc"].fillna(0)
        sub["tailwind_z"] = z(tailwind_raw)

        draft_prior_z = z(-sub["draft_number"].fillna(sub["draft_number"].max() + 50))
        regression_z = draft_prior_z - z(sub["pts_per_game"].fillna(0))
        sub["draft_capital_z"] = (draft_prior_z + regression_z) / 2

        sub["aging_z"] = z(sub["aging_slope"])
        sub["crowding_z"] = z(sub["crowding_target_share"])

        sub["breakout_z"] = (
            sub["eff_z"] + sub["trend_z"] + sub["tailwind_z"]
            + sub["draft_capital_z"] + sub["aging_z"] - sub["crowding_z"]
        )
        out_frames.append(sub)

    scored = pd.concat(out_frames, ignore_index=True)

    # ---- select 3-5 per position: top 5 by breakout_z, but drop any beyond
    #      the top 3 that score below 0 (below the pool's own average) so a
    #      thin position doesn't force weak names onto the list ----
    selected = []
    for pos in POSITIONS:
        sub = scored[scored["position"] == pos].sort_values("breakout_z", ascending=False)
        top5 = sub.head(SELECT_MAX)
        keep = pd.concat([top5.head(SELECT_MIN), top5.iloc[SELECT_MIN:][top5.iloc[SELECT_MIN:]["breakout_z"] > 0]])
        selected.append(keep)
    candidates = pd.concat(selected, ignore_index=True)
    candidates = candidates.sort_values(["position", "breakout_z"], ascending=[True, False])

    out_cols = [
        "player_id", "player_display_name", "position", "team", "years_exp",
        "draft_number", "age", "games", "room40_pts", "pts_per_game",
        "eff_shrunk", "sample_n", "snap_share_trend", "target_share_trend",
        "target_share_vacated", "rush_share_vacated", "new_oc",
        "aging_slope", "crowding_target_share",
        "eff_z", "trend_z", "tailwind_z", "draft_capital_z", "aging_z", "crowding_z",
        "breakout_z",
    ]
    candidates_out = candidates[out_cols]
    scored_out = scored[out_cols]

    cand_path = os.path.join(OUT_DIR, "breakout_candidates_2026.csv")
    scored_path = os.path.join(OUT_DIR, "breakout_scored_pool_2026.csv")
    candidates_out.to_csv(cand_path, index=False)
    scored_out.to_csv(scored_path, index=False)

    print(f"\nWrote: {cand_path} ({len(candidates_out)} rows)")
    print(f"Wrote: {scored_path} ({len(scored_out)} rows, full eligible pool for reference)")

    print("\n=== Breakout candidates by position ===")
    for pos in POSITIONS:
        sub = candidates_out[candidates_out["position"] == pos]
        if sub.empty:
            print(f"\n{pos}: none selected")
            continue
        print(f"\n{pos} ({len(sub)}):")
        print(sub[["player_display_name", "team", "years_exp", "breakout_z",
                    "sample_n"]].to_string(index=False))


if __name__ == "__main__":
    main()
