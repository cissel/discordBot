#!/usr/bin/env python3
"""
mlbBetsTotalRecord.py

One-off / on-demand: recompute the TOTAL W-L and P/L across the entire
hardrock_mlb_bets_log.csv history (not just games not yet in
recap_graded_events.json), using the exact same grading logic as
mlbBetsRecap.py. Prints per-day breakdown plus grand total.

Usage:
  venv/bin/python3 python/mlbBetsTotalRecord.py
"""
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mlbBetsRecap as R

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

    day_totals = defaultdict(lambda: {"wins": 0, "losses": 0, "net": 0.0})
    total_wins = 0
    total_losses = 0
    total_net = 0.0
    games_graded = 0
    games_final_no_ev = 0
    games_not_final_or_error = 0

    for eid, grp in sorted(by_event.items()):
        try:
            espn_json = R.fetch_game_result(eid)
        except Exception:
            games_not_final_or_error += 1
            continue

        if not R.is_final(espn_json):
            games_not_final_or_error += 1
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
            games_final_no_ev += 1
            continue

        player_stats = R.extract_player_stats(espn_json)

        short_name_row = next((r for r in grp if "@" in r["mlbGame"]), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["mlbGame"].split("@", 1)]
        else:
            away_short = scores["away"]["team_full"]
            home_short = scores["home"]["team_full"]

        graded = []
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
            graded.append({"win": win, "profit": profit})

        if not graded:
            games_final_no_ev += 1
            continue

        games_graded += 1
        day_key = first_snapshot[:10]  # scrapedAt date = pregame snapshot day
        for g in graded:
            if g["win"]:
                total_wins += 1
                day_totals[day_key]["wins"] += 1
            else:
                total_losses += 1
                day_totals[day_key]["losses"] += 1
            total_net += g["profit"]
            day_totals[day_key]["net"] += g["profit"]

    print(f"Games graded: {games_graded} | Final w/ no +EV bets: {games_final_no_ev} | Not final / fetch error: {games_not_final_or_error}")
    print()
    print("By pregame-snapshot date:")
    for day in sorted(day_totals):
        d = day_totals[day]
        wl = d["wins"] + d["losses"]
        wp = d["wins"] / wl * 100 if wl else 0
        sign = "+" if d["net"] >= 0 else ""
        print(f"  {day}: {d['wins']}-{d['losses']} ({wp:.1f}%)  {sign}${d['net']:.2f}")
    print()
    total_bets = total_wins + total_losses
    win_pct = total_wins / total_bets * 100 if total_bets else 0
    sign = "+" if total_net >= 0 else ""
    print(f"TOTAL: {total_wins}-{total_losses} ({win_pct:.1f}%)  Net: {sign}${total_net:.2f} on $1 flat stakes across {total_bets} graded bets")

if __name__ == "__main__":
    main()
