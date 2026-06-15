"""
worldSilliesMap.py
Dumps World Sillies PF/PA/W/L to CSV for the fantasy map R plot.
"""
import csv, os
from espn_api.baseball import League

LEAGUE_ID = 1858112591
YEAR      = 2026
CSV_PATH  = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/map.csv")

def fetch_map_data():
    league   = League(league_id=LEAGUE_ID, year=YEAR)
    standings = league.standings()

    pf = {t.team_id: 0.0 for t in standings}
    pa = {t.team_id: 0.0 for t in standings}

    for team in standings:
        for match in team.schedule:
            winner = getattr(match, 'winner', None)
            hfs = getattr(match, 'home_final_score', 0) or 0
            afs = getattr(match, 'away_final_score', 0) or 0
            # Include completed weeks AND current live week (UNDECIDED with scores > 0)
            if (winner in ('HOME', 'AWAY') or (winner == 'UNDECIDED' and (hfs > 0 or afs > 0))):
                if match.home_team == team and hfs > 0:
                    pf[team.team_id] += hfs
                    pa[team.team_id] += afs
                elif match.away_team == team and afs > 0:
                    pf[team.team_id] += afs
                    pa[team.team_id] += hfs

    rows = []
    for team in standings:
        tid = team.team_id
        rows.append({
            "team_name": team.team_name.strip(),
            "wins":      team.wins,
            "losses":    team.losses,
            "pf":        round(pf[tid], 1),
            "pa":        round(pa[tid], 1),
        })

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["team_name", "wins", "losses", "pf", "pa", "current_week"])
        writer.writeheader()
        for row in rows:
            row["current_week"] = league.currentMatchupPeriod
        writer.writerows(rows)

    print(f"Map data written to {CSV_PATH}")

if __name__ == "__main__":
    fetch_map_data()
