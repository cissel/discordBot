#!/usr/bin/env python3
"""
buildNflVorp.py

Phase 3, step 2: the actual "moneyball" layer. Computes Value Over Replacement
Player (VORP) using ROOM 40's ACTUAL roster math (not a generic 12-team
league assumption), then joins with current ADP to find market inefficiencies.

Room 40 settings (confirmed via Sleeper API, see nfl-room40-draft-prep.md):
  12 teams, roster: QB, RB, RB, WR, WR, TE, FLEX, K, DEF, BN x5
  Starting slots relevant to skill positions: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX

Replacement level definition: the player ranked at the "last starter" spot
league-wide for that position, accounting for the shared FLEX slot (RB/WR/TE
eligible). This is standard fantasy sabermetric VORP methodology - replacement
level is NOT "average player", it's "the player you could get for free
(waiver wire) right after all 12 teams have finished drafting their starters."

FLEX allocation: rather than guess how the FLEX slot splits across RB/WR/TE,
derive it EMPIRICALLY from real historical Room 40 scoring - see
buildNflFlexSplit.py for full methodology. Per season 2019-2025: lock in
guaranteed starters (top 24 RB, top 24 WR, top 12 TE by real room40_pts),
pool everyone left over position-blind, and see who actually wins the 12
league-wide flex slots by points scored. Averaged across seasons, the split
came out ~95% WR / ~5% RB / ~0% TE - a genuinely lopsided result, but a real
one: Room 40 is full PPR (1.0 pt/reception, confirmed via Sleeper league
settings), and in full-PPR formats RB depth collapses hard past the top ~20
backs while WR depth stays flat much further down the position (the same
dynamic behind "Zero RB" draft strategies). This replaced a prior fixed
55/40/5 (RB/WR/TE) assumption that was itself inconsistent with this file's
own docstring, which separately claimed 60/35/5 - neither matched the data.
Falls back to the hardcoded split below only if the empirical CSV is missing.

Usage: venv/bin/python3 python/buildNflVorp.py
Output: outputs/sports/nfl/fantasy/vorp_2026.csv
"""
import os

import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")

N_TEAMS = 12
STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 1
# Fallback only - see buildNflFlexSplit.py for the empirically-derived split,
# loaded from flex_split_empirical.csv at runtime below.
FLEX_SPLIT_FALLBACK = {"RB": 0.55, "WR": 0.40, "TE": 0.05}


def load_flex_split():
    """Load the empirically-derived FLEX split (see buildNflFlexSplit.py).
    Falls back to a hardcoded guess only if that file hasn't been built yet."""
    split_path = os.path.join(OUT_DIR, "flex_split_empirical.csv")
    if not os.path.exists(split_path):
        print("WARNING: flex_split_empirical.csv not found - run "
              "buildNflFlexSplit.py first. Falling back to hardcoded guess "
              f"{FLEX_SPLIT_FALLBACK}.")
        return dict(FLEX_SPLIT_FALLBACK)
    split_df = pd.read_csv(split_path)
    avg_row = split_df[split_df["source"] == "average"].iloc[0]
    split = {pos: float(avg_row[pos]) for pos in ("RB", "WR", "TE")}
    print(f"Loaded empirical FLEX split (avg 2019-2025): "
          f"RB {split['RB']*100:.1f}% / WR {split['WR']*100:.1f}% / TE {split['TE']*100:.1f}%")
    return split


FLEX_SPLIT = load_flex_split()


def compute_replacement_rank(position):
    """Replacement rank = last startable player league-wide at that position,
    including their share of the shared FLEX slot."""
    base = STARTERS[position] * N_TEAMS
    flex_share = FLEX_SLOTS * N_TEAMS * FLEX_SPLIT.get(position, 0)
    return int(round(base + flex_share))


