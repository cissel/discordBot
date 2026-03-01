# mlbToday.py
# Fetches today's MLB schedule from the free MLB Stats API and writes
# outputs/sports/mlb/gamesToday.csv with columns: matchup, time
# (same format as nhlToday.py / nbaToday.R so /ball can consume it uniformly)

import csv, sys, urllib.request, json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OUTPUT = Path("~/discordBot/outputs/sports/mlb/gamesToday.csv").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")

def fetch_today():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    url   = (f"https://statsapi.mlb.com/api/v1/schedule"
             f"?sportId=1&date={today}&hydrate=teams,game(content(summary))")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[mlbToday] fetch error: {e}", file=sys.stderr)
        return None

def main():
    data = fetch_today()
    if not data:
        sys.exit(1)

    dates = data.get("dates", [])
    if not dates:
        # No games today — write empty file so caller knows we ran fine
        OUTPUT.write_text("matchup,time\n")
        print("[mlbToday] no games today")
        return

    rows = []
    for game in dates[0].get("games", []):
        away = game["teams"]["away"]["team"]["abbreviation"]
        home = game["teams"]["home"]["team"]["abbreviation"]
        matchup = f"{away} @ {home}"

        status = game.get("status", {}).get("abstractGameState", "")
        detail = game.get("status", {}).get("detailedState", "")

        if status == "Live":
            linescore = game.get("linescore", {})
            inning    = linescore.get("currentInning", "")
            half      = linescore.get("inningHalf", "")
            away_runs = game["teams"]["away"].get("score", 0)
            home_runs = game["teams"]["home"].get("score", 0)
            time_str  = f"🔴 LIVE  {away} {away_runs}–{home_runs} {home}  {half} {inning}"
        elif status == "Final":
            away_runs = game["teams"]["away"].get("score", "?")
            home_runs = game["teams"]["home"].get("score", "?")
            time_str  = f"Final: {away} {away_runs}–{home_runs} {home}"
        elif detail in ("Postponed", "Cancelled", "Suspended"):
            time_str = detail
        else:
            game_time_utc = game.get("gameDate")  # "2025-04-10T17:05:00Z"
            if game_time_utc:
                try:
                    dt  = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                    dt_et = dt.astimezone(ET)
                    time_str = dt_et.strftime("%-I:%M %p ET")
                except Exception:
                    time_str = game_time_utc
            else:
                time_str = "TBD"

        rows.append({"matchup": matchup, "time": time_str})

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["matchup", "time"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[mlbToday] wrote {len(rows)} games to {OUTPUT}")

if __name__ == "__main__":
    main()