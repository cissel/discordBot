#!/usr/bin/env python3
"""
buildAdpDispersion.py

Phase 3 extension: two distinct ADP "spread" metrics, requested by James
after noticing his cached ADP board had drifted from Sleeper's live
draft-room page. Deliberately kept as two separate metrics rather than one
number, because they answer different questions:

  1. CROSS-PLATFORM SPREAD (today's snapshot only):
     abs(sleeper_adp - espn_adp) per player, today. High spread = the two
     platforms' drafter pools disagree on where to take this player right
     now - a market-inefficiency / "which room is behind" signal. Needs
     both adp_2026.csv (Sleeper) and espn_adp_2026.csv (ESPN) from the same
     day. Two sources only (Yahoo requires OAuth, FantasyPros' free page
     is capped at 5 rows without a paid API key - not worth it for 2
     extra points on a stdev of 2).

  2. TRAILING VOLATILITY (requires several days of adp_history.csv):
     stdev of a SINGLE platform's (Sleeper) ADP for a player across the
     last N snapshot days. High volatility = the market's price on this
     player is actively moving (injury news, camp buzz, depth chart shakeup)
     - different question from #1, and only becomes meaningful once
     adp_history.csv has accumulated more than 1-2 days of snapshots (single
     source, so cross-platform spread doesn't apply here).

Name matching for #1 reuses the same normalization approach as
buildNfl2026Projections.py's norm_name() (Sleeper<->nflverse join already
solves the identical Jr./Sr./accent problem) since ESPN has its own player
ID space with no shared key to Sleeper's player_id.

Usage: venv/bin/python3 python/buildAdpDispersion.py
Output: outputs/sports/nfl/fantasy/adp_cross_platform_spread.csv (metric #1)
        outputs/sports/nfl/fantasy/adp_volatility.csv (metric #2, skipped
        with a message if fewer than 3 days of Sleeper history exist yet)
"""
import os
import re
import unicodedata

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
SLEEPER_ADP_PATH = os.path.join(OUT_DIR, "adp_2026.csv")
ESPN_ADP_PATH = os.path.join(OUT_DIR, "espn_adp_2026.csv")
HISTORY_PATH = os.path.join(OUT_DIR, "adp_history.csv")

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)
MIN_DAYS_FOR_VOLATILITY = 3  # fewer than this and a stdev is mostly noise, not signal


def norm_name(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def build_cross_platform_spread():
    if not (os.path.exists(SLEEPER_ADP_PATH) and os.path.exists(ESPN_ADP_PATH)):
        print("SKIPPED cross-platform spread: need both adp_2026.csv and espn_adp_2026.csv "
              "(run fetchNflAdpAndRoster.py and fetchEspnAdp.py first).")
        return

    sleeper = pd.read_csv(SLEEPER_ADP_PATH)[["full_name", "position", "adp_overall"]].copy()
    sleeper["norm_name"] = sleeper["full_name"].map(norm_name)
    sleeper = sleeper.rename(columns={"adp_overall": "sleeper_adp"})

    espn = pd.read_csv(ESPN_ADP_PATH)[["full_name", "position", "adp_overall"]].copy()
    espn["norm_name"] = espn["full_name"].map(norm_name)
    espn = espn.rename(columns={"adp_overall": "espn_adp", "full_name": "espn_full_name"})

    merged = sleeper.merge(espn[["norm_name", "espn_adp", "espn_full_name"]], on="norm_name", how="inner")
    unmatched_sleeper = len(sleeper) - len(merged)
    if unmatched_sleeper > 0:
        print(f"Note: {unmatched_sleeper} Sleeper players had no ESPN name match (rookies/name-format "
              f"edge cases) - excluded from spread, not a bug, just no comparison possible.")

    merged["spread"] = (merged["sleeper_adp"] - merged["espn_adp"]).abs()
    merged = merged.sort_values("spread", ascending=False)
    out_cols = ["full_name", "position", "sleeper_adp", "espn_adp", "spread"]
    out_path = os.path.join(OUT_DIR, "adp_cross_platform_spread.csv")
    merged[out_cols].to_csv(out_path, index=False)

    print(f"\nWrote: {out_path} ({len(merged)} matched players)")
    print("CAVEAT (confirmed 2026-08-01 via GraphQL schema introspection + integer-permutation check): "
          "Sleeper's adp_dd_ppr this early in preseason is a gapless integer 1..N permutation - a "
          "consensus RANK, not a real statistical average (ESPN's adp_overall shows genuine decimals "
          "like 6.26, confirming the contrast). Expect Sleeper's numbers to gain real decimal precision "
          "later in August as more real drafts complete. Until then, read 'spread' here as "
          "'Sleeper's community rank vs ESPN's live drafted average' - still a real signal, just not "
          "an apples-to-apples ADP-vs-ADP comparison yet.")
    print("\nTop 10 biggest Sleeper-vs-ESPN ADP disagreements:")
    print(merged[out_cols].head(10).to_string(index=False))


def build_volatility():
    if not os.path.exists(HISTORY_PATH):
        print("\nSKIPPED volatility: adp_history.csv doesn't exist yet - run fetchNflAdpAndRoster.py "
              "daily for a few days first.")
        return

    hist = pd.read_csv(HISTORY_PATH)
    sleeper_hist = hist[hist["source"] == "sleeper"]
    n_days = sleeper_hist["snapshot_date"].nunique()
    if n_days < MIN_DAYS_FOR_VOLATILITY:
        print(f"\nSKIPPED volatility: only {n_days} day(s) of Sleeper history so far "
              f"(need >= {MIN_DAYS_FOR_VOLATILITY}) - check back after a few more daily runs.")
        return

    vol = (sleeper_hist.groupby(["full_name", "position"])["adp_overall"]
           .agg(adp_rank_stdev="std", adp_rank_mean="mean", n_snapshots="count")
           .reset_index())
    vol = vol[vol["n_snapshots"] >= MIN_DAYS_FOR_VOLATILITY]
    vol = vol.sort_values("adp_rank_stdev", ascending=False)
    out_path = os.path.join(OUT_DIR, "adp_volatility.csv")
    vol.to_csv(out_path, index=False)

    print(f"\nWrote: {out_path} ({len(vol)} players, {n_days} days of history)")
    print("CAVEAT: this is Sleeper RANK movement, not true ADP movement, while Sleeper's adp_dd_ppr "
          "field is still integer-quantized preseason (see cross-platform spread caveat above) - "
          "still a legitimate 'is the market moving on this player' signal, just not yet fractional-ADP-precise.")
    print("\nTop 10 most volatile ADP (Sleeper, day-over-day):")
    print(vol.head(10).to_string(index=False))


def main():
    build_cross_platform_spread()
    build_volatility()


if __name__ == "__main__":
    main()
