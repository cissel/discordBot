#!/usr/bin/env python3
"""
buildNflDraftStrategySim.py

Phase 4, step 4: roster-aware positional strategy simulator - answers "what
order should I actually draft positions in" by SIMULATING full 12-team snake
drafts under different named strategies and scoring the resulting STARTING
LINEUP's total VORP (not just "value drafted", which overcounts bench
depth you can't start).

This supersedes the static round-based scarcity chart (positional_scarcity.py)
by adding the piece that was missing: YOUR OWN ROSTER STATE. A position's
"scarcity" only matters to you relative to how many starter slots you still
need to fill - once your 2 RB slots are locked in, more RB is just bench
depth and should be valued accordingly.

METHOD:
  - Field (11 opponents) draft "best ADP available" each pick - a simple,
    standard proxy for market behavior. Not adaptive/strategic, but a
    reasonable baseline (this is the same assumption most public ADP-based
    mocks make).
  - User follows one of several named STRATEGIES (position-priority rules).
  - After a 15-round snake draft, score the user's team as the sum of VORP
    of their OPTIMAL starting lineup (best QB, best 2 RB, best 2 WR, best
    TE, best remaining RB/WR/TE for FLEX) - bench players contribute zero,
    same as they would on your actual scoreboard.
  - Repeat across all 12 draft slots (since positional value differs a lot
    between picking 1st vs 12th) and average per strategy.

STRATEGIES MODELED (first pass - simple, named, extensible):
  - zero_rb:    no RB in rounds 1-4, prioritize WR/TE/QB early, backfill RB late
  - hero_rb:    exactly one RB in round 1 if elite value present, then zero_rb after
  - robust_rb:  RB in rounds 1-2 if any RB available, else BPA
  - balanced:   fill starter slots (QB,RB,RB,WR,WR,TE) via best-available-at-need,
                then BPA for flex/bench
  - bpa:        pure best-player-available every pick regardless of position,
                capped at reasonable position limits so it doesn't hoard one
                position past usefulness

LIMITATIONS (be upfront - this is a "first pass", not a closed-form solver):
  - Field model is non-adaptive (always ADP-best), doesn't react to runs.
  - No trade logic, no injury news, no bye-week stacking considerations.
  - Only QB/RB/WR/TE modeled (matches VORP board scope) - no K/DEF rounds.
  - Single random-seed-free simulation per (strategy, slot) - deterministic
    given the ADP field assumption, not a Monte Carlo distribution. A future
    iteration could add opponent randomness for a confidence interval.

Usage: venv/bin/python3 python/buildNflDraftStrategySim.py
Output: outputs/sports/nfl/fantasy/draft_strategy_sim.csv
        outputs/sports/nfl/fantasy/draft_strategy_sim.png
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
ADP_CAP = 200
N_TEAMS = 12
N_ROUNDS = 15
STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
# Sane position caps so BPA/robust_rb strategies don't hoard one position
# into meaninglessness (mirrors realistic human draft behavior).
POSITION_CAPS = {"QB": 3, "RB": 7, "WR": 7, "TE": 3}

# BENCH UTILITY DISCOUNT: once a candidate's marginal starting-lineup gain is
# ~0 (it can't start/flex), raw VORP is NOT a fair tiebreaker across positions
# - a backup QB's VORP looks big purely because the QB replacement baseline
# is low (few teams roster 3+ QBs), not because a QB3 has any real value in
# a 1-QB league (streaming a replacement-level QB off waivers is trivial).
# RB/WR/TE bench DOES have real value (bye-week fill-in, injury insurance,
# handcuffs), so it should win zero-gain ties over an extra QB. These are
# rough, named-consensus multipliers (not fit to data) - flag as a modeling
# choice, not empirical fact, if asked.
BENCH_VALUE_DISCOUNT = {"QB": 0.05, "RB": 0.5, "WR": 0.5, "TE": 0.3}


def tiebreak_score(position, vorp):
    """Discounted bench-utility score used ONLY to break ties among candidates
    with equal (usually zero) marginal starting-lineup gain."""
    return BENCH_VALUE_DISCOUNT.get(position, 0.3) * vorp


def snake_order(n_teams, n_rounds):
    """Returns pick order as a list of team indices (0-indexed), snake style."""
    order = []
    for rnd in range(n_rounds):
        teams = list(range(n_teams))
        if rnd % 2 == 1:
            teams = teams[::-1]
        order.extend(teams)
    return order


def score_lineup(team_players, pool):
    """Given a list of player rows drafted by one team, compute the OPTIMAL
    starting lineup's total VORP (bench players contribute 0)."""
    df = pd.DataFrame(team_players)
    if df.empty:
        return 0.0
    used_idx = set()
    total = 0.0
    for pos, n in STARTERS.items():
        pos_df = df[df["position"] == pos].sort_values("vorp", ascending=False)
        for _, row in pos_df.head(n).iterrows():
            total += row["vorp"]
            used_idx.add(row["full_name"])
    # FLEX: best remaining RB/WR/TE not already used as a starter
    flex_pool = df[df["position"].isin(FLEX_ELIGIBLE) & ~df["full_name"].isin(used_idx)]
    if not flex_pool.empty:
        best_flex = flex_pool.sort_values("vorp", ascending=False).iloc[0]
        total += best_flex["vorp"]
    return total


class Strategy:
    """Position-priority rule set. `pick()` returns which position to target
    this round (or None for pure BPA), given the user's roster-so-far."""
    name = "base"

    def pick_position(self, round_num, roster_counts):
        return None  # None = BPA


