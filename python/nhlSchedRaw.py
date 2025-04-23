from nhlpy import NHLClient
from datetime import datetime
import csv

client = NHLClient()
today = datetime.today().strftime('%Y-%m-%d')
schedule = client.schedule.get_schedule(date=today)

output_path = "/Users/jamescissel/discordBot/outputs/sports/nhl/nhlSchedRaw.csv"

with open(output_path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["start_time_utc", "home_team", "away_team", "venue"])

    for game in schedule["games"]:
        start_time = game.get("startTimeUTC", "")
        home = f"{game['homeTeam']['placeName']['default']} {game['homeTeam']['commonName']['default']}"
        away = f"{game['awayTeam']['placeName']['default']} {game['awayTeam']['commonName']['default']}"
        venue = game.get("venue", {}).get("default", "Unknown Arena")

        writer.writerow([start_time, home, away, venue])

print("✅ Dumped raw NHL schedule to CSV.")
