#!/usr/bin/env python3
"""
buildNflStrategyGuide.py

Phase 4, step 6: round-by-round position-priority OUTLINE for each named
draft strategy (zero_rb, hero_rb, robust_rb, balanced, bpa) - answers "which
position should I take in round N if I'm following strategy X" as a
printable reference card, complementing the existing tools:
  - buildNflDraftStrategySim.py answers "which strategy wins overall"
    (aggregate starting-lineup VORP).
  - buildNflPositionalScarcity.py answers "which position has the steepest
    cliff coming up" (context-free of any specific strategy).
  - THIS script answers "round-by-round, what does following strategy X
    actually look like in practice" - not hand-picked, but the ACTUAL
    position picked by the sim's marginal-value logic in buildNflDraftStrategySim.py,
    run across all 12 draft slots and summarized by mode (most common pick).

METHOD: re-runs the exact same simulate_draft() logic from
buildNflDraftStrategySim.py (same field model, same marginal-starting-lineup-
value picker, same position caps) across all 12 draft slots per strategy,
but additionally logs which POSITION was drafted each round (not just the
final score). Rounds where slots disagree on position show the pct of slots
that agreed - low agreement = "flexible round" (the strategy doesn't force
a position, take a real value swing wherever it is).

LIMITATIONS (inherited from buildNflDraftStrategySim.py - be upfront):
  - Field is a non-adaptive best-ADP-available proxy, not a real opponent.
  - No injury news / bye weeks / trades.
  - Deterministic given the ADP field (no opponent-behavior randomness).
  - "Most common position" can hide real slot-to-slot variation - check the
    pct_agreement column before treating a round as a hard rule.

Usage: venv/bin/python3 python/buildNflStrategyGuide.py
Output: outputs/sports/nfl/fantasy/strategy_guide.csv
        outputs/sports/nfl/fantasy/strategy_guide.png
"""
import os
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from buildNflDraftStrategySim import (
    ADP_CAP, FLEX_ELIGIBLE, N_ROUNDS, N_TEAMS, OUT_DIR, POSITION_CAPS,
    STARTERS, STRATEGIES, score_lineup, snake_order, tiebreak_score,
)

POS_COLORS = {"QB": "#7B4CE0", "RB": "#2E9E4E", "WR": "#2E7BE0", "TE": "#E0A02E", "-": "#DDDDDD"}


def simulate_draft_logged(pool_df, strategy, user_slot):
    """Same logic as buildNflDraftStrategySim.simulate_draft, but also
    returns a {round_num: position} log for the user's picks."""
    available = pool_df.copy().reset_index(drop=True)
    order = snake_order(N_TEAMS, N_ROUNDS)

    rosters = {t: [] for t in range(N_TEAMS)}
    roster_counts = {t: {} for t in range(N_TEAMS)}
    pick_log = {}

    for pick_num, team in enumerate(order):
        if available.empty:
            break
        round_num = pick_num // N_TEAMS + 1

        if team == user_slot:
            allowed_positions = strategy.pick_position(round_num, roster_counts[team])
            candidates = available
            if allowed_positions:
                filtered = available[available["position"].isin(allowed_positions)]
                if not filtered.empty:
                    candidates = filtered
            capped_out = {p for p, cap in POSITION_CAPS.items()
                          if roster_counts[team].get(p, 0) >= cap}
            if capped_out:
                filtered2 = candidates[~candidates["position"].isin(capped_out)]
                if not filtered2.empty:
                    candidates = filtered2

            current_roster = rosters[team]
            baseline = score_lineup(current_roster, pool_df)
            shortlist = (candidates.sort_values("vorp", ascending=False)
                         .groupby("position", group_keys=False).head(2))
            best_row, best_gain, best_tiebreak = None, -1e18, -1e18
            for _, cand_row in shortlist.iterrows():
                trial_roster = current_roster + [cand_row.to_dict()]
                gain = score_lineup(trial_roster, pool_df) - baseline
                tiebreak = tiebreak_score(cand_row["position"], cand_row["vorp"])
                if (gain, tiebreak) > (best_gain, best_tiebreak):
                    best_gain, best_tiebreak, best_row = gain, tiebreak, cand_row
            pick_row = best_row
            pick_log[round_num] = pick_row["position"]
        else:
            capped_out = {p for p, cap in POSITION_CAPS.items()
                          if roster_counts[team].get(p, 0) >= cap}
            candidates = available
            if capped_out:
                filtered = available[~available["position"].isin(capped_out)]
                if not filtered.empty:
                    candidates = filtered
            pick_row = candidates.sort_values("adp_overall", ascending=True).iloc[0]

        rosters[team].append(pick_row.to_dict())
        pos = pick_row["position"]
        roster_counts[team][pos] = roster_counts[team].get(pos, 0) + 1
        available = available[available["full_name"] != pick_row["full_name"]]

    return pick_log


