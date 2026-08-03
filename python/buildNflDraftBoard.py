#!/usr/bin/env python3
"""
buildNflDraftBoard.py

Phase 4, step 1: turns the Phase 3 VORP/ADP board into an actual draft-day
artifact - a tiered cheat sheet. Tiers are derived per-position via 1D
KMeans clustering on VORP (not fixed round-number cutoffs), so a tier break
reflects a real gap in projected value rather than an arbitrary "top 12".

Output:
  outputs/sports/nfl/fantasy/draft_board_2026.csv   (full board, tier column,
                                                      is_breakout flag)
  outputs/sports/nfl/fantasy/draft_board_2026.png   (printable cheat sheet,
                                                      one column per position,
                                                      breakout candidates
                                                      starred)

Breakout star flag (Phase 5): merges breakout_candidates_2026.csv (built by
buildNflBreakoutScore.py - see that file for the composite scoring logic)
onto the board by normalized player name. A candidate whose VORP-model
projection places them beyond ADP_CAP is still force-included as a
"Breakout Watch" row (tier 99) rather than silently dropped - the whole
point of a breakout candidate is often that the market (ADP) hasn't caught
up yet, so cutting them at the same ADP line that defines "draftable" would
hide exactly the players this feature exists to surface. 2 of 20 candidates
as of the 2026-08 build (Spencer Rattler, Colson Yankoff) have no Sleeper
ADP entry at all and can't be placed on ANY ADP-based board - printed as a
warning at build time rather than silently vanishing.

Usage: venv/bin/python3 python/buildNflDraftBoard.py
"""
import os
import re
import unicodedata

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
ADP_CAP = 200  # matches buildNflVorp.py's draftable-range definition
BREAKOUT_WATCH_TIER = 99  # sentinel tier for breakout candidates beyond ADP_CAP

POSITIONS = ["QB", "RB", "WR", "TE"]
POS_COLORS = {"QB": "#7B4CE0", "RB": "#2E9E4E", "WR": "#2E7BE0", "TE": "#E0A02E"}

N_TIERS_TARGET = {"QB": 6, "RB": 8, "WR": 8, "TE": 6}  # upper bound; shrinks if too few players

SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    """Same normalization convention as buildNfl2026Projections.py's norm_name -
    strip accents/suffixes/punctuation so name-based joins across sources
    (Sleeper ADP vs nflverse-derived breakout scores) aren't broken by
    formatting differences (Jr./Sr./accented letters)."""
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIX_RE.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_breakout_candidates():
    """Load breakout_candidates_2026.csv if it exists. Returns an empty
    DataFrame (no crash) if buildNflBreakoutScore.py hasn't been run yet -
    the star feature degrades gracefully rather than blocking the board."""
    path = os.path.join(OUT_DIR, "breakout_candidates_2026.csv")
    if not os.path.exists(path):
        print("NOTE: breakout_candidates_2026.csv not found - run "
              "buildNflBreakoutScore.py first for breakout stars. "
              "Building board without them.")
        return pd.DataFrame(columns=["norm_name", "position", "breakout_z"])
    cand = pd.read_csv(path)
    cand["norm_name"] = cand["player_display_name"].map(norm_name)
    return cand[["norm_name", "position", "player_display_name", "breakout_z"]]


