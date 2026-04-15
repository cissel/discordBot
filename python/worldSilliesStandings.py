import csv
from espn_api.baseball import League

LEAGUE_ID = 1858112591  # Replace with your actual league ID
YEAR = 2026         # Replace with the current season year
CSV_PATH = "/Users/jamescissel/discordBot/outputs/sports/mlb/fantasy/standings.csv"
 
def fetch_standings():
    league = League(league_id=LEAGUE_ID, year=YEAR)
    standings = league.standings()
 
    rows = []
    for i, team in enumerate(standings, start=1):
        owner = ', '.join(f"{o['firstName']} {o['lastName']}" for o in team.owners) if team.owners else 'N/A'
        rows.append({
            "rank":      i,
            "team_name": team.team_name,
            "owner":     owner,
            "wins":      team.wins,
            "losses":    team.losses,
            "ties":      team.ties,
        })
 
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "team_name", "owner", "wins", "losses", "ties"])
        writer.writeheader()
        writer.writerows(rows)
 
    print(f"Standings written to {CSV_PATH}")
 
if __name__ == "__main__":
    fetch_standings()