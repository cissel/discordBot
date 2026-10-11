#!/usr/bin/env python3
"""
nflBetsTotalRecordByType.py

Reads the persistent outputs/sports/nfl/betting/hardrock_graded_results.csv
log (written by nflBetsRecap.py each run, one row per graded bet, using
nflBetsRecap's own grading policy -- last pre-kickoff snapshot per game)
and rolls it up by bet type: moneyline, spread, total (O/U), player props.

This is a pure CSV read, no ESPN re-fetch -- per sportsbook-ev-analysis
skill §9, re-deriving grading logic in a second script (and especially
picking a DIFFERENT snapshot policy, e.g. "first snapshot" copied from the
MLB rollup) gives numbers that don't match what nflBetsRecap.py actually
graded. Always read hardrock_graded_results.csv for rollups; only
nflBetsRecap.py itself should talk to ESPN's summary endpoint.

Usage:
  venv/bin/python3 python/nflBetsTotalRecordByType.py
"""
import csv
import os
import re
from collections import defaultdict

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/betting")
GRADED_RESULTS_PATH = os.path.join(OUT_DIR, "hardrock_graded_results.csv")


def classify(bet_desc, market):
    if market == "props_odds":
        return "prop"
    if re.search(r"\bML$", bet_desc):
        return "moneyline"
    if re.search(r"spread$", bet_desc):
        return "spread"
    if re.match(r"^[OU]\s*[\d.]+$", bet_desc.strip()):
        return "total"
    return "other"


def main():
    if not os.path.exists(GRADED_RESULTS_PATH):
        print("No graded results yet -- run nflBetsRecap.py first "
              "(it writes this file as it grades finished games).")
        return

    rows = list(csv.DictReader(open(GRADED_RESULTS_PATH)))
    buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "net": 0.0})
    events = set()

    for r in rows:
        win = r["win"] == "True"
        profit = float(r["profit"])
        btype = classify(r["betDescription"], r["market"])
        b = buckets[btype]
        if win:
            b["wins"] += 1
        else:
            b["losses"] += 1
        b["net"] += profit
        events.add(r["espnEventId"])

    print(f"Graded bets: {len(rows)}  |  Games represented: {len(events)}\n")

    order = ["moneyline", "spread", "total", "prop", "other"]
    grand_w = grand_l = 0
    grand_net = 0.0
    for btype in order:
        b = buckets.get(btype)
        if not b:
            continue
        n = b["wins"] + b["losses"]
        wp = b["wins"] / n * 100 if n else 0
        sign = "+" if b["net"] >= 0 else ""
        print(f"{btype:10s}: {b['wins']:3d}-{b['losses']:3d} ({wp:5.1f}%)  {sign}${b['net']:.2f}  ({n} bets, avg {sign}${b['net']/n:.3f}/bet)" if n else f"{btype}: no bets")
        grand_w += b["wins"]
        grand_l += b["losses"]
        grand_net += b["net"]

    print()
    ml = buckets["moneyline"]
    sp = buckets["spread"]
    tot = buckets["total"]
    main_lines = {
        "wins": ml["wins"] + sp["wins"] + tot["wins"],
        "losses": ml["losses"] + sp["losses"] + tot["losses"],
        "net": ml["net"] + sp["net"] + tot["net"],
    }
    n_main = main_lines["wins"] + main_lines["losses"]
    prop = buckets["prop"]
    n_prop = prop["wins"] + prop["losses"]

    print("Grouped comparison:")
    if n_main:
        wp = main_lines["wins"] / n_main * 100
        sign = "+" if main_lines["net"] >= 0 else ""
        print(f"  Game lines (ML+Spread+Total): {main_lines['wins']}-{main_lines['losses']} ({wp:.1f}%)  {sign}${main_lines['net']:.2f}  ({n_main} bets, avg {sign}${main_lines['net']/n_main:.3f}/bet)")
    if n_prop:
        wp = prop["wins"] / n_prop * 100
        sign = "+" if prop["net"] >= 0 else ""
        print(f"  Player props:                  {prop['wins']}-{prop['losses']} ({wp:.1f}%)  {sign}${prop['net']:.2f}  ({n_prop} bets, avg {sign}${prop['net']/n_prop:.3f}/bet)")

    n_all = grand_w + grand_l
    if n_all:
        wp = grand_w / n_all * 100
        sign = "+" if grand_net >= 0 else ""
        print(f"\nGrand total: {grand_w}-{grand_l} ({wp:.1f}%)  {sign}${grand_net:.2f}  ({n_all} bets)")


if __name__ == "__main__":
    main()
