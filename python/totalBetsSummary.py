#!/usr/bin/env python3
"""
totalBetsSummary.py

One-off / on-demand totals across ALL games ever logged by hardrockEV.py
(NFL) and hardrockMlbEV.py (MLB), not just games graded since the last
recap run. Read-only: never touches recap_graded_events.json state.

Reuses the exact same grading logic as nflBetsRecap.py / mlbBetsRecap.py
(same snapshot selection, same +EV filter, same flat $1 stake convention)
so totals here always match what the recaps would have reported.

Usage:
  venv/bin/python3 python/totalBetsSummary.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
import nflBetsRecap as nfl
import mlbBetsRecap as mlb
import clvTracking


def nfl_totals():
    rows = nfl.load_master_log()
    event_id_map = nfl.build_event_id_map(rows)
    games = defaultdict(list)
    for r in rows:
        games[(r["nflGame"], r["matchTime"])].append(r)
    by_event = defaultdict(list)
    for (nfl_game, match_time), grp in games.items():
        eid = None
        for r in grp:
            if r.get("espnEventId"):
                eid = r["espnEventId"]
                break
        if eid is None:
            eid = nfl.resolve_event_id(nfl_game, match_time, event_id_map)
        if eid is None:
            continue
        by_event[eid].extend(grp)

    now_utc = datetime.now(timezone.utc)
    all_graded = []
    checked, finals, no_ev = 0, 0, 0
    for eid, grp in sorted(by_event.items()):
        match_times = {r["matchTime"] for r in grp}
        kicked_off = any(nfl.matchtime_to_utc(mt) <= now_utc for mt in match_times)
        if not kicked_off:
            continue
        checked += 1
        try:
            espn_json = nfl.fetch_game_result(eid)
        except Exception:
            continue
        if not nfl.is_final(espn_json):
            continue
        finals += 1
        scores = nfl.extract_scores(espn_json)
        away_full, home_full = scores["away"]["team_full"], scores["home"]["team_full"]

        by_snapshot = defaultdict(list)
        for r in grp:
            by_snapshot[r["scrapedAt"]].append(r)
        kickoff_utc = nfl.matchtime_to_utc(next(iter(match_times)))
        pre = [s for s in by_snapshot if datetime.fromisoformat(s) <= kickoff_utc]
        if not pre:
            pre = list(by_snapshot.keys())
        last_snapshot = max(pre, key=lambda s: datetime.fromisoformat(s))
        snapshot_rows = by_snapshot[last_snapshot]

        pos_ev_rows = [r for r in snapshot_rows
                       if r["modelStatus"] not in ("no_model", "", None)
                       and r.get("expectedValue") and float(r["expectedValue"]) > 0]
        if not pos_ev_rows:
            no_ev += 1
            continue

        player_stats = nfl.extract_player_stats(espn_json)
        short_name_row = next((r for r in grp if "@" in r["nflGame"] and len(r["nflGame"]) < 30), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["nflGame"].split("@", 1)]
        else:
            away_short, home_short = away_full, home_full

        clv_map = clvTracking.compute_clv_for_event(
            grp, lambda status: status not in ("no_model", "", None)
        )

        for r in pos_ev_rows:
            market = r["market"]
            if market == "props_odds":
                result = nfl.grade_prop_bet(r["betDescription"], player_stats)
            else:
                result = nfl.grade_main_line_bet(r["betDescription"], scores, away_short, home_short)
            if result is None:
                continue
            win, detail = result
            profit = nfl.american_profit_per_dollar(r["payout"]) if win else -1.0
            all_graded.append({"sport": "NFL", "game": f"{away_full} @ {home_full}",
                                "desc": r["betDescription"], "win": win, "profit": profit,
                                "clv": clv_map.get(r.get("outcomeId"))})
    return all_graded, checked, finals, no_ev


def mlb_totals():
    rows = mlb.load_master_log()
    event_id_map = mlb.build_event_id_map(rows)
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
            eid = mlb.resolve_event_id(mlb_game, match_time, event_id_map)
        if eid is None:
            continue
        by_event[eid].extend(grp)

    all_graded = []
    checked, finals, no_ev = 0, 0, 0
    for eid, grp in sorted(by_event.items()):
        checked += 1
        try:
            espn_json = mlb.fetch_game_result(eid)
        except Exception:
            continue
        if not mlb.is_final(espn_json):
            continue
        finals += 1
        scores = mlb.extract_scores(espn_json)
        away_full, home_full = scores["away"]["team_full"], scores["home"]["team_full"]

        by_snapshot = defaultdict(list)
        for r in grp:
            by_snapshot[r["scrapedAt"]].append(r)
        first_snapshot = min(by_snapshot, key=lambda s: datetime.fromisoformat(s))
        snapshot_rows = by_snapshot[first_snapshot]

        pos_ev_rows = [r for r in snapshot_rows
                       if r["modelStatus"] in ("espn_bpi", "draftkings_devig", "mlbapi_poisson")
                       and r.get("expectedValue") and float(r["expectedValue"]) > 0]
        if not pos_ev_rows:
            no_ev += 1
            continue

        player_stats = mlb.extract_player_stats(espn_json)
        short_name_row = next((r for r in grp if "@" in r["mlbGame"]), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["mlbGame"].split("@", 1)]
        else:
            away_short, home_short = away_full, home_full

        clv_map = clvTracking.compute_clv_for_event(
            grp, lambda status: status in ("espn_bpi", "draftkings_devig", "mlbapi_poisson")
        )

        for r in pos_ev_rows:
            market = r["market"]
            if market == "props_odds":
                result = mlb.grade_prop_bet(r["betDescription"], player_stats)
            else:
                result = mlb.grade_main_line_bet(r["betDescription"], scores, away_short, home_short)
            if result is None:
                continue
            win, detail = result
            profit = mlb.american_profit_per_dollar(r["payout"]) if win else -1.0
            all_graded.append({"sport": "MLB", "game": f"{away_full} @ {home_full}",
                                "desc": r["betDescription"], "win": win, "profit": profit,
                                "clv": clv_map.get(r.get("outcomeId"))})
    return all_graded, checked, finals, no_ev


def summarize(label, graded):
    wins = [g for g in graded if g["win"]]
    losses = [g for g in graded if not g["win"]]
    net = sum(g["profit"] for g in graded)
    clv_list = [g["clv"] for g in graded if g.get("clv") is not None]
    n_clv, avg_clv, pct_beat, n_single = clvTracking.summarize_clv(clv_list)
    line = (f"{label}: {len(wins)}-{len(losses)} ({len(graded)} graded bets) | "
            f"Net on $1 flat stakes: {'+' if net >= 0 else ''}${net:.2f}")
    if avg_clv is not None:
        line += (f" | Avg CLV: {clvTracking.fmt_clv(avg_clv)} "
                  f"({pct_beat*100:.0f}% beat close, {n_clv} measurable, {n_single} single-snapshot)")
    print(line)
    return wins, losses, net


def main():
    nfl_graded, nfl_checked, nfl_finals, nfl_no_ev = nfl_totals()
    mlb_graded, mlb_checked, mlb_finals, mlb_no_ev = mlb_totals()

    print(f"[debug] NFL: {nfl_checked} events checked, {nfl_finals} final, {nfl_no_ev} final w/ no +EV bets")
    print(f"[debug] MLB: {mlb_checked} events checked, {mlb_finals} final, {mlb_no_ev} final w/ no +EV bets")
    print()

    nfl_w, nfl_l, nfl_net = summarize("🏈 NFL", nfl_graded)
    mlb_w, mlb_l, mlb_net = summarize("⚾ MLB", mlb_graded)

    all_graded = nfl_graded + mlb_graded
    all_w, all_l, all_net = summarize("TOTAL", all_graded)


if __name__ == "__main__":
    main()
