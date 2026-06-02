import csv
import requests
from datetime import datetime, timedelta

CSV_PATH = "~/discordBot/outputs/sports/mlb/probableStarters.csv"

def get_probable_starters_tomorrow():
    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={tomorrow}"
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
                    "game_date":    tomorrow,
                })

    # Sort alphabetically by pitcher name
    rows.sort(key=lambda r: r["pitcher_name"])
    return rows

if __name__ == "__main__":
    rows = get_probable_starters_tomorrow()
    print(f"Found {len(rows)} probable starters for tomorrow")
    for r in rows:
        print(f"  {r['pitcher_name']} ({r['team']}) — {r['matchup']}")

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "pitcher_name", "team", "matchup", "home_away", "game_date"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written to {CSV_PATH}")