import csv
import sys
import requests
from datetime import datetime, timedelta
from espn_api.baseball import League
import unicodedata
import os

LEAGUE_ID = 1858112591
YEAR      = 2026
CSV_PATH  = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/freeagents.csv")

VALID_POSITIONS = ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP", "PP"]

def normalize(name):
    return unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii").strip().lower()

def get_probable_starters_tomorrow():
    """Returns a set of pitcher full names starting tomorrow."""
    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={tomorrow}"
        f"&hydrate=probablePitcher"
        f"&fields=dates,date,games,teams,home,away,probablePitcher,fullName"
    )
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        starters = set()
        for date_block in data.get("dates", []):
            for game in date_block.get("games", []):
                for side in ("home", "away"):
                    pitcher = game["teams"][side].get("probablePitcher")
                    if pitcher:
                        starters.add(normalize(pitcher["fullName"]))
        return starters
    except Exception as e:
        print(f"WARNING: couldn't fetch probable starters: {e}")
        return set()

def fetch_free_agents(position: str, size: int = 50):
    position = position.upper().strip()
    if position not in VALID_POSITIONS:
        print(f"ERROR: invalid position '{position}'. Valid: {VALID_POSITIONS}")
        sys.exit(1)

    starters_tomorrow = set()
    if position in ("SP", "RP", "PP"):
        starters_tomorrow = get_probable_starters_tomorrow()
        print(f"Found {len(starters_tomorrow)} probable starters tomorrow")
        print("DEBUG starters tomorrow:", sorted(starters_tomorrow))

    # PP is just SP under the hood — fetch SP free agents then filter
    espn_position = "SP" if position == "PP" else position
    league = League(league_id=LEAGUE_ID, year=YEAR)
    agents = league.free_agents(size=size*4, position=espn_position)  # fetch more so we have enough after filtering

    rows = []
    for p in agents:
        starting_tomorrow = normalize(p.name) in starters_tomorrow
        if position == "PP" and not starting_tomorrow:
            continue  # PP mode: skip anyone not starting tomorrow
        rows.append({
            "player_name":            p.name,
            "position":               p.position,
            "pro_team":               p.proTeam,
            "percent_owned":          p.percent_owned,
            "projected_total_points": p.projected_total_points,
            "injury_status":          p.injuryStatus,
            "starting_tomorrow":      starting_tomorrow,
        })
        if len(rows) == size:
            break  # cap at requested size

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "player_name", "position", "pro_team",
            "percent_owned", "projected_total_points",
            "injury_status", "starting_tomorrow"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Free agents written to {CSV_PATH}")


if __name__ == "__main__":
    pos = sys.argv[1] if len(sys.argv) > 1 else "SP"
    fetch_free_agents(pos)