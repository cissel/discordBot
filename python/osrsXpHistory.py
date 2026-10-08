#!/usr/bin/env python3
"""
osrsXpHistory.py

Plots XP-over-time for the OSRS crew (or a single player) from the
long-running snapshot log written daily by osrsLevelHistory.py
(outputs/osrs/level_history.csv, cron job osrs-level-history-daily).

Backs the /osrs history Discord command. Not auto-posted anywhere -
on-demand only.

Usage: python3 osrsXpHistory.py [player] [skill]
  player = RSN (required for "allskills" mode), or "all" (default) to
           plot every crew member on one chart for a single skill
  skill  = any OSRS skill name, defaults to "total", OR "allskills" to
           plot every skill's level over time for one player on a single
           chart (one line per skill, all 23 skills)

Writes:
  outputs/osrs/xp_history_<player>_<skill>.png
  outputs/osrs/xp_history_<player>_allskills.png   (allskills mode)

Navy house theme (matches buildNflDraftBoard.py / nflDraftDashboard.py):
  BG=#02233F, GRID=#274066, TXT=white for ALL text incl. captions.
"""
import sys
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

HISTORY = Path("~/discordBot/outputs/osrs/level_history.csv").expanduser()
OUT_DIR = Path("~/discordBot/outputs/osrs").expanduser()
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Navy house theme
BG = "#02233F"
GRID = "#274066"
TXT = "white"

# Distinct line colors per player (cycled if crew grows beyond this list)
PLAYER_COLORS = [
    "#2E7BE0", "#2E9E4E", "#E0A02E", "#7B4CE0", "#E04F4F",
    "#4FE0D8", "#E04FAF", "#B0E04F", "#9E9E9E",
]

# Fixed skill order (excludes "total" - the allskills chart is a
# per-skill level comparison, and total lives on a totally different scale)
SKILL_ORDER = [
    "attack", "defence", "strength", "hitpoints", "ranged",
    "prayer", "magic", "cooking", "woodcutting", "fletching",
    "fishing", "firemaking", "crafting", "smithing", "mining",
    "herblore", "agility", "thieving", "slayer", "farming",
    "runecraft", "hunter", "construction", "sailing",
]

ALLSKILLS_ALIASES = {"allskills", "all_skills", "all-skills", "skills"}

# OSRS xp needed per level grows exponentially (level 99 needs 13.0M xp vs
# level 50's ~101k), so on a LINEAR y-axis every skill looks completely flat
# for its first year or more of real, meaningful progress - not a bug, that's
# just what the raw numbers look like next to the later exponential blowup.
# Log y-axis fixes this: equal *multiplicative* (percentage) xp gains take up
# equal vertical space regardless of absolute level, so early-game grinding
# shows up as clearly as late-game grinding.
# log(0) is undefined, and level 1 in a skill is legitimately 0 xp (e.g. a
# freshly-started skill like Sailing) - so zero/blank xp values are floored
# to FLOOR_XP purely for plotting. Real values are never below the lvl-2
# threshold (83 xp), so a floor of 10 sits clearly below all genuine data and
# just gives level-1 starting points a place to sit on a log axis.
FLOOR_XP = 10


