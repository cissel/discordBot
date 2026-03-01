# pgaLeaderboard.py
# Fetches live PGA Tour leaderboard from the ESPN hidden API.
# No API key required.
#
# Writes two files:
#   outputs/sports/pga/leaderboard.csv   — top 10 players
#   outputs/sports/pga/tournament.csv    — tournament metadata
#
# leaderboard.csv columns:  position, name, score, today, thru, round
# tournament.csv columns:   name, course, city, state, round, status, detail

import csv, sys, json, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

OUT_DIR    = Path("~/discordBot/outputs/sports/pga").expanduser()
OUT_LB     = OUT_DIR / "leaderboard.csv"
OUT_TOURN  = OUT_DIR / "tournament.csv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCOREBOARD_URL = "http://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
HEADERS        = {"User-Agent": "discordBot/1.0"}

def fetch():
    req = urllib.request.Request(SCOREBOARD_URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[pgaLeaderboard] fetch error: {e}", file=sys.stderr)
        return None

def score_str(val):
    """Convert raw score integer to display string (E, -3, +2, etc.)"""
    if val is None:
        return "–"
    try:
        n = int(val)
        if n == 0:
            return "E"
        return f"+{n}" if n > 0 else str(n)
    except (ValueError, TypeError):
        return str(val)

def main():
    data = fetch()
    if not data:
        sys.exit(1)

    events = data.get("events", [])
    if not events:
        # No active tournament this week
        OUT_TOURN.write_text("name,course,city,state,round,status,detail\n")
        OUT_LB.write_text("position,name,score,today,thru,round\n")
        print("[pgaLeaderboard] no active PGA event")
        return

    # Grab first event (there's only ever one PGA event at a time)
    event = events[0]
    tourn_name = event.get("name", "PGA Tour Event")
    competitions = event.get("competitions", [])

    if not competitions:
        OUT_TOURN.write_text("name,course,city,state,round,status,detail\n")
        OUT_LB.write_text("position,name,score,today,thru,round\n")
        print("[pgaLeaderboard] no competition data")
        return

    comp = competitions[0]

    # Tournament metadata
    venue     = comp.get("venue", {})
    course    = venue.get("fullName", "")
    city      = venue.get("address", {}).get("city", "")
    state     = venue.get("address", {}).get("state", "")
    status    = comp.get("status", {})
    status_type   = status.get("type", {})
    status_desc   = status_type.get("description", "")   # "In Progress", "Scheduled", "Final"
    status_detail = status.get("displayClock", "")        # e.g. "Round 3"
    status_short  = status_type.get("shortDetail", "")   # e.g. "R3 - In Progress"

    with open(OUT_TOURN, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name","course","city","state","round","status","detail"])
        w.writeheader()
        w.writerow({
            "name":   tourn_name,
            "course": course,
            "city":   city,
            "state":  state,
            "round":  status_detail,
            "status": status_desc,
            "detail": status_short,
        })

    # Leaderboard — competitors are already sorted by position
    competitors = comp.get("competitors", [])

    rows = []
    for comp_entry in competitors[:10]:
        athlete   = comp_entry.get("athlete", {})
        name      = athlete.get("displayName", "Unknown")
        stats     = comp_entry.get("statistics", [])

        # ESPN golf stats list varies — index by name
        stat_map  = {s["name"]: s.get("displayValue", "–") for s in stats}

        position  = comp_entry.get("status", {}).get("position", {}).get("displayName", "–")
        score     = score_str(comp_entry.get("score"))

        # "today" = score for current round, "thru" = holes completed
        today     = stat_map.get("scoreToPar", stat_map.get("score", "–"))
        thru      = stat_map.get("holesPlayed", comp_entry.get("linescores") and "18" or "–")

        # Simpler fallback: grab from linescores if stats are sparse
        linescores = comp_entry.get("linescores", [])
        current_round = len(linescores)

        rows.append({
            "position": position,
            "name":     name,
            "score":    score,
            "today":    today,
            "thru":     str(thru),
            "round":    str(current_round),
        })

    with open(OUT_LB, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["position","name","score","today","thru","round"])
        w.writeheader()
        w.writerows(rows)

    print(f"[pgaLeaderboard] wrote {len(rows)} players — {tourn_name} ({status_short})")

if __name__ == "__main__":
    main()