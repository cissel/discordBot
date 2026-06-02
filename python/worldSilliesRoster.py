import csv
from espn_api.baseball import League

LEAGUE_ID = 1858112591
YEAR      = 2026
CSV_PATH  = "~/discordBot/outputs/sports/mlb/fantasy/roster.csv"


def fetch_roster():
    league   = League(league_id=LEAGUE_ID, year=YEAR)
    matchups = league.box_scores()

    rows = []
    for matchup in matchups:
        for side, lineup in [("home", matchup.home_lineup), ("away", matchup.away_lineup)]:
            team_name = matchup.home_team.team_name if side == "home" else matchup.away_team.team_name
            for player in lineup:
                rows.append({
                    "team_name":      team_name,
                    "player_name":    player.name,
                    "slot_position":  player.slot_position,
                    "position":       player.position,
                    "points":         player.points,
                    "injury_status":  player.injuryStatus,
                })

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "team_name", "player_name", "slot_position", "position", "points", "injury_status"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Roster written to {CSV_PATH}")


if __name__ == "__main__":
    fetch_roster()