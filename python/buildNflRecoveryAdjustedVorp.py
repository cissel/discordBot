#!/usr/bin/env python3
"""
buildNflRecoveryAdjustedVorp.py

Phase 3 extension: apply the age-informed injury-recovery signal
(injury_recovery_context_2026.csv, built from the nflreadr::load_injuries()
2009-2025 study, n=185 qualifying player-seasons) as a bounded correction to
the model's projected_room40_pts, then recompute VORP/value_gap over the
FULL player pool so the correction properly reshuffles replacement-level
math and rankings (not just the 28-player flagged subset).

Why a multiplicative rate correction, not a full re-projection: the trained
model (trainNflFantasyModel.py) already leans heavily on lag1_pts_per_game
as its rate-baseline feature. We don't have per-player recovery-adjusted
retraining data (n=19 usable baselines is far too small to retrain on), so
instead we scale each flagged player's EXISTING model projection by
recovery_informed_ppg / lag1_pts_per_game - i.e. "what would this player's
projection look like if the model's rate input had been the age-informed
recovery-adjusted rate instead of the raw (currently injury-influenced,
in some cases small-sample) 2025 rate." This only touches the ~19 players
where a real pre-injury 2024 healthy baseline exists (10+ games) - see
injury_recovery_context_2026.csv for the other 9 flagged players that
still only carry the raw injury_shortened_2025 flag with no adjustment.

Adjustment factor is clipped to [0.75, 1.30] - a directional nudge, not a
license to swing a projection by more than +/-30%. This is not a
replacement for reading the per-player caveats in
injury_recovery_context_2026.csv; it is a ranking-order adjustment on top
of the existing VORP board.

Usage: venv/bin/python3 python/buildNflRecoveryAdjustedVorp.py
Output: outputs/sports/nfl/fantasy/vorp_2026_recovery_adjusted.csv
        outputs/sports/nfl/fantasy/vorp_2026_recovery_adjusted_draftable.csv
"""
import os

import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")

N_TEAMS = 12
STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 1
FLEX_SPLIT_FALLBACK = {"RB": 0.55, "WR": 0.40, "TE": 0.05}
ADJ_FACTOR_CLIP = (0.75, 1.30)


def load_flex_split():
    split_path = os.path.join(OUT_DIR, "flex_split_empirical.csv")
    if not os.path.exists(split_path):
        return dict(FLEX_SPLIT_FALLBACK)
    split_df = pd.read_csv(split_path)
    avg_row = split_df[split_df["source"] == "average"].iloc[0]
    return {pos: float(avg_row[pos]) for pos in ("RB", "WR", "TE")}


FLEX_SPLIT = load_flex_split()


def compute_replacement_rank(position):
    base = STARTERS[position] * N_TEAMS
    flex_share = FLEX_SLOTS * N_TEAMS * FLEX_SPLIT.get(position, 0)
    return int(round(base + flex_share))


def main():
    vorp = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    ctx = pd.read_csv(os.path.join(OUT_DIR, "injury_recovery_context_2026.csv"))

    # only the players with a usable recovery-informed rate (real 2024 healthy
    # baseline, 10+ games) get an adjustment - the other 9 flagged players in
    # ctx keep their original projection untouched
    ctx_adj = ctx.dropna(subset=["recovery_informed_ppg", "lag1_pts_per_game"]).copy()
    ctx_adj = ctx_adj[ctx_adj["lag1_pts_per_game"] > 0]
    ctx_adj["adj_factor_raw"] = ctx_adj["recovery_informed_ppg"] / ctx_adj["lag1_pts_per_game"]
    ctx_adj["adj_factor"] = ctx_adj["adj_factor_raw"].clip(*ADJ_FACTOR_CLIP)

    merge_cols = ["full_name", "position", "team", "adj_factor", "adj_factor_raw",
                  "recovery_informed_ppg", "ppg_delta_recovery_vs_model"]
    out = vorp.merge(ctx_adj[merge_cols], on=["full_name", "position", "team"], how="left")

    out["recovery_adjusted"] = out["adj_factor"].notna()
    out["projected_room40_pts_original"] = out["projected_room40_pts"]
    out["projected_room40_pts"] = np.where(
        out["adj_factor"].notna(),
        out["projected_room40_pts_original"] * out["adj_factor"],
        out["projected_room40_pts_original"],
    )

    n_adj = int(out["recovery_adjusted"].sum())
    print(f"Applied recovery adjustment to {n_adj} players "
          f"(of {ctx_adj.shape[0]} with a usable baseline - some may not have "
          f"matched the full pool by name/team)")

    # ---- recompute VORP from scratch on the adjusted projections ----
    replacement_ranks = {pos: compute_replacement_rank(pos) for pos in STARTERS}
    replacement_values = {}
    for pos, rank in replacement_ranks.items():
        pos_df = out[out["position"] == pos].sort_values("projected_room40_pts", ascending=False)
        if len(pos_df) >= rank:
            replacement_values[pos] = pos_df.iloc[rank - 1]["projected_room40_pts"]
        else:
            replacement_values[pos] = pos_df["projected_room40_pts"].min()

    out["replacement_value"] = out["position"].map(replacement_values)
    out["vorp"] = out["projected_room40_pts"] - out["replacement_value"]
    out["vorp_rank"] = out["vorp"].rank(ascending=False, method="min")
    out["adp_rank"] = out["adp_overall"].rank(ascending=True, method="min")
    out["value_gap"] = out["adp_rank"] - out["vorp_rank"]
    out["vorp_rank_shift"] = out["vorp_rank_x"] if "vorp_rank_x" in out.columns else np.nan

    out = out.sort_values("vorp", ascending=False)
    out_path = os.path.join(OUT_DIR, "vorp_2026_recovery_adjusted.csv")
    out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path} ({len(out)} rows)")

    draftable = out[out["adp_overall"] <= 200].copy()
    draftable["value_gap"] = draftable["adp_rank"] - draftable["vorp"].rank(ascending=False, method="min")
    draftable_path = os.path.join(OUT_DIR, "vorp_2026_recovery_adjusted_draftable.csv")
    draftable.to_csv(draftable_path, index=False)
    print(f"Wrote: {draftable_path} ({len(draftable)} rows)")

    print("\n=== Recovery-adjusted players: shift in VORP rank vs pre-adjustment (draftable range) ===")
    original = vorp[["full_name", "position", "vorp_rank", "value_gap"]].rename(
        columns={"vorp_rank": "vorp_rank_before", "value_gap": "value_gap_before"})
    cmp = draftable.merge(original, on=["full_name", "position"], how="left")
    cmp = cmp[cmp["recovery_adjusted"]]
    cmp["vorp_rank_delta"] = cmp["vorp_rank_before"] - cmp["vorp_rank"]  # positive = moved UP in rank
    print(cmp.sort_values("vorp_rank_delta", ascending=False)
          [["full_name", "position", "adp_overall", "adj_factor", "vorp_rank_before", "vorp_rank",
            "vorp_rank_delta", "value_gap_before", "value_gap"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
