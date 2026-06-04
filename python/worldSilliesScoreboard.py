import csv
import os
from espn_api.baseball import League

LEAGUE_ID = 1858112591
YEAR      = 2026
CSV_PATH  = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/scoreboard.csv")


def fetch_scoreboard():
    league   = League(league_id=LEAGUE_ID, year=YEAR)
    matchups = league.box_scores()

    rows = []
    for matchup in matchups:
        home_team  = matchup.home_team.team_name  if matchup.home_team  else "BYE"
        away_team  = matchup.away_team.team_name  if matchup.away_team  else "BYE"
        home_score = matchup.home_score           if matchup.home_score is not None else 0
        away_score = matchup.away_score           if matchup.away_score is not None else 0

        rows.append({
            "home_team":  home_team,
            "home_score": home_score,
            "away_team":  away_team,
            "away_score": away_score,
        })

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["home_team", "home_score", "away_team", "away_score"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Scoreboard written to {CSV_PATH}")


if __name__ == "__main__":
    fetch_scoreboard()
