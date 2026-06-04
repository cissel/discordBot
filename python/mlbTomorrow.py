# mlbTomorrow.py
# Fetches tomorrow's MLB schedule from the free MLB Stats API and writes
# outputs/sports/mlb/gamesTomorrow.csv with columns: matchup, time

import csv, sys, urllib.request, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OUTPUT = Path("~/discordBot/outputs/sports/mlb/gamesTomorrow.csv").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")

def fetch_tomorrow():
    tomorrow = (datetime.now(ET) + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={tomorrow}&hydrate=teams,game(content(summary))")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[mlbTomorrow] fetch error: {e}", file=sys.stderr)
        return None

def main():
    data = fetch_tomorrow()
    if not data:
        sys.exit(1)

    dates = data.get("dates", [])
    if not dates:
        OUTPUT.write_text("matchup,time\n")
        print("[mlbTomorrow] no games tomorrow")
        return

    rows = []
    for game in dates[0].get("games", []):
        away = game["teams"]["away"]["team"].get("abbreviation") or game["teams"]["away"]["team"].get("name", "TBD")
        home = game["teams"]["home"]["team"].get("abbreviation") or game["teams"]["home"]["team"].get("name", "TBD")
        matchup = f"{away} @ {home}"

        detail = game.get("status", {}).get("detailedState", "")
        if detail in ("Postponed", "Cancelled", "Suspended"):
            time_str = detail
        else:
            game_time_utc = game.get("gameDate")
            if game_time_utc:
                try:
                    dt    = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
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

    print(f"[mlbTomorrow] wrote {len(rows)} games to {OUTPUT}")

if __name__ == "__main__":
    main()
