#!/usr/bin/env python3
"""
buildNflPositionalScarcity.py

Phase 4, step 3 (basic version): a first data-driven pass at "what order
should I draft positions in", using the existing VORP/ADP pipeline rather
than guessing/anecdote (e.g. "Zero RB").

METHOD (intentionally simple - a starting point, not a full solver):
  1. Take the draftable pool (ADP <= 200, already built) and bucket every
     player into a "round" by their real market ADP (12-team league, so
     round = ceil(adp_overall / 12)). This approximates "when this player
     is typically gone" in a real draft.
  2. For each position x round, compute the MEAN VORP of players whose ADP
     falls in that round. This is "how much value is typically on the board
     at position P if you wait until round R to take it."
  3. Compute the round-over-round DROP in mean VORP per position. A big
     drop from round R to R+1 = a real cliff (scarce, must draft this round
     or lose real value). A small/flat drop = safe to wait (deep position).
  4. Surface, per round, which position has the steepest cliff coming up
     next round - i.e. a simple "priority order" signal for that pick.

LIMITATIONS (be upfront about these, don't oversell the basic version):
  - This assumes ADP order = draft order, i.e. it does NOT simulate actual
    draft flow, opponent behavior, or your own roster construction needs
    (e.g. you already have 2 RBs, so RB cliffs matter less to you now).
  - Uses mean VORP within a round bucket - a noisy model (this one has some
    high-variance rows, see injury-conflation caveat) means individual
    round means can bounce around; look at the SHAPE across rounds, not any
    single round in isolation.
  - Does not yet account for roster construction (how many of each position
    you still need) - that's the natural next iteration once this basic
    version is validated.

Usage: venv/bin/python3 python/buildNflPositionalScarcity.py
Output: outputs/sports/nfl/fantasy/positional_scarcity.csv
        outputs/sports/nfl/fantasy/positional_scarcity.png
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
ADP_CAP = 200
N_TEAMS = 12
POSITIONS = ["QB", "RB", "WR", "TE"]
POS_COLORS = {"QB": "#7B4CE0", "RB": "#2E9E4E", "WR": "#2E7BE0", "TE": "#E0A02E"}


def main():
    df = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    df = df[(df["adp_overall"] <= ADP_CAP) & (df["position"].isin(POSITIONS))].copy()
    df["round"] = np.ceil(df["adp_overall"] / N_TEAMS).astype(int)

    max_round = df["round"].max()
    round_means = (
        df.groupby(["position", "round"])["vorp"].mean()
        .unstack("position")
        .reindex(range(1, max_round + 1))
    )

    print("=== Mean VORP by round bucket and position ===")
    print(round_means.round(1).to_string())

    # round-over-round drop: positive = value lost by waiting one more round
    drop = round_means.diff().mul(-1)
    print("\n=== Round-over-round VORP drop (waiting-one-round cost) ===")
    print(drop.round(1).to_string())

    # Per round, which position has the steepest upcoming cliff (biggest
    # drop from THIS round to NEXT round) -> what you lose most by skipping.
    upcoming_cliff = drop.shift(-1)  # cliff between round R and R+1, indexed at R
    print("\n=== Steepest upcoming cliff per round (position to prioritize) ===")
    priority = upcoming_cliff.idxmax(axis=1, skipna=True)
    for rnd in range(1, max_round):
        row = upcoming_cliff.loc[rnd].dropna()
        if row.empty:
            continue
        best_pos = row.idxmax()
        print(f"  Round {rnd}: prioritize {best_pos} "
              f"(waiting to round {rnd+1} costs ~{row[best_pos]:.1f} VORP pts) "
              f"| all: {row.round(1).to_dict()}")

    out_csv = os.path.join(OUT_DIR, "positional_scarcity.csv")
    round_means.to_csv(out_csv)
    print(f"\nWrote: {out_csv}")

    # ---- plot: VORP decay curve per position across rounds ----
    fig, ax = plt.subplots(figsize=(12, 7))
    for pos in POSITIONS:
        ax.plot(round_means.index, round_means[pos], marker="o", label=pos,
                color=POS_COLORS[pos], linewidth=2)
    ax.set_xlabel("Draft Round (12-team, ADP-based)")
    ax.set_ylabel("Mean VORP of players available in that round")
    ax.set_title("Room 40 - Positional Value Decay by Round\n"
                  "(steeper slope = scarcer position = draft it sooner; flat = safe to wait)")
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.legend()
    ax.grid(alpha=0.3)
    out_png = os.path.join(OUT_DIR, "positional_scarcity.png")
    plt.tight_layout()
    plt.savefig(out_png, dpi=130)
    plt.close()
    print(f"Wrote: {out_png}")


if __name__ == "__main__":
    main()
