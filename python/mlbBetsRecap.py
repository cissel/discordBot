#!/usr/bin/env python3
"""
mlbBetsRecap.py

Daily recap of the +EV bets hardrockMlbEV.py flagged, graded against
actual game results. Same pattern as nflBetsRecap.py -- reads
outputs/sports/mlb/betting/hardrock_mlb_bets_log.csv (the append-only
master log), finds games that have gone FINAL since the last recap,
grades the LAST pre-first-pitch snapshot's +EV bets for each, and prints
a plain-text Discord-friendly recap (no markdown tables).

Only grades bets where modelStatus is a real model (espn_bpi /
draftkings_devig / mlbapi_poisson) and expectedValue > 0 in the snapshot
closest to (but before) first pitch. Flat $1 stakes, matching the log's
own EV-per-$1 convention.

State: outputs/sports/mlb/betting/recap_graded_events.json -- espnEventIds
already reported, so re-running (or the daily cron) never double-reports
a game. Safe to delete to force a re-grade of everything.

No-agent cron output contract: empty stdout -> nothing delivered. If no
new games went final since the last run, this script prints nothing.

Usage:
  venv/bin/python3 python/mlbBetsRecap.py
"""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import requests

import clvTracking

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/mlb/betting")
MASTER_LOG_PATH = os.path.join(OUT_DIR, "hardrock_mlb_bets_log.csv")
STATE_PATH = os.path.join(OUT_DIR, "recap_graded_events.json")

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={event_id}"

