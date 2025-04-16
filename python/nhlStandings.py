from nhlpy import NHLClient
import csv

client = NHLClient()
standings = client.standings.get_standings()

# Group teams by conference and division
grouped = {}

for team in standings["standings"]:
    conf = team["conferenceName"]
    div = team["divisionName"]
    key = f"{conf} - {div}"

    if key not in grouped:
        grouped[key] = []

    grouped[key].append({
        "team": team["teamName"]["default"],
        "points": team["points"],
        "wins": team["wins"],
        "losses": team["losses"],
        "ot_losses": team["otLosses"],
        "games_played": team["gamesPlayed"],
        "goal_diff": team["goalDifferential"],
        "streak": f"{team['streakCode']}{team['streakCount']}"
    })

# Write to CSV
output_path = "/Users/jamescissel/discordBot/outputs/sports/nhl/standings.csv"

with open(output_path, "w", newline="") as f:
    writer = csv.writer(f)

    for group in sorted(grouped):
        writer.writerow([group])  # section header
        writer.writerow(["Team", "Pts", "W", "L", "OTL", "GP", "GD", "Streak"])

        # Sort teams in each group by points (descending)
        sorted_teams = sorted(grouped[group], key=lambda x: x["points"], reverse=True)

        for team in sorted_teams:
            writer.writerow([
                team["team"],
                team["points"],
                team["wins"],
                team["losses"],
                team["ot_losses"],
                team["games_played"],
                team["goal_diff"],
                team["streak"]
            ])

        writer.writerow([])  # blank row between divisions

print(f"Standings written to {output_path}")
