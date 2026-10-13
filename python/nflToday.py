"""
nflToday.py
Fetches today's NFL schedule from ESPN's public scoreboard API and writes
outputs/sports/nfl/gamesToday.csv with columns: matchup, time
(same convention as mlbToday.py / nhlToday.py / nbaToday.py so /ball and
/game watch can consume it uniformly - see gameWatch.py list_games_today).
"""

import csv, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

OUTPUT = Path("~/discordBot/outputs/sports/nfl/gamesToday.csv").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def fetch_today():
    today = datetime.now(ET).strftime("%Y%m%d")
    try:
        r = requests.get(ESPN_SCOREBOARD, params={"dates": today}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[nflToday] fetch error: {e}", file=sys.stderr)
        return None


def main():
    data = fetch_today()
    if not data:
        sys.exit(1)

    events = data.get("events", [])
    if not events:
        OUTPUT.write_text("matchup,time\n")
        print("[nflToday] no games today")
        return

    rows = []
    for e in events:
        comp = e["competitions"][0]
        teams = {}
        for c in comp["competitors"]:
            teams[c["homeAway"]] = c["team"].get("displayName", c["team"].get("abbreviation", "?"))
        away = teams.get("away", "?")
        home = teams.get("home", "?")
        matchup = f"{away} @ {home}"

        status = comp["status"]["type"]
        if status["state"] == "post":
            time_str = status.get("shortDetail", "Final")
        else:
            time_str = status.get("shortDetail", "TBD")

        rows.append({"matchup": matchup, "time": time_str})

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["matchup", "time"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[nflToday] wrote {len(rows)} games to {OUTPUT}")


if __name__ == "__main__":
    main()