def assign_tiers(pos_df, n_tiers):
    """1D KMeans on VORP within a position. Returns tier labels 1..k where
    tier 1 = highest mean VORP. Falls back to a single tier if too few rows
    to cluster meaningfully."""
    n = len(pos_df)
    k = min(n_tiers, max(1, n // 2))
    if k <= 1 or n < 3:
        return pd.Series([1] * n, index=pos_df.index)

    x = pos_df["vorp"].values.reshape(-1, 1)
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(x)
    # order clusters by mean VORP descending -> tier 1 = best
    cluster_means = pd.Series(x.flatten(), index=pos_df.index).groupby(km.labels_).mean()
    rank_map = {cluster: rank + 1 for rank, cluster in
                enumerate(cluster_means.sort_values(ascending=False).index)}
    return pd.Series([rank_map[c] for c in km.labels_], index=pos_df.index)


def main():
    full = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    full = full[full["position"].isin(POSITIONS)].copy()
    full["norm_name"] = full["full_name"].map(norm_name)

    candidates = load_breakout_candidates()
    breakout_keys = set(zip(candidates["norm_name"], candidates["position"]))
    full["is_breakout"] = full.apply(
        lambda r: (r["norm_name"], r["position"]) in breakout_keys, axis=1
    )

    # Candidates flagged by buildNflBreakoutScore.py but never even matched
    # into vorp_2026.csv (e.g. no Sleeper ADP entry to begin with - can't be
    # placed on ANY ADP-based board) - surface this rather than silently
    # dropping them off the whole feature.
    matched_keys = set(zip(full["norm_name"], full["position"]))
    unmatched = candidates[
        ~candidates.apply(lambda r: (r["norm_name"], r["position"]) in matched_keys, axis=1)
    ]
    if len(unmatched):
        print(f"WARNING: {len(unmatched)} breakout candidate(s) have no ADP/VORP "
              f"entry at all and cannot appear on the board: "
              f"{unmatched['player_display_name'].tolist()}")

    df = full[full["adp_overall"] <= ADP_CAP].copy()

    # Breakout candidates whose VORP-model projection put them beyond ADP_CAP
    # would otherwise vanish - force them onto the board as a separate
    # "Breakout Watch" tier instead. The whole point of this feature is often
    # players the market (ADP) hasn't caught up to yet.
    beyond_cap_breakouts = full[(full["adp_overall"] > ADP_CAP) & full["is_breakout"]].copy()
    if len(beyond_cap_breakouts):
        print(f"Force-including {len(beyond_cap_breakouts)} breakout candidate(s) "
              f"beyond ADP {ADP_CAP} as Breakout Watch: "
              f"{beyond_cap_breakouts['full_name'].tolist()}")

    tier_frames = []
    for pos in POSITIONS:
        pos_df = df[df["position"] == pos].sort_values("vorp", ascending=False).copy()
        pos_df["tier"] = assign_tiers(pos_df, N_TIERS_TARGET[pos])
        tier_frames.append(pos_df)
        pos_watch = beyond_cap_breakouts[beyond_cap_breakouts["position"] == pos].copy()
        if len(pos_watch):
            pos_watch["tier"] = BREAKOUT_WATCH_TIER
            tier_frames.append(pos_watch)

    board = pd.concat(tier_frames).sort_values(
        ["position", "tier", "vorp"], ascending=[True, True, False]
    )
    board = board.drop(columns=["norm_name"])

    print("=== Tier counts by position ===")
    print(board.groupby(["position", "tier"]).size().unstack(fill_value=0))
    print(f"\nBreakout candidates on board: {board['is_breakout'].sum()}")

    out_csv = os.path.join(OUT_DIR, "draft_board_2026.csv")
    board.to_csv(out_csv, index=False)
    print(f"\nWrote: {out_csv} ({len(board)} rows)")

    # ---- printable cheat sheet: one column per position, grouped by tier ----
    fig, axes = plt.subplots(1, len(POSITIONS), figsize=(22, 30))
    fig.suptitle("Room 40 - 2026 Draft Board (Tiered by VORP)\n"
                  "Rookies flagged (R) | \u2605 = breakout candidate (2nd/3rd-yr, see /nfl cheatsheet caveat) | "
                  "value_gap: + = ADP undervalues, - = ADP overvalues",
                  fontsize=15, fontweight="bold", y=0.995)

    for ax, pos in zip(axes, POSITIONS):
        pos_df = board[board["position"] == pos]
        ax.set_title(pos, fontsize=18, fontweight="bold", color=POS_COLORS[pos])
        ax.axis("off")
        y = 1.0
        line_h = 1.0 / max(len(pos_df) + pos_df["tier"].nunique() + 2, 1)
        prev_tier = None
        for _, row in pos_df.iterrows():
            if row["tier"] != prev_tier:
                y -= line_h * 0.4
                tier_label = "Breakout Watch (beyond ADP cap)" if row["tier"] == BREAKOUT_WATCH_TIER \
                    else f"Tier {int(row['tier'])}"
                ax.text(0.0, y, f"\u2014 {tier_label} \u2014", fontsize=10,
                        fontweight="bold", color="gray", transform=ax.transAxes)
                y -= line_h
                prev_tier = row["tier"]
            rookie_flag = " (R)" if row.get("player_type") == "rookie" else ""
            star = "\u2605 " if row.get("is_breakout") else ""
            gap = row["value_gap"]
            gap_str = f"+{gap:.0f}" if gap > 0 else f"{gap:.0f}"
            gap_color = "green" if gap > 15 else ("red" if gap < -15 else "black")
            label = f"{star}{row['full_name']}{rookie_flag}  (ADP {row['adp_overall']:.0f}, {gap_str})"
            ax.text(0.02, y, label, fontsize=9, transform=ax.transAxes, color=gap_color,
                    fontweight="bold" if star else "normal")
            y -= line_h

    fig.text(0.5, 0.005,
              "CAVEAT: big red value_gap on players who missed significant 2025 time to injury "
              "(e.g. Burrow, Purdy) is a known model artifact, not a real fade - see "
              "injury_shortened_2025 flag in draft_board_2026.csv before trusting those. "
              "\u2605 breakout candidates are a composite score (efficiency/usage-trend/situation/draft-capital/"
              "aging-slope, shrunk for small samples) - see breakout_scored_pool_2026.csv for the full breakdown.",
              ha="center", fontsize=8.5, style="italic", color="dimgray")

    plt.tight_layout(rect=[0, 0.01, 1, 0.97])
    out_png = os.path.join(OUT_DIR, "draft_board_2026.png")
    plt.savefig(out_png, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"Wrote: {out_png}")


if __name__ == "__main__":
    main()