class ZeroRB(Strategy):
    name = "zero_rb"

    def pick_position(self, round_num, roster_counts):
        if round_num <= 4 and roster_counts.get("RB", 0) == 0:
            return {"QB", "WR", "TE"}
        return None


class HeroRB(Strategy):
    name = "hero_rb"

    def pick_position(self, round_num, roster_counts):
        if round_num == 1:
            return {"RB"}
        if 2 <= round_num <= 4 and roster_counts.get("RB", 0) <= 1:
            return {"QB", "WR", "TE"}
        return None


class RobustRB(Strategy):
    name = "robust_rb"

    def pick_position(self, round_num, roster_counts):
        if round_num <= 2 and roster_counts.get("RB", 0) < 2:
            return {"RB"}
        return None


class Balanced(Strategy):
    name = "balanced"
    FILL_ORDER = ["RB", "WR", "RB", "WR", "TE", "QB"]

    def pick_position(self, round_num, roster_counts):
        for pos in self.FILL_ORDER:
            needed = STARTERS.get(pos, 0)
            have = roster_counts.get(pos, 0)
            if have < needed:
                return {pos}
        return None


class BPA(Strategy):
    name = "bpa"

    def pick_position(self, round_num, roster_counts):
        return None


STRATEGIES = [ZeroRB(), HeroRB(), RobustRB(), Balanced(), BPA()]


def simulate_draft(pool_df, strategy, user_slot):
    """pool_df: full draftable player pool sorted by adp_overall ascending.
    user_slot: 0-indexed draft position (0 = picks first).
    Field (everyone else) always takes best-ADP-available. User evaluates
    candidates by MARGINAL starting-lineup value (not raw VORP) so a 2nd/3rd
    QB - which can't start and contributes 0 - is correctly deprioritized
    even though its raw VORP looks big. Strategy still filters WHICH
    positions are eligible each round; marginal value picks the best among
    those. Returns the user's optimal starting lineup VORP."""
    available = pool_df.copy().reset_index(drop=True)
    order = snake_order(N_TEAMS, N_ROUNDS)

    rosters = {t: [] for t in range(N_TEAMS)}
    roster_counts = {t: {} for t in range(N_TEAMS)}

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

            # Marginal-value pick: among the top few VORP candidates per
            # eligible position, choose whichever adds the most to the
            # OPTIMAL STARTING LINEUP (bench adds ~0, so a 3rd QB naturally
            # loses to a marginal RB/WR/TE that can actually start/flex).
            current_roster = rosters[team]
            baseline = score_lineup(current_roster, pool_df)
            shortlist = (candidates.sort_values("vorp", ascending=False)
                         .groupby("position", group_keys=False).head(2))
            best_row, best_gain, best_tiebreak = None, -1e18, -1e18
            for _, cand_row in shortlist.iterrows():
                trial_roster = current_roster + [cand_row.to_dict()]
                gain = score_lineup(trial_roster, pool_df) - baseline
                tiebreak = tiebreak_score(cand_row["position"], cand_row["vorp"])
                # Primary: marginal starting-lineup gain. Secondary (only
                # when gain is tied, e.g. both candidates are pure bench):
                # discounted bench-utility score, NOT raw VORP - see
                # BENCH_VALUE_DISCOUNT comment above for why raw VORP is an
                # unfair cross-position tiebreaker once nobody can start.
                if (gain, tiebreak) > (best_gain, best_tiebreak):
                    best_gain, best_tiebreak, best_row = gain, tiebreak, cand_row
            pick_row = best_row
        else:
            # field: best-ADP-available, respecting the same sane position caps
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

    return score_lineup(rosters[user_slot], pool_df)


def main():
    df = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    df = df[(df["adp_overall"] <= ADP_CAP) &
            (df["position"].isin(list(STARTERS.keys())))].copy()
    df = df.dropna(subset=["vorp", "adp_overall"])

    results = []
    for strategy in STRATEGIES:
        for user_slot in range(N_TEAMS):
            score = simulate_draft(df, strategy, user_slot)
            results.append({"strategy": strategy.name, "draft_slot": user_slot + 1,
                             "starting_lineup_vorp": score})

    results_df = pd.DataFrame(results)
    summary = results_df.groupby("strategy")["starting_lineup_vorp"].agg(["mean", "std", "min", "max"])
    summary = summary.sort_values("mean", ascending=False)

    print("=== Strategy comparison (avg starting-lineup VORP across all 12 draft slots) ===")
    print(summary.round(1).to_string())

    out_csv = os.path.join(OUT_DIR, "draft_strategy_sim.csv")
    results_df.to_csv(out_csv, index=False)
    print(f"\nWrote: {out_csv}")

    # ---- plot: per-slot performance by strategy ----
    fig, ax = plt.subplots(figsize=(12, 7))
    pivot = results_df.pivot(index="draft_slot", columns="strategy", values="starting_lineup_vorp")
    pivot = pivot[summary.index]  # order columns by overall rank
    pivot.plot(kind="bar", ax=ax, width=0.8)
    ax.set_xlabel("Draft Slot (1 = picks first)")
    ax.set_ylabel("Starting Lineup VORP")
    ax.set_title("Room 40 - Draft Strategy Comparison by Draft Slot\n"
                  "(simulated: field drafts best-ADP-available, you follow each named strategy)")
    ax.legend(title="Strategy")
    ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    out_png = os.path.join(OUT_DIR, "draft_strategy_sim.png")
    plt.savefig(out_png, dpi=130)
    plt.close()
    print(f"Wrote: {out_png}")


if __name__ == "__main__":
    main()