def main():
    proj = pd.read_csv(os.path.join(OUT_DIR, "projections_2026.csv"))
    proj = proj.dropna(subset=["projected_room40_pts"]).copy()
    proj["player_type"] = "veteran"

    # merge in rookie prior scores (separate model, different confidence per
    # position - RB rookie predictions are known-weak, see skill notes) so the
    # VORP board is complete rather than silently missing the entire rookie class
    rookie_path = os.path.join(OUT_DIR, "projections_2026_rookies.csv")
    if os.path.exists(rookie_path):
        rookies = pd.read_csv(rookie_path).dropna(subset=["projected_room40_pts_rookie"]).copy()
        rookies = rookies.rename(columns={"projected_room40_pts_rookie": "projected_room40_pts"})
        rookies["player_type"] = "rookie"
        rookies["adp_position"] = rookies.get("adp_position", np.nan)
        rookies["sleeper_proj_pts_week1"] = np.nan
        rookies["lag1_room40_pts"] = np.nan
        rookies["lag1_pts_per_game"] = np.nan
        rookies["lag1_games"] = np.nan
        rookies["lag1_target_share"] = np.nan
        rookies["lag1_snap_share_avg"] = np.nan
        rookies["model_used"] = "rookie_prior_ridge"
        rookies["new_oc"] = 0
        rookies["coach_proe_signature"] = np.nan
        rookies["team_changed"] = 0  # rookies have no prior-season team to compare against
        common_cols = [c for c in proj.columns if c in rookies.columns]
        proj = pd.concat([proj[common_cols], rookies[common_cols]], ignore_index=True)
        print(f"Merged {len(rookies)} rookie projections into VORP pool "
              f"(total pool now {len(proj)})")

    replacement_ranks = {pos: compute_replacement_rank(pos) for pos in STARTERS}
    print("Replacement level rank by position (starters + FLEX share):")
    for pos, rank in replacement_ranks.items():
        print(f"  {pos}: rank {rank} (of {N_TEAMS} teams x {STARTERS[pos]} starters "
              f"+ {FLEX_SLOTS * N_TEAMS * FLEX_SPLIT.get(pos, 0):.1f} flex share)")

    replacement_values = {}
    for pos, rank in replacement_ranks.items():
        pos_df = proj[proj["position"] == pos].sort_values("projected_room40_pts", ascending=False)
        if len(pos_df) >= rank:
            replacement_values[pos] = pos_df.iloc[rank - 1]["projected_room40_pts"]
        else:
            replacement_values[pos] = pos_df["projected_room40_pts"].min()
        print(f"  {pos} replacement value: {replacement_values[pos]:.1f} pts "
              f"(player at rank {rank})")

    proj["replacement_value"] = proj["position"].map(replacement_values)
    proj["vorp"] = proj["projected_room40_pts"] - proj["replacement_value"]

    # Market inefficiency: rank by VORP vs rank by ADP. Positive = market is
    # drafting this player LATER than their value suggests (draft target).
    proj["vorp_rank"] = proj["vorp"].rank(ascending=False, method="min")
    proj["adp_rank"] = proj["adp_overall"].rank(ascending=True, method="min")
    proj["value_gap"] = proj["adp_rank"] - proj["vorp_rank"]  # positive = undervalued by ADP

    # KNOWN LIMITATION FLAG: lag1_room40_pts (season TOTAL, not rate) is a top
    # model feature. A player who missed significant time to injury in 2025 gets
    # a deflated season total even if their PER-GAME rate was fine or improved -
    # the model can't distinguish "declined" from "was hurt." Flag anyone who
    # played a materially shortened 2025 season (<12 of ~17 games) so a human
    # can sanity-check whether a "fade" is really just an injury-report artifact
    # rather than a genuine value read. Discovered Jul 29 2026 - see several
    # 2025-injured stars (Ja Marr-tier WRs, Burrow, Purdy) showing up as false
    # fades despite stable/improved per-game production.
    proj["injury_shortened_2025"] = proj["lag1_games"] < 12
    proj["lag1_pace_adj_pts"] = proj["lag1_pts_per_game"] * 17  # what a healthy 17-game pace implies

    # ELITE-MOVER CAVEAT FLAG: flag players who (a) changed teams for 2026 and
    # (b) carried a big target share (>=25%, a real WR1/entrenched role) into
    # that move. The model DOES have a team_changed feature now (see
    # trainNflFantasyModel.py, added 2026-08 after this exact case - Wan'Dale
    # Robinson - got flagged as a suspiciously overvalued live-draft-tool
    # recommendation), but that fix is only a modest, aggregate-level
    # discount, not a precise correction. Tested and REJECTED a more specific
    # "destination target competition" feature (correlation with outcome was
    # not statistically significant, r=-0.03 to 0.07, p>0.2 across three
    # formulations) - the honest finding is these cases carry real INFORMATION
    # GAP that this data can't resolve (e.g. Davante Adams/Tyreek Hill/A.J.
    # Brown moved into loaded rooms and were still great; Jarvis Landry/Will
    # Fuller moved and fell off - no clean signal separates them here).
    # Flag these for MANUAL research (beat writers, camp reports, target
    # projections) rather than trust the model number blindly - same
    # philosophy as injury_shortened_2025 above.
    proj["elite_mover_2026"] = (proj["team_changed"] == 1) & (proj["lag1_target_share"] >= 0.25)

    out = proj.sort_values("vorp", ascending=False)
    out_path = os.path.join(OUT_DIR, "vorp_2026.csv")
    out.to_csv(out_path, index=False)

    print(f"\nWrote: {out_path}")
    print(f"Rows: {len(out)}")

    print("\n=== Top 15 VORP overall ===")
    print(out.head(15)[["full_name", "position", "team", "adp_overall", "vorp", "value_gap"]]
          .to_string(index=False))

    print("\n=== Top 15 biggest positive value gaps (market undervalues, ADP later than value) ===")
    undervalued = out.sort_values("value_gap", ascending=False).head(15)
    print(undervalued[["full_name", "position", "team", "adp_overall", "adp_rank", "vorp_rank", "value_gap"]]
          .to_string(index=False))

    print("\n=== Top 15 biggest negative value gaps (market overvalues, fade candidates) ===")
    overvalued = out.sort_values("value_gap", ascending=True).head(15)
    print(overvalued[["full_name", "position", "team", "adp_overall", "adp_rank", "vorp_rank", "value_gap"]]
          .to_string(index=False))

    # DRAFT-RELEVANT view: restrict to ADP <= 200 (12-team x ~14-16 roster spots =
    # realistic draftable range). Beyond this, both ADP and small-sample-backup
    # projections get noisy and the "value gap" signal stops being meaningful -
    # nobody is making a real draft-day decision on a rank-350 vs rank-300 waiver
    # wire RB. This is the list that actually matters on draft day.
    draftable = out[out["adp_overall"] <= 200].copy()
    draftable["value_gap"] = draftable["adp_rank"] - draftable["vorp"].rank(ascending=False, method="min")

    print("\n" + "=" * 70)
    print("DRAFT-RELEVANT VIEW (ADP <= 200, the range that matters on draft day)")
    print("=" * 70)
    print("\n=== Top 12 draft targets (undervalued within draftable range) ===")
    print(draftable.sort_values("value_gap", ascending=False).head(12)
          [["full_name", "position", "team", "adp_overall", "vorp", "value_gap"]]
          .to_string(index=False))

    print("\n=== Top 12 fade candidates (overvalued within draftable range) ===")
    print(draftable.sort_values("value_gap", ascending=True).head(12)
          [["full_name", "position", "team", "adp_overall", "vorp", "value_gap"]]
          .to_string(index=False))

    draftable.to_csv(os.path.join(OUT_DIR, "vorp_2026_draftable.csv"), index=False)
    print(f"\nWrote draftable-range subset: {os.path.join(OUT_DIR, 'vorp_2026_draftable.csv')} ({len(draftable)} rows)")


if __name__ == "__main__":
    main()