def _format_date_axis(ax, dates):
    """X-axis date ticks that adapt to span: full history now runs Sep 2024
    -> present (over 2 years), so monthly ticks (the old behavior) were
    cramped and unreadable. Show one tick per YEAR when the range covers
    more than ~1 year; fall back to month/day ticks for short-range plots
    (e.g. a skill with only a few months of data) so those don't end up
    with just one or two ticks."""
    span_days = (dates.max() - dates.min()).days if len(dates) else 0
    if span_days > 400:
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=(4, 7, 10)))
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    else:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def _apply_log_yaxis(ax, values):
    """Log-scale y-axis for xp. A single tick strategy doesn't work across
    all our charts: the allskills chart spans ~5 decades of xp
    (tens up to tens of millions) where dense "auto" sub-ticks (2x,3x,4x...
    at every decade) overlap into unreadable mush; the single-skill/total
    charts often span under ONE decade (e.g. 22M-35M) where plain
    power-of-ten ticks (10M, 100M) would place zero ticks in view at all.
    Decide per-plot based on how many decades the actual data spans."""
    ax.set_yscale("log")
    finite = values[np.isfinite(values) & (values > 0)]
    span_decades = (np.log10(finite.max()) - np.log10(finite.min())) if len(finite) else 0
    if span_decades > 2:
        # wide span: label only powers of ten to avoid overlapping text
        ax.yaxis.set_major_locator(mticker.LogLocator(base=10, numticks=12))
    else:
        # narrow span: power-of-ten ticks alone would show nothing, so
        # allow a few sensible in-decade values too (2x, 5x, etc.)
        ax.yaxis.set_major_locator(mticker.LogLocator(base=10, subs="auto", numticks=12))
    ax.yaxis.set_minor_locator(
        mticker.LogLocator(base=10, subs=np.arange(2, 10) * 0.1, numticks=12)
    )
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))


