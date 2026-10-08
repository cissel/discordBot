#!/usr/bin/env python3
"""
mlbBetsTotalRecordByType.py

Same grading pipeline as mlbBetsRecap.py / mlbBetsTotalRecord.py, but
splits the totals out by bet type: moneyline, run line, total (O/U), and
player props. Lets you see if the edge (if any) is concentrated in one
bucket vs. spread thin/negative across all of them.

Usage:
  venv/bin/python3 python/mlbBetsTotalRecordByType.py
"""
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mlbBetsRecap as R


def classify(bet_desc, market):
    if market == "props_odds":
        return "prop"
    if re.search(r"\bML$", bet_desc):
        return "moneyline"
    if re.search(r"run line$", bet_desc):
        return "run_line"
    if re.match(r"^[OU]\s*[\d.]+$", bet_desc.strip()):
        return "total"
    return "other"


def main():
    rows = R.load_master_log()
    event_id_map = R.build_event_id_map(rows)

    games = defaultdict(list)
    for r in rows:
        games[(r["mlbGame"], r["matchTime"])].append(r)

    by_event = defaultdict(list)
    for (mlb_game, match_time), grp in games.items():
        eid = None
        for r in grp:
            if r.get("espnEventId"):
                eid = r["espnEventId"]
                break
        if eid is None:
            eid = R.resolve_event_id(mlb_game, match_time, event_id_map)
        if eid is None:
            continue
        by_event[eid].extend(grp)

    buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "net": 0.0})
    games_graded = 0

    for eid, grp in sorted(by_event.items()):
        try:
            espn_json = R.fetch_game_result(eid)
        except Exception:
            continue
        if not R.is_final(espn_json):
            continue

        scores = R.extract_scores(espn_json)

        by_snapshot = defaultdict(list)
        for r in grp:
            by_snapshot[r["scrapedAt"]].append(r)
        first_snapshot = min(by_snapshot, key=lambda s: datetime.fromisoformat(s))
        snapshot_rows = by_snapshot[first_snapshot]

        pos_ev_rows = [r for r in snapshot_rows
                       if r["modelStatus"] in ("espn_bpi", "draftkings_devig", "mlbapi_poisson")
                       and r.get("expectedValue")
                       and float(r["expectedValue"]) > 0]
        if not pos_ev_rows:
            continue

        player_stats = R.extract_player_stats(espn_json)

        short_name_row = next((r for r in grp if "@" in r["mlbGame"]), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["mlbGame"].split("@", 1)]
        else:
            away_short = scores["away"]["team_full"]
            home_short = scores["home"]["team_full"]

        any_graded = False
        for r in pos_ev_rows:
            market = r["market"]
            if market == "props_odds":
                result = R.grade_prop_bet(r["betDescription"], player_stats)
            else:
                result = R.grade_main_line_bet(r["betDescription"], scores, away_short, home_short)
            if result is None:
                continue
            win, detail = result
            profit = R.american_profit_per_dollar(r["payout"]) if win else -1.0
            btype = classify(r["betDescription"], market)
            b = buckets[btype]
            if win:
                b["wins"] += 1
            else:
                b["losses"] += 1
            b["net"] += profit
            any_graded = True
        if any_graded:
            games_graded += 1

    print(f"Games with graded bets: {games_graded}\n")

    order = ["moneyline", "run_line", "total", "prop", "other"]
    grand_w = grand_l = 0
    grand_net = 0.0
    for btype in order:
        b = buckets.get(btype)
        if not b:
            continue
        n = b["wins"] + b["losses"]
        wp = b["wins"] / n * 100 if n else 0
        sign = "+" if b["net"] >= 0 else ""
        print(f"{btype:12s}: {b['wins']:3d}-{b['losses']:3d} ({wp:5.1f}%)  {sign}${b['net']:.2f}  ({n} bets, avg {sign}${b['net']/n:.3f}/bet)" if n else f"{btype}: no bets")
        grand_w += b["wins"]
        grand_l += b["losses"]
        grand_net += b["net"]

    print()
    ml = buckets["moneyline"]
    rl = buckets["run_line"]
    tot = buckets["total"]
    main_lines = {
        "wins": ml["wins"] + rl["wins"] + tot["wins"],
        "losses": ml["losses"] + rl["losses"] + tot["losses"],
        "net": ml["net"] + rl["net"] + tot["net"],
    }
    n_main = main_lines["wins"] + main_lines["losses"]
    prop = buckets["prop"]
    n_prop = prop["wins"] + prop["losses"]

    print("Grouped comparison:")
    if n_main:
        wp = main_lines["wins"] / n_main * 100
        sign = "+" if main_lines["net"] >= 0 else ""
        print(f"  Game lines (ML+RL+Total): {main_lines['wins']}-{main_lines['losses']} ({wp:.1f}%)  {sign}${main_lines['net']:.2f}  ({n_main} bets, avg {sign}${main_lines['net']/n_main:.3f}/bet)")
    if n_prop:
        wp = prop["wins"] / n_prop * 100
        sign = "+" if prop["net"] >= 0 else ""
        print(f"  Player props:              {prop['wins']}-{prop['losses']} ({wp:.1f}%)  {sign}${prop['net']:.2f}  ({n_prop} bets, avg {sign}${prop['net']/n_prop:.3f}/bet)")

    n_all = grand_w + grand_l
    if n_all:
        wp = grand_w / n_all * 100
        sign = "+" if grand_net >= 0 else ""
        print(f"\nGrand total: {grand_w}-{grand_l} ({wp:.1f}%)  {sign}${grand_net:.2f}  ({n_all} bets)")


if __name__ == "__main__":
    main()
