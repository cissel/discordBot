#!/usr/bin/env python3
"""
nflBetsRecap.py

Daily recap of the +EV bets hardrockEV.py flagged, graded against actual
game results. Reads outputs/sports/nfl/betting/hardrock_bets_log.csv
(the append-only master log every hardrockEV.py run writes to), finds
games that have gone FINAL since the last recap, grades the LAST
pre-kickoff snapshot's +EV bets for each, and prints a plain-text
Discord-friendly recap (no markdown tables -- Discord doesn't render
those).

Only grades bets where modelStatus != 'no_model' and expectedValue > 0
in the snapshot closest to (but before) kickoff -- i.e. exactly what
/nfl bets would have shown right before the game started. Flat $1
stakes, matching the log's own EV-per-$1 convention.

State: outputs/sports/nfl/betting/recap_graded_events.json -- espnEventIds
already reported, so re-running (or the daily cron) never double-reports
a game. Safe to delete to force a re-grade of everything.

No-agent cron output contract: empty stdout -> nothing delivered. If no
new games went final since the last run, this script prints nothing.

Usage:
  venv/bin/python3 python/nflBetsRecap.py
"""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import clvTracking

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/betting")
MASTER_LOG_PATH = os.path.join(OUT_DIR, "hardrock_bets_log.csv")
STATE_PATH = os.path.join(OUT_DIR, "recap_graded_events.json")
GRADED_RESULTS_PATH = os.path.join(OUT_DIR, "hardrock_graded_results.csv")
GRADED_RESULTS_FIELDS = [
    "espnEventId", "nflGame", "matchTime", "market", "betDescription",
    "payout", "confidence", "expectedValue", "modelStatus", "win", "profit",
    "clv", "gradedAt",
]

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"

# Prop market -> which actual boxscore stat category+field(s) to sum.
# Mirrors hardrockEV.py's PROP_STAT_KEYS (same categories the model itself
# projected against), so grading is apples-to-apples with what was priced.
ACTUAL_STAT_KEYS = {
    "Anytime TD": ["rush_td", "rec_td"],
    "To Score 2+ TDs": ["rush_td", "rec_td"],
    "To Score 3+ TDs": ["rush_td", "rec_td"],
    "Passing TDs": ["pass_td"],
    "Receptions": ["rec"],
    "Pass Completions": ["pass_cmp"],
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


def append_graded_results(rows):
    """Append one row per graded bet to the persistent results log, so
    later rollups (total W/L, P&L, by bet-type) are a fast CSV read
    instead of re-fetching/re-grading every final game from ESPN."""
    if not rows:
        return
    write_header = not os.path.exists(GRADED_RESULTS_PATH)
    with open(GRADED_RESULTS_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GRADED_RESULTS_FIELDS)
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def load_master_log():
    rows = []
    with open(MASTER_LOG_PATH) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def matchtime_to_utc(matchtime_str):
    """hardrockEV.py's matchTime column is isportgenius's own server tz,
    UTC+10 -- convert to real UTC."""
    naive = datetime.strptime(matchtime_str, "%Y-%m-%d %H:%M:%S")
    return (naive - timedelta(hours=10)).replace(tzinfo=timezone.utc)


def build_event_id_map(rows):
    """(short_nflGame, matchTime) -> espnEventId, from main-line rows
    (the only rows with espnEventId populated)."""
    m = {}
    for r in rows:
        if r.get("espnEventId"):
            m[(r["nflGame"], r["matchTime"])] = r["espnEventId"]
    return m


def resolve_event_id(nfl_game_full, match_time, event_id_map):
    """Prop rows carry full team names (e.g. 'Detroit Lions @ Buffalo
    Bills') with no espnEventId. Match against the short-name keys
    (e.g. 'Lions @ Bills') from main-line rows sharing the same
    matchTime, disambiguating simultaneous kickoffs by team-name
    substring match."""
    for (short_name, mt), eid in event_id_map.items():
        if mt != match_time:
            continue
        teams = [t.strip() for t in short_name.split("@")]
        if all(t in nfl_game_full for t in teams):
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
            "score": int(c["score"]),
        }
    return out


def extract_player_stats(espn_json):
    """normalized player name -> dict of summed actual stats."""
    stats = defaultdict(lambda: defaultdict(int))
    players = espn_json.get("boxscore", {}).get("players", [])
    for team in players:
        for cat in team.get("statistics", []):
            cat_name = cat.get("name")
            for ath in cat.get("athletes", []):
                name = norm_name(ath["athlete"]["displayName"])
                s = ath.get("stats") or []
                try:
                    if cat_name == "passing" and len(s) >= 4:
                        comp_att = s[0].split("/")
                        stats[name]["pass_cmp"] += int(comp_att[0])
                        stats[name]["pass_td"] += int(s[3])
                    elif cat_name == "rushing" and len(s) >= 4:
                        stats[name]["rush_td"] += int(s[3])
                    elif cat_name == "receiving" and len(s) >= 4:
                        stats[name]["rec"] += int(s[0])
                        stats[name]["rec_td"] += int(s[3])
                except (ValueError, IndexError):
                    continue
    return stats


def parse_prop_desc(bet_desc):
    """'Jahmyr Gibbs - Anytime TD (O 0.5)' ->
    ('Jahmyr Gibbs', 'Anytime TD', 'over', 0.5)."""
    m = re.match(r"^(.*?) - (.*?) \(([OU])\s*([\d.]+)\)$", bet_desc)
    if not m:
        m = re.match(r"^(.*?) - (.*?) \((Yes|No)\)$", bet_desc, re.IGNORECASE)
        if m:
            player, market, side = m.groups()
            return player.strip(), market.strip(), side.lower(), None
        return None
    player, market, side_letter, line = m.groups()
    side = "over" if side_letter.upper() == "O" else "under"
    return player.strip(), market.strip(), side, float(line)