def load_history():
    if not HISTORY.exists():
        return None
    df = pd.read_csv(HISTORY)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _skill_colors(n):
    """Generate n visually distinct colors by combining tab20/tab20b/tab20c."""
    cmaps = [plt.get_cmap("tab20"), plt.get_cmap("tab20b"), plt.get_cmap("tab20c")]
    colors = []
    for cmap in cmaps:
        colors.extend([cmap(i) for i in range(cmap.N)])
    if n > len(colors):
        colors = colors * (n // len(colors) + 1)
    return colors[:n]


def plot_allskills(df, player_arg):
    """One line per skill (level over time) for one player, all on one chart."""
    if player_arg.lower() == "all":
        print("[osrsXpHistory] allskills mode needs a specific player, not 'all' "
              "(23 skills x whole crew would be unreadable)")
        sys.exit(1)

    pdf_all = df[df["player"].str.lower() == player_arg.lower()].copy()
    if pdf_all.empty:
        print(f"[osrsXpHistory] no rows for player '{player_arg}'")
        sys.exit(1)

    real_player = pdf_all["player"].iloc[0]
    skills_present = [s for s in SKILL_ORDER if s in set(pdf_all["skill"].unique())]
    if not skills_present:
        print(f"[osrsXpHistory] no per-skill rows for player '{player_arg}'")
        sys.exit(1)

    n_days = pdf_all["date"].nunique()
    colors = _skill_colors(len(skills_present))

    fig, ax = plt.subplots(figsize=(13, 7.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for i, skill in enumerate(skills_present):
        sdf = pdf_all[pdf_all["skill"] == skill].sort_values("date")
        if sdf.empty:
            continue
        marker = "o" if len(sdf) <= 30 else None
        plot_xp = sdf["xp"].clip(lower=FLOOR_XP)
        ax.plot(sdf["date"], plot_xp, marker=marker, linewidth=1.8,
                 color=colors[i], label=skill.title(), markersize=4)

    ax.set_title(f"OSRS Skill XP Over Time - {real_player}",
                 fontsize=15, fontweight="bold", color=TXT)
    ax.set_xlabel("Date", color=TXT)
    ax.set_ylabel("XP (log scale)", color=TXT)

    _format_date_axis(ax, pdf_all["date"])
    fig.autofmt_xdate(rotation=30)

    ax.tick_params(colors=TXT)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, which="major", color=GRID, linewidth=0.6, alpha=0.7)
    ax.grid(True, which="minor", color=GRID, linewidth=0.3, alpha=0.3)
    _apply_log_yaxis(ax, pdf_all["xp"].clip(lower=FLOOR_XP).to_numpy())

    # 23 possible lines - legend goes outside on the right, small font, 1 col
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
              facecolor="#0a2840", edgecolor=GRID, labelcolor=TXT, fontsize=8,
              ncol=1)

    caption = f"source: crew hiscores snapshot (daily cron)  ·  {n_days} day(s) logged"
    if n_days < 3:
        caption += "  ·  early data - trend will sharpen as more days log"
    fig.text(0.42, 0.01, caption, ha="center", fontsize=8.5, style="italic", color=TXT)

    fig.tight_layout(rect=[0, 0.03, 0.82, 1])

    safe_player = real_player.lower().replace(" ", "_")
    out_path = OUT_DIR / f"xp_history_{safe_player}_allskills.png"
    fig.savefig(out_path, facecolor=BG, dpi=150)
    plt.close(fig)

    print(f"[osrsXpHistory] wrote {out_path} ({len(skills_present)} skill(s), {n_days} day(s))")
    print(f"PATH:{out_path}")


def plot_single_skill(df, player_arg, skill_arg):
    skill_df = df[df["skill"] == skill_arg].copy()
    if skill_df.empty:
        print(f"[osrsXpHistory] no rows for skill '{skill_arg}'")
        sys.exit(1)

    if player_arg.lower() != "all":
        # case-insensitive RSN match
        match = skill_df["player"].str.lower() == player_arg.lower()
        if not match.any():
            print(f"[osrsXpHistory] no rows for player '{player_arg}' / skill '{skill_arg}'")
            sys.exit(1)
        skill_df = skill_df[match]
        players = sorted(skill_df["player"].unique())
    else:
        players = sorted(skill_df["player"].unique())

    n_days = skill_df["date"].nunique()

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for i, p in enumerate(players):
        pdf = skill_df[skill_df["player"] == p].sort_values("date")
        if len(pdf) < 1:
            continue
        color = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        marker = "o" if len(pdf) <= 30 else None
        plot_xp = pdf["xp"].clip(lower=FLOOR_XP)
        ax.plot(pdf["date"], plot_xp, marker=marker, linewidth=2,
                 color=color, label=p, markersize=5)

    skill_label = "Total Level" if skill_arg == "total" else skill_arg.title()
    title_scope = "Crew" if player_arg.lower() == "all" else players[0]
    ax.set_title(f"OSRS {skill_label} XP History - {title_scope}",
                 fontsize=15, fontweight="bold", color=TXT)
    ax.set_xlabel("Date", color=TXT)
    ax.set_ylabel("XP (log scale)", color=TXT)

    _format_date_axis(ax, skill_df["date"])
    fig.autofmt_xdate(rotation=30)

    ax.tick_params(colors=TXT)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, which="major", color=GRID, linewidth=0.6, alpha=0.7)
    ax.grid(True, which="minor", color=GRID, linewidth=0.3, alpha=0.3)
    _apply_log_yaxis(ax, skill_df["xp"].clip(lower=FLOOR_XP).to_numpy())

    ax.legend(loc="upper left", facecolor="#0a2840", edgecolor=GRID,
              labelcolor=TXT, fontsize=9)

    caption = f"source: crew hiscores snapshot (daily cron)  ·  {n_days} day(s) logged"
    if n_days < 3:
        caption += "  ·  early data - trend will sharpen as more days log"
    fig.text(0.5, 0.01, caption, ha="center", fontsize=8.5, style="italic", color=TXT)

    fig.tight_layout(rect=[0, 0.03, 1, 1])

    safe_player = player_arg.lower().replace(" ", "_") if player_arg.lower() != "all" else "all"
    out_path = OUT_DIR / f"xp_history_{safe_player}_{skill_arg}.png"
    fig.savefig(out_path, facecolor=BG, dpi=150)
    plt.close(fig)

    print(f"[osrsXpHistory] wrote {out_path} ({len(players)} player(s), {n_days} day(s))")
    print(f"PATH:{out_path}")


def main():
    player_arg = sys.argv[1].strip() if len(sys.argv) > 1 else "all"
    skill_arg = sys.argv[2].strip().lower() if len(sys.argv) > 2 else "total"

    df = load_history()
    if df is None or df.empty:
        print("[osrsXpHistory] no history file / empty history yet")
        sys.exit(1)

    if skill_arg in ALLSKILLS_ALIASES:
        plot_allskills(df, player_arg)
    else:
        plot_single_skill(df, player_arg, skill_arg)


if __name__ == "__main__":
    main()
