#!/usr/bin/env python3
"""
buildNflBigBoard.py

Phase 4, step 7: a flat, POSITION-AGNOSTIC overall draft order - one ranked
list you can just go down pick by pick, instead of the position-columned
tiered cheat sheet (buildNflDraftBoard.py / draft_board_2026.png). This is
the "just tell me who to take next regardless of position" artifact.

METHOD:
  - Ranked purely by VORP descending across ALL positions (not per-position
    tiers) - VORP is already replacement-level-adjusted per position, so it's
    the correct apples-to-apples currency for a cross-position ranking
    (unlike raw projected points, which would just reorder by QB > everyone
    since QBs score the most gross points).
  - Tiers still shown (via 1D KMeans on VORP, same method as the positional
    board) so you can see "there's a real cliff after pick 14" even in a
    single flat list, but tiers span positions here instead of resetting
    per-position.

Usage: venv/bin/python3 python/buildNflBigBoard.py
Output: outputs/sports/nfl/fantasy/big_board_2026.csv
        outputs/sports/nfl/fantasy/big_board_2026.png
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from sklearn.cluster import KMeans

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
ADP_CAP = 200  # matches buildNflVorp.py's draftable-range definition
POSITIONS = ["QB", "RB", "WR", "TE"]
POS_COLORS = {"QB": "#7B4CE0", "RB": "#2E9E4E", "WR": "#2E7BE0", "TE": "#E0A02E"}
N_TIERS_TARGET = 16  # overall board is longer than one position, allow more tiers
N_ROWS = 150  # ~12.5 rounds worth - covers a full draft plus a buffer


def assign_tiers(df, n_tiers):
    n = len(df)
    k = min(n_tiers, max(1, n // 4))
    if k <= 1 or n < 3:
        return pd.Series([1] * n, index=df.index)
    x = df["vorp"].values.reshape(-1, 1)
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(x)
    cluster_means = pd.Series(x.flatten(), index=df.index).groupby(km.labels_).mean()
    rank_map = {cluster: rank + 1 for rank, cluster in
                enumerate(cluster_means.sort_values(ascending=False).index)}
    return pd.Series([rank_map[c] for c in km.labels_], index=df.index)


def main():
    df = pd.read_csv(os.path.join(OUT_DIR, "vorp_2026.csv"))
    df = df[df["adp_overall"] <= ADP_CAP].copy()
    df = df[df["position"].isin(POSITIONS)].copy()
    df = df.sort_values("vorp", ascending=False).head(N_ROWS).reset_index(drop=True)

    df["overall_rank"] = df.index + 1
    df["tier"] = assign_tiers(df, N_TIERS_TARGET)

    out_csv = os.path.join(OUT_DIR, "big_board_2026.csv")
    df.to_csv(out_csv, index=False)
    print(f"Wrote: {out_csv} ({len(df)} rows)")

    print("\n=== Tier counts (overall, cross-position) ===")
    print(df.groupby("tier").size().to_string())

    print("\n=== Top 24 overall (a full 2-round snake draft's worth) ===")
    print(df.head(24)[["overall_rank", "full_name", "position", "team",
                        "adp_overall", "vorp", "value_gap"]].to_string(index=False))

    # ---- printable big board: single flat column, tier breaks marked ----
    n_show = min(len(df), N_ROWS)
    fig_h = max(20, n_show * 0.16)
    fig, ax = plt.subplots(figsize=(9, fig_h))
    ax.axis("off")
    ax.set_title("Room 40 - 2026 Big Board (Overall Draft Order, Cross-Position)\n"
                  "Rookies flagged (R) | value_gap: + = ADP undervalues, - = ADP overvalues",
                  fontsize=15, fontweight="bold")

    y = 1.0
    line_h = 1.0 / (n_show + df["tier"].nunique() + 2)
    prev_tier = None
    for _, row in df.head(n_show).iterrows():
        if row["tier"] != prev_tier:
            y -= line_h * 0.4
            ax.text(0.0, y, f"— Tier {int(row['tier'])} —", fontsize=10,
                    fontweight="bold", color="gray", transform=ax.transAxes)
            y -= line_h
            prev_tier = row["tier"]
        rookie_flag = " (R)" if row.get("player_type") == "rookie" else ""
        gap = row["value_gap"]
        gap_str = f"+{gap:.0f}" if gap > 0 else f"{gap:.0f}"
        gap_color = "green" if gap > 15 else ("red" if gap < -15 else "black")
        label = (f"{int(row['overall_rank']):>3}. {row['full_name']}{rookie_flag} "
                 f"({row['position']}, {row['team']}) — ADP {row['adp_overall']:.0f}, {gap_str}")
        ax.text(0.02, y, label, fontsize=9, transform=ax.transAxes,
                color=gap_color, fontweight="bold" if gap_color != "black" else "normal")
        # small position color tag
        ax.text(0.0, y, "\u2588", fontsize=9, transform=ax.transAxes,
                color=POS_COLORS.get(row["position"], "black"))
        y -= line_h

    fig.text(0.5, 0.005,
              "CAVEAT: big red value_gap on players who missed significant 2025 time to injury "
              "(e.g. Burrow, Purdy) is a known model artifact, not a real fade - see "
              "injury_shortened_2025 flag in big_board_2026.csv before trusting those.",
              ha="center", fontsize=8, style="italic", color="dimgray")

    plt.tight_layout(rect=[0, 0.01, 1, 0.97])
    out_png = os.path.join(OUT_DIR, "big_board_2026.png")
    plt.savefig(out_png, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"Wrote: {out_png}")


if __name__ == "__main__":
    main()