def parse_spread_desc(bet_desc):
    """'Lions +6.0 spread' -> ('Lions', 6.0, 'plus') / 'Bills -6.0 spread'
    -> ('Bills', 6.0, 'minus')."""
    m = re.match(r"^(.*?)\s+([-+][\d.]+)\s+spread$", bet_desc)
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
    if market not in ACTUAL_STAT_KEYS:
        return None
    actual = sum(player_stats.get(norm_name(player), {}).get(k, 0)
                 for k in ACTUAL_STAT_KEYS[market])
    if side == "yes":
        return actual >= 1, f"{player} {market}: actual {actual}"
    if side == "no":
        return actual == 0, f"{player} {market}: actual {actual}"
    if line is None:
        return None
    if side == "over":
        if actual == line:
            return None  # push
        return actual > line, f"{player} {market}: actual {actual} (line {line})"
    else:
        if actual == line:
            return None
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

    spread = parse_spread_desc(bet_desc)
    if spread:
        team, point = spread
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

    # Group rows by (nflGame, matchTime) to find each distinct game and
    # its snapshots ordered by scrapedAt.
    games = defaultdict(list)
    for r in rows:
        games[(r["nflGame"], r["matchTime"])].append(r)

    # Resolve each game grouping to an espn event id, then re-group by
    # that id so full-name (props) and short-name (main lines) groupings
    # for the same real-world game get merged.
    by_event = defaultdict(list)
    for (nfl_game, match_time), grp in games.items():
        eid = None
        for r in grp:
            if r.get("espnEventId"):
                eid = r["espnEventId"]
                break
        if eid is None:
            eid = resolve_event_id(nfl_game, match_time, event_id_map)
        if eid is None:
            continue
        by_event[eid].extend(grp)

    now_utc = datetime.now(timezone.utc)
    recap_sections = []

    for eid, grp in sorted(by_event.items()):
        if eid in graded_events:
            continue

        # Only bother checking games whose kickoff has already passed.
        match_times = {r["matchTime"] for r in grp}
        kicked_off = any(matchtime_to_utc(mt) <= now_utc for mt in match_times)
        if not kicked_off:
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

        # Snapshot closest to (but before) kickoff, per bet's own matchTime.
        by_snapshot = defaultdict(list)
        for r in grp:
            by_snapshot[r["scrapedAt"]].append(r)
        kickoff_utc = matchtime_to_utc(next(iter(match_times)))
        pre_kickoff_snapshots = [s for s in by_snapshot if
                                  datetime.fromisoformat(s) <= kickoff_utc]
        if not pre_kickoff_snapshots:
            pre_kickoff_snapshots = list(by_snapshot.keys())
        last_snapshot = max(pre_kickoff_snapshots,
                             key=lambda s: datetime.fromisoformat(s))
        snapshot_rows = by_snapshot[last_snapshot]

        pos_ev_rows = [r for r in snapshot_rows
                       if r["modelStatus"] not in ("no_model", "", None)
                       and r.get("expectedValue")
                       and float(r["expectedValue"]) > 0]

        if not pos_ev_rows:
            graded_events.add(eid)
            continue

        player_stats = extract_player_stats(espn_json)

        # Short names for main-line grading (away/home as printed by HR).
        short_name_row = next((r for r in grp if "@" in r["nflGame"]
                                and len(r["nflGame"]) < 30), None)
        if short_name_row:
            away_short, home_short = [t.strip() for t in short_name_row["nflGame"].split("@", 1)]
        else:
            away_short, home_short = away_full, home_full

        # CLV uses ALL snapshots ever logged for this game (grp), not just
        # the pre-kickoff one used for W/L grading -- it needs the FIRST
        # time each outcomeId showed +EV vs. the LAST (closing) snapshot.
        clv_map = clvTracking.compute_clv_for_event(
            grp, lambda status: status not in ("no_model", "", None)
        )

        graded = []
        persist_rows = []
        for r in pos_ev_rows:
            market = r["market"]
            if market == "props_odds":
                result = grade_prop_bet(r["betDescription"], player_stats)
            else:
                result = grade_main_line_bet(r["betDescription"], scores, away_short, home_short)
            if result is None:
                continue  # unparseable or push -- excluded from record
            win, detail = result
            profit = american_profit_per_dollar(r["payout"]) if win else -1.0
            clv = clv_map.get(r.get("outcomeId"))
            graded.append({
                "desc": r["betDescription"],
                "payout": r["payout"],
                "confidence": r["confidence"],
                "ev": r["expectedValue"],
                "win": win,
                "detail": detail,
                "profit": profit,
                "clv": clv,
            })
            persist_rows.append({
                "espnEventId": eid,
                "nflGame": r["nflGame"],
                "matchTime": r["matchTime"],
                "market": market,
                "betDescription": r["betDescription"],
                "payout": r["payout"],
                "confidence": r["confidence"],
                "expectedValue": r["expectedValue"],
                "modelStatus": r["modelStatus"],
                "win": win,
                "profit": profit,
                "clv": clv["clv"] if clv and not clv.get("single_snapshot") else "",
                "gradedAt": now_utc.isoformat(),
            })

        if not graded:
            graded_events.add(eid)
            continue

        append_graded_results(persist_rows)

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
        header = "🏈 **NFL +EV Bets Recap** (research only, no real wagers placed)\n"
        print(header + "\n\n".join(recap_sections))
        print("\nNot proven edges -- tracking CLV/outcomes over multiple weeks before trusting any single bet.")


if __name__ == "__main__":
    main()
