import csv
import os
import requests
import sys
from datetime import datetime, timedelta

CSV_PATH_TOMORROW = os.path.expanduser("~/discordBot/outputs/sports/mlb/probableStarters.csv")
CSV_PATH_TODAY    = os.path.expanduser("~/discordBot/outputs/sports/mlb/probableStartersToday.csv")

def get_probable_starters(date_str: str) -> list[dict]:
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={date_str}"
        f"&hydrate=probablePitcher"
        f"&fields=dates,date,games,teams,home,away,probablePitcher,fullName,team,name,gameDate"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            home_team = game["teams"]["home"].get("team", {}).get("name", "?")
            away_team = game["teams"]["away"].get("team", {}).get("name", "?")
            matchup   = f"{away_team} @ {home_team}"

            for side in ("home", "away"):
                pitcher = game["teams"][side].get("probablePitcher")
                if not pitcher:
                    continue
                rows.append({
                    "pitcher_name": pitcher["fullName"],
                    "team":         game["teams"][side].get("team", {}).get("name", "?"),
                    "matchup":      matchup,
                    "home_away":    side,
                    "game_date":    date_str,
                })

    rows.sort(key=lambda r: r["pitcher_name"])
    return rows

def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "pitcher_name", "team", "matchup", "home_away", "game_date"
        ])
        writer.writeheader()
        writer.writerows(rows)

if __name__ == "__main__":
    # accepts optional arg: "today" or "tomorrow" (default: tomorrow)
    which = sys.argv[1] if len(sys.argv) > 1 else "tomorrow"

    if which == "today":
        date_str = datetime.today().strftime("%Y-%m-%d")
        csv_path = CSV_PATH_TODAY
    else:
        date_str = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        csv_path = CSV_PATH_TOMORROW

    rows = get_probable_starters(date_str)
    print(f"Found {len(rows)} probable starters for {which} ({date_str})")
    for r in rows:
        print(f"  {r['pitcher_name']} ({r['team']}) - {r['matchup']}")

    write_csv(rows, csv_path)
    print(f"Written to {csv_path}")
