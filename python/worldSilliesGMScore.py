#!/usr/bin/env python3
"""
worldSilliesGMScore.py
Grades each manager in the World Sillies league on their decisions
for the most recently completed matchup period.

Scoring (0-100):
  Start/Sit accuracy  40pts  - did you bench anyone who outscored your starters?
  Roster quality      30pts  - season OPS/ERA of active roster vs league average
  Weekly efficiency   20pts  - score vs projected, and win/loss vs expected
  Waiver smarts       10pts  - recent adds who contributed points

Writes: ~/discordBot/outputs/sports/mlb/fantasy/gm_scores.json
"""

import os, sys, json, datetime, requests, unicodedata
from pathlib import Path
from collections import defaultdict

BASE        = Path(os.path.expanduser("~/discordBot"))
FANTASY_DIR = BASE / "outputs/sports/mlb/fantasy"
OUT_JSON    = FANTASY_DIR / "gm_scores.json"
FANTASY_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_YEAR = datetime.date.today().year
MLB_API      = "https://statsapi.mlb.com/api/v1"
HEADERS_MLB  = {"User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)"}

# Lineup slot IDs - anything not in BENCH/IL is a starter
BENCH_SLOT = 16
IL_SLOT    = 17
INACTIVE_SLOTS = {BENCH_SLOT, IL_SLOT}

# League averages
LG_OPS  = 0.718
LG_ERA  = 4.20
LG_WHIP = 1.28

# ESPN stat ID mappings (from raw API)
# stat 8  = strikeouts (batters)
# stat 20 = runs
# stat 21 = RBI
# We use appliedStatTotal (fantasy points) directly - no need to decode stat IDs

def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").strip().lower()