# Prop market -> which actual boxscore stat key to read (from ESPN's
# batting/pitching category `keys` array -- see extract_player_stats).
BATTER_ACTUAL_KEYS = {
    "Hits": ["hits"],
    "Runs": ["runs"],
    "RBIs": ["RBIs"],
    "Home Runs": ["homeRuns"],
    "Total Bases": None,  # computed, see extract_player_stats
    "Singles": None,      # computed
    "Doubles": None,      # not in ESPN's boxscore summary keys -- no_actual_stat
    "Triples": None,
    "Stolen Bases": None,
    "Hits+Runs+RBI": ["hits", "runs", "RBIs"],
}
PITCHER_ACTUAL_KEYS = {
    "Strikeouts": ["strikeouts"],
    "Earned Runs": ["earnedRuns"],
    "Hits Allowed": ["hits"],
}

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    s = (s or "").strip().lower()
    s = SUFFIXES.sub("", s).strip()
    s = re.sub(r"[.'\-]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return set(json.load(f))
    return set()


def save_state(graded_ids):
    with open(STATE_PATH, "w") as f:
        json.dump(sorted(graded_ids), f)


def load_master_log():
    rows = []
    with open(MASTER_LOG_PATH) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def build_event_id_map(rows):
    """(hr_event_name, matchTime) -> espnEventId, from main-line rows (the
    only rows with espnEventId populated). Prop rows share the same
    hr_event_name format ('Twins @ Angels') as main-line rows for MLB
    (unlike NFL, HardRock doesn't expand to full team names for MLB props),
    so this direct key match should cover both."""
    m = {}
    for r in rows:
        if r.get("espnEventId"):
            m[(r["mlbGame"], r["matchTime"])] = r["espnEventId"]
    return m


def resolve_event_id(mlb_game, match_time, event_id_map):
    if (mlb_game, match_time) in event_id_map:
        return event_id_map[(mlb_game, match_time)]
    # fallback: same matchTime, team-name substring match (handles any
    # naming drift between prop and main-line rows for the same game)
    for (key_game, mt), eid in event_id_map.items():
        if mt != match_time:
            continue
        teams = [t.strip() for t in key_game.split("@")]
        if all(t in mlb_game for t in teams):
            return eid
    return None


def fetch_game_result(event_id):
    resp = requests.get(ESPN_SUMMARY.format(event_id=event_id), timeout=20)
    resp.raise_for_status()
    return resp.json()


def is_final(espn_json):
    try:
        comp = espn_json["header"]["competitions"][0]
        return comp["status"]["type"]["completed"] is True
    except (KeyError, IndexError):
        return False


def extract_scores(espn_json):
    comp = espn_json["header"]["competitions"][0]
    out = {}
    for c in comp["competitors"]:
        out[c["homeAway"]] = {
            "team_full": c["team"]["displayName"],
            "score": int(c.get("score", 0)),
        }
    return out


def extract_player_stats(espn_json):
    """normalized player name -> dict of summed actual stats, keyed to
    match BATTER_ACTUAL_KEYS/PITCHER_ACTUAL_KEYS field names."""
    stats = defaultdict(lambda: defaultdict(int))
    players = espn_json.get("boxscore", {}).get("players", [])
    for team in players:
        for cat in team.get("statistics", []):
            keys = cat.get("keys", [])
            cat_type = cat.get("type")
            for ath in cat.get("athletes", []):
                name = norm_name(ath["athlete"]["displayName"])
                vals = ath.get("stats") or []
                row = dict(zip(keys, vals))
                try:
                    if cat_type == "batting":
                        stats[name]["hits"] = int(row.get("hits", 0) or 0)
                        stats[name]["runs"] = int(row.get("runs", 0) or 0)
                        stats[name]["RBIs"] = int(row.get("RBIs", 0) or 0)
                        stats[name]["homeRuns"] = int(row.get("homeRuns", 0) or 0)
                    elif cat_type == "pitching":
                        stats[name]["strikeouts"] = int(row.get("strikeouts", 0) or 0)
                        stats[name]["earnedRuns"] = int(row.get("earnedRuns", 0) or 0)
                        stats[name]["hits"] = int(row.get("hits", 0) or 0)
                except (ValueError, TypeError):
                    continue
    return stats


def parse_prop_desc(bet_desc):
    """'Turner Hill - RBIs (O 0.5)' -> ('Turner Hill', 'RBIs', 'over', 0.5)."""
    m = re.match(r"^(.*?) - (.*?) \(([OU])\s*([\d.]+)\)$", bet_desc)
    if not m:
        return None
    player, market, side_letter, line = m.groups()
    side = "over" if side_letter.upper() == "O" else "under"
    return player.strip(), market.strip(), side, float(line)


def parse_runline_desc(bet_desc):
    """'Cardinals -1.5 run line' -> ('Cardinals', 1.5, minus_sign)."""
    m = re.match(r"^(.*?)\s+([-+][\d.]+)\s+run line$", bet_desc)
    if not m:
        return None
    team, point = m.groups()
    return team.strip(), float(point)


def parse_total_desc(bet_desc):
    m = re.match(r"^([OU])\s*([\d.]+)$", bet_desc.strip())
    if not m:
        return None
    side_letter, point = m.groups()
    side = "over" if side_letter.upper() == "O" else "under"
    return side, float(point)


def parse_ml_desc(bet_desc):
    m = re.match(r"^(.*?)\s+ML$", bet_desc)
    return m.group(1).strip() if m else None


def american_profit_per_dollar(odds_str):
    val = float(str(odds_str).strip().replace("+", ""))
    return val / 100.0 if val > 0 else 100.0 / abs(val)


def grade_prop_bet(bet_desc, player_stats):
    parsed = parse_prop_desc(bet_desc)
    if not parsed:
        return None
    player, market, side, line = parsed
    if market in BATTER_ACTUAL_KEYS:
        keys = BATTER_ACTUAL_KEYS[market]
    elif market in PITCHER_ACTUAL_KEYS:
        keys = PITCHER_ACTUAL_KEYS[market]
    else:
        keys = None
    if not keys:
        return None  # e.g. Total Bases/Doubles/Triples/Stolen Bases -- ESPN's
                      # boxscore summary doesn't expose these fields directly
    p_stats = player_stats.get(norm_name(player))
    if p_stats is None:
        return None
    actual = sum(p_stats.get(k, 0) for k in keys)
    if actual == line:
        return None  # push
    if side == "over":
        return actual > line, f"{player} {market}: actual {actual} (line {line})"
    else:
        return actual < line, f"{player} {market}: actual {actual} (line {line})"


def grade_main_line_bet(bet_desc, scores, away_short, home_short):
    away_score = scores["away"]["score"]
    home_score = scores["home"]["score"]

    ml_team = parse_ml_desc(bet_desc)
    if ml_team:
        if ml_team == away_short:
            return away_score > home_score, f"Final {away_short} {away_score} - {home_short} {home_score}"
        elif ml_team == home_short:
            return home_score > away_score, f"Final {away_short} {away_score} - {home_short} {home_score}"
        return None

    runline = parse_runline_desc(bet_desc)
    if runline:
        team, point = runline
        if team == away_short:
            margin = away_score - home_score
        elif team == home_short:
            margin = home_score - away_score
        else:
            return None
        result = margin + point
        if result == 0:
            return None  # push
        return result > 0, f"Final {away_short} {away_score} - {home_short} {home_score} (margin {margin:+.1f}, needed {point:+.1f})"

    total = parse_total_desc(bet_desc)
    if total:
        side, point = total
        actual_total = away_score + home_score
        if actual_total == point:
            return None
        if side == "over":
            return actual_total > point, f"Total {actual_total} (line {point})"
        else:
            return actual_total < point, f"Total {actual_total} (line {point})"

    return None


def main():
    if not os.path.exists(MASTER_LOG_PATH):
        return  # nothing logged yet -> no output

    rows = load_master_log()
    graded_events = load_state()
    event_id_map = build_event_id_map(rows)

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
            eid = resolve_event_id(mlb_game, match_time, event_id_map)
        if eid is None:
            continue
        by_event[eid].extend(grp)

    recap_sections = []

    for eid, grp in sorted(by_event.items()):
        if eid in graded_events:
            continue

        try:
            espn_json = fetch_game_result(eid)
        except requests.RequestException:
            continue

        if not is_final(espn_json):
            continue  # still in progress or not started -- check again next run

        scores = extract_scores(espn_json)
        away_full = scores["away"]["team_full"]
        home_full = scores["home"]["team_full"]

        by_snapshot = defaultdict(list)
        for r in grp:
            by_snapshot[r["scrapedAt"]].append(r)
        # Use the earliest available snapshot for this game (MLB games often
        # only get one or two scrapes before first pitch given the daily
        # 2h cron cadence vs. a several-hour pregame window) -- earliest
        # snapshot is the one furthest from any in-game line movement.
        first_snapshot = min(by_snapshot, key=lambda s: datetime.fromisoformat(s))
        snapshot_rows = by_snapshot[first_snapshot]

        pos_ev_rows = [r for r in snapshot_rows
                       if r["modelStatus"] in ("espn_bpi", "draftkings_devig", "mlbapi_poisson")
                       and r.get("expectedValue")
                       and float(r["expectedValue"]) > 0]

        if not pos_ev_rows:
            graded_events.add(eid)
            continue

        player_stats = extract_player_stats(espn_json)

        short_name_row = next((r for r in grp if "@" in r["mlbGame"]), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["mlbGame"].split("@", 1)]
        else:
            away_short, home_short = away_full, home_full

        # CLV uses ALL snapshots ever logged for this game (grp), not just
        # the earliest one used for W/L grading -- it needs the FIRST time
        # each outcomeId showed +EV vs. the LAST (closing) snapshot.
        clv_map = clvTracking.compute_clv_for_event(
            grp, lambda status: status in ("espn_bpi", "draftkings_devig", "mlbapi_poisson")
        )

        graded = []
        for r in pos_ev_rows:
            market = r["market"]
            if market == "props_odds":
                result = grade_prop_bet(r["betDescription"], player_stats)
            else:
                result = grade_main_line_bet(r["betDescription"], scores, away_short, home_short)
            if result is None:
                continue  # unparseable, push, or no comparable actual stat
            win, detail = result
            profit = american_profit_per_dollar(r["payout"]) if win else -1.0
            graded.append({
                "desc": r["betDescription"],
                "payout": r["payout"],
                "confidence": r["confidence"],
                "ev": r["expectedValue"],
                "win": win,
                "detail": detail,
                "profit": profit,
                "clv": clv_map.get(r.get("outcomeId")),
            })

        if not graded:
            graded_events.add(eid)
            continue

        wins = [g for g in graded if g["win"]]
        losses = [g for g in graded if not g["win"]]
        net = sum(g["profit"] for g in graded)
        game_clv = [g["clv"] for g in graded if g["clv"] is not None]
        n_clv, avg_clv, pct_beat, n_single = clvTracking.summarize_clv(game_clv)

        lines = []
        lines.append(f"**{away_full} @ {home_full}** — Final {scores['away']['score']}-{scores['home']['score']}")
        for g in sorted(graded, key=lambda x: -x["profit"]):
            emoji = "✅" if g["win"] else "❌"
            sign = "+" if g["profit"] >= 0 else ""
            clv_str = ""
            if g["clv"] is not None and not g["clv"]["single_snapshot"]:
                clv_str = f" — CLV: {clvTracking.fmt_clv(g['clv']['clv'])}"
            lines.append(f"{emoji} {g['desc']} ({g['payout']}) — {g['detail']} — {sign}${g['profit']:.2f}{clv_str}")
        record_line = f"Record: {len(wins)}-{len(losses)} | Net on $1 flat stakes: {'+' if net >= 0 else ''}${net:.2f}"
        if avg_clv is not None:
            record_line += (f" | Avg CLV: {clvTracking.fmt_clv(avg_clv)} "
                             f"({pct_beat*100:.0f}% beat close, {n_clv} measurable)")
        lines.append(record_line)
        recap_sections.append("\n".join(lines))

        graded_events.add(eid)

    save_state(graded_events)

    if recap_sections:
        header = "⚾ **MLB +EV Bets Recap** (research only, no real wagers placed)\n"
        print(header + "\n\n".join(recap_sections))
        print("\nNot proven edges -- tracking CLV/outcomes over multiple weeks before trusting any single bet.")


if __name__ == "__main__":
    main()