def main():
    df = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    df = df[(df["adp_overall"] <= ADP_CAP) &
            (df["position"].isin(list(STARTERS.keys())))].copy()
    df = df.dropna(subset=["vorp", "adp_overall"])

    rows = []
    for strategy in STRATEGIES:
        # round_num -> list of positions picked, one per draft slot
        round_positions = {r: [] for r in range(1, N_ROUNDS + 1)}
        for user_slot in range(N_TEAMS):
            pick_log = simulate_draft_logged(df, strategy, user_slot)
            for r, pos in pick_log.items():
                round_positions[r].append(pos)

        for r in range(1, N_ROUNDS + 1):
            picks = round_positions[r]
            if not picks:
                continue
            counts = Counter(picks)
            top_pos, top_n = counts.most_common(1)[0]
            rows.append({
                "strategy": strategy.name,
                "round": r,
                "top_position": top_pos,
                "pct_slots_agreeing": round(100 * top_n / len(picks), 1),
                "n_slots": len(picks),
            })

    guide = pd.DataFrame(rows)
    out_csv = os.path.join(OUT_DIR, "strategy_guide.csv")
    guide.to_csv(out_csv, index=False)
    print(f"Wrote: {out_csv}")

    print("\n=== Round-by-round position priority per strategy (mode across 12 draft slots) ===")
    pivot_pos = guide.pivot(index="round", columns="strategy", values="top_position")
    pivot_pct = guide.pivot(index="round", columns="strategy", values="pct_slots_agreeing")
    strategy_order = [s.name for s in STRATEGIES]
    pivot_pos = pivot_pos[strategy_order]
    pivot_pct = pivot_pct[strategy_order]
    print(pivot_pos.to_string())

    # ---- printable table: rounds down, strategies across, cell = position ----
    n_rounds_shown = min(12, N_ROUNDS)  # rounds 1-12 cover all starters + flex + early bench
    fig, ax = plt.subplots(figsize=(11, 0.42 * n_rounds_shown + 1.6))
    ax.axis("off")
    col_labels = [s.upper().replace("_", " ") for s in strategy_order]
    row_labels = [f"Rd {r}" for r in range(1, n_rounds_shown + 1)]

    cell_text = []
    cell_colors = []
    for r in range(1, n_rounds_shown + 1):
        row_text, row_color = [], []
        for s in strategy_order:
            pos = pivot_pos.loc[r, s] if r in pivot_pos.index else "-"
            pct = pivot_pct.loc[r, s] if r in pivot_pct.index else 0
            pos = pos if isinstance(pos, str) else "-"
            row_text.append(f"{pos} ({pct:.0f}%)")
            row_color.append(POS_COLORS.get(pos, "#FFFFFF"))
        cell_text.append(row_text)
        cell_colors.append(row_color)

    table = ax.table(cellText=cell_text, cellColours=cell_colors,
                      rowLabels=row_labels, colLabels=col_labels,
                      loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)

    fig.suptitle("Room 40 - Draft Strategy Guide\n"
                  "Most common position picked each round, per strategy (12-slot simulation)\n"
                  "% = how many of the 12 draft slots agreed on that position",
                  fontsize=13, fontweight="bold", y=0.995)
    fig.text(0.5, 0.01,
              "CAVEAT: field model = non-adaptive best-ADP-available (doesn't react to runs). "
              "Low % rounds = strategy is flexible there, don't force it.",
              ha="center", fontsize=8, style="italic", color="dimgray")

    plt.subplots_adjust(top=0.80, bottom=0.06)
    out_png = os.path.join(OUT_DIR, "strategy_guide.png")
    plt.savefig(out_png, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"Wrote: {out_png}")

    # ---- overall ranking summary (reuses draft_strategy_sim.csv if present) ----
    sim_csv = os.path.join(OUT_DIR, "draft_strategy_sim.csv")
    if os.path.exists(sim_csv):
        sim_df = pd.read_csv(sim_csv)
        summary = sim_df.groupby("strategy")["starting_lineup_vorp"].mean().sort_values(ascending=False)
        print("\n=== Overall ranking (mean starting-lineup VORP across all 12 slots, from draft_strategy_sim.csv) ===")
        print(summary.round(1).to_string())


if __name__ == "__main__":
    main()