def mlb_get(url, params=None, timeout=8):
    r = requests.get(url, params=params, headers=HEADERS_MLB, timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_player_season_stats(mlbam_id: int, is_pitcher: bool) -> dict:
    """Pull season stats from MLB Stats API."""
    try:
        group = "pitching" if is_pitcher else "hitting"
        data  = mlb_get(f"{MLB_API}/people/{mlbam_id}/stats",
                        {"stats": "season", "group": group, "season": CURRENT_YEAR})
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        if is_pitcher:
            ip   = float(s.get("inningsPitched", 0) or 0)
            era  = float(s.get("era", 99) or 99)
            whip = float(s.get("whip", 99) or 99)
            return {"ip": ip, "era": era, "whip": whip}
        else:
            ops = float(s.get("ops", 0) or 0)
            avg = float(s.get("avg", 0) or 0)
            return {"ops": ops, "avg": avg}
    except:
        return {}

def resolve_mlbam(espn_name: str) -> tuple[int | None, bool]:
    """Resolve ESPN player name to MLBAM ID via MLB Stats API search."""
    try:
        data    = mlb_get(f"{MLB_API}/people/search", {"names": espn_name, "hydrate": "currentTeam"})
        people  = data.get("people", [])
        if not people:
            return None, False
        p = people[0]
        pos = p.get("primaryPosition", {}).get("abbreviation", "")
        is_pitcher = pos in ("SP", "RP", "P")
        return p["id"], is_pitcher
    except:
        return None, False

def grade_letter(score: int) -> str:
    if score >= 90: return "A+"
    if score >= 85: return "A"
    if score >= 80: return "A-"
    if score >= 77: return "B+"
    if score >= 73: return "B"
    if score >= 70: return "B-"
    if score >= 67: return "C+"
    if score >= 63: return "C"
    if score >= 60: return "C-"
    if score >= 55: return "D+"
    if score >= 50: return "D"
    return "F"

def main():
    from espn_api.baseball import League

    league = League(league_id=1858112591, year=2026)
    req    = league.espn_request

    # Find the most recently COMPLETED matchup period
    # currentMatchupPeriod is the live one, so we want currentMatchupPeriod - 1
    current_mp = league.currentMatchupPeriod
    grading_mp = current_mp - 1
    if grading_mp < 1:
        print("[gmscore] season hasn't started yet", file=sys.stderr)
        sys.exit(1)

    # Get matchupPeriod -> scoringPeriod mapping from settings
    params = {"view": ["mSettings"]}
    data   = req.league_get(params=params)
    mp_map = data.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriods", {})
    scoring_periods = mp_map.get(str(grading_mp), [grading_mp])
    print(f"[gmscore] grading matchup period {grading_mp} (scoring periods: {scoring_periods})")

    # Load the schedule to find matchups for the grading period
    schedule_params = {"view": ["mMatchup", "mMatchupScore"], "scoringPeriodId": grading_mp}
    sched_data = req.league_get(params=schedule_params)
    schedule   = sched_data.get("schedule", [])
    matchups   = {e["id"]: e for e in schedule if e.get("matchupPeriodId") == grading_mp}
    print(f"[gmscore] found {len(matchups)} matchups for period {grading_mp}")

    # Get all teams roster for each scoring period in this matchup
    # We'll aggregate by player across all scoring periods
    team_player_pts: dict[int, dict[str, dict]] = defaultdict(dict)
    # team_id -> player_name -> {pts, slot, name}

    for sp in scoring_periods:
        for team in league.teams:
            params = {"view": ["mTeam", "mRoster"], "scoringPeriodId": sp, "forTeamId": team.team_id}
            try:
                tdata   = req.league_get(params=params)
                entries = tdata["teams"][0]["roster"]["entries"]
            except:
                continue

            for e in entries:
                pf   = e.get("playerPoolEntry", {})
                name = pf.get("player", {}).get("fullName", "?")
                pts  = float(pf.get("appliedStatTotal", 0) or 0)
                slot = e.get("lineupSlotId", BENCH_SLOT)
                pid  = pf.get("player", {}).get("id")

                key = name
                if key not in team_player_pts[team.team_id]:
                    team_player_pts[team.team_id][key] = {
                        "name":   name,
                        "pts":    0.0,
                        "slot":   slot,
                        "pid":    pid,
                        "days":   0,
                    }
                team_player_pts[team.team_id][key]["pts"]  += pts
                team_player_pts[team.team_id][key]["days"] += 1
                # Use the most common (or last) slot
                if slot not in INACTIVE_SLOTS:
                    team_player_pts[team.team_id][key]["slot"] = slot

    print(f"[gmscore] roster data loaded for {len(team_player_pts)} teams")

    # Build team scores
    results = []

    for team in league.teams:
        team_id = team.team_id
        players = list(team_player_pts.get(team_id, {}).values())
        if not players:
            continue

        starters = [p for p in players if p["slot"] not in INACTIVE_SLOTS]
        bench    = [p for p in players if p["slot"] == BENCH_SLOT]

        starter_pts = sum(p["pts"] for p in starters)
        bench_pts   = sum(p["pts"] for p in bench)
        total_pts   = starter_pts + bench_pts

        # ── Start/Sit score (40 pts) ──────────────────────────────────────────
        # Find bench players who outscored any starter
        if starters and bench:
            min_starter_pts = min(p["pts"] for p in starters)
            mistakes = [p for p in bench if p["pts"] > min_starter_pts and p["pts"] > 2]
            mistakes.sort(key=lambda p: -p["pts"])

            # Penalty per mistake, scaled by magnitude
            penalty = 0
            for m in mistakes:
                gap = m["pts"] - min_starter_pts
                penalty += min(gap * 1.2, 15)

            ss_score = max(0, 40 - penalty)
        else:
            ss_score = 30  # neutral if no bench
            mistakes = []

        # ── Win/Loss vs expected (20 pts) ─────────────────────────────────────
        # Find this team's matchup
        wl_score  = 10  # neutral baseline
        win       = False
        opp_name  = "?"
        opp_score = 0.0
        margin    = 0.0

        for mid, m in matchups.items():
            home_id = m.get("home", {}).get("teamId")
            away_id = m.get("away", {}).get("teamId")
            if home_id == team_id or away_id == team_id:
                opp_id    = away_id if home_id == team_id else home_id
                my_pts    = float(m.get("home" if home_id == team_id else "away", {}).get("totalPoints", total_pts) or total_pts)
                opp_pts   = float(m.get("away" if home_id == team_id else "home", {}).get("totalPoints", 0) or 0)
                win       = my_pts > opp_pts
                margin    = my_pts - opp_pts
                opp_team  = next((t for t in league.teams if t.team_id == opp_id), None)
                opp_name  = opp_team.team_name if opp_team else "?"
                opp_score = opp_pts

                if win:
                    wl_score = 20 if margin > 30 else 17 if margin > 10 else 14
                else:
                    wl_score = 5 if margin < -30 else 8 if margin < -10 else 10
                break

        # ── Roster quality score (30 pts) ─────────────────────────────────────
        # Sample up to 5 active players for sabermetric quality check
        # (skip full stat pull for speed - use projected_total_points as proxy)
        # Better: use ESPN's own season projections
        roster_score = 20  # default neutral

        # Use starter average pts per day as quality signal
        if starters:
            days = max(len(scoring_periods), 1)
            avg_pts_per_day = starter_pts / days / len(starters)
            # league average ~5-8 pts/player/day in World Sillies scoring
            LG_AVG_PPD = 6.5
            diff = (avg_pts_per_day - LG_AVG_PPD) / LG_AVG_PPD
            roster_score = max(0, min(30, round(20 + diff * 25)))

        # ── Waiver smarts (10 pts) ────────────────────────────────────────────
        # Check if recently added players (acquisitionType == 'WAIVER' or 'FREE_AGENT') contributed
        recent_adds = [p for p in starters
                       if p.get("acquisitionType") in ("WAIVER", "FREE_AGENT")
                       and p["pts"] > 5]
        waiver_score = min(10, 5 + len(recent_adds) * 2)

        # ── Total ─────────────────────────────────────────────────────────────
        total_score = round(ss_score + wl_score + roster_score + waiver_score)
        total_score = max(0, min(100, total_score))

        # Top scorer and biggest mistake
        top_starter  = max(starters, key=lambda p: p["pts"]) if starters else None
        best_bench   = max(bench,    key=lambda p: p["pts"]) if bench    else None

        results.append({
            "team_name":      team.team_name,
            "team_id":        team_id,
            "matchup_period": grading_mp,
            "total_score":    total_score,
            "grade":          grade_letter(total_score),
            "win":            win,
            "weekly_pts":     round(starter_pts, 1),
            "bench_pts":      round(bench_pts, 1),
            "opp_name":       opp_name,
            "opp_score":      round(opp_score, 1),
            "margin":         round(margin, 1),
            "ss_score":       round(ss_score, 1),
            "roster_score":   round(roster_score, 1),
            "wl_score":       round(wl_score, 1),
            "waiver_score":   round(waiver_score, 1),
            "top_starter":    top_starter["name"] if top_starter else None,
            "top_starter_pts": round(top_starter["pts"], 1) if top_starter else 0,
            "best_bench":     best_bench["name"] if best_bench else None,
            "best_bench_pts": round(best_bench["pts"], 1) if best_bench else 0,
            "mistakes":       [{"name": m["name"], "pts": round(m["pts"], 1)} for m in mistakes[:3]],
        })

    # Sort by total score descending
    results.sort(key=lambda x: -x["total_score"])

    out = {
        "generated":      datetime.datetime.now().isoformat(),
        "matchup_period": grading_mp,
        "scoring_periods": scoring_periods,
        "teams":          results,
    }

    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\n[gmscore] GM Report - Matchup Period {grading_mp}")
    print(f"{'RANK':4} {'GRADE':5} {'SCORE':5}  {'TEAM':30} {'W/L':4} {'PTS':6}")
    print("-" * 65)
    for i, t in enumerate(results, 1):
        wl = "W" if t["win"] else "L"
        print(f"{i:4} {t['grade']:5} {t['total_score']:5}  {t['team_name']:30} {wl:4} {t['weekly_pts']:6.1f}")
        if t["mistakes"]:
            for m in t["mistakes"][:2]:
                print(f"           !! benched {m['name']} ({m['pts']:.1f} pts)")

    print(f"\nok - {OUT_JSON}")

if __name__ == "__main__":
    main()
