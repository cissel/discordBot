from nhlpy import NHLClient
from datetime import datetime, timedelta
from dateutil import tz
import csv 
import os

client = NHLClient()
team_name = "Florida Panthers"
today = datetime.today()

OUTPUT_PATH = "/Users/jamescissel/discordBot/outputs"
csv_path = os.path.join(OUTPUT_PATH, "sports/nhl/nextTeamGame.csv")

# Gather games for the next 7 days
games = []
for i in range(7):
    date_str = (today + timedelta(days=i)).strftime("%Y-%m-%d")
    try:
        data = client.schedule.get_schedule(date=date_str)
        games.extend(data['games'])
    except:
        pass

next_game = None
for game in games:
    game_time = datetime.strptime(game['startTimeUTC'], "%Y-%m-%dT%H:%M:%SZ")

    # ✅ Use correct structure for team names
    home_team = f"{game['homeTeam']['placeName']['default']} {game['homeTeam']['commonName']['default']}"
    away_team = f"{game['awayTeam']['placeName']['default']} {game['awayTeam']['commonName']['default']}"

    print("DEBUG: checking matchup:")
    print("Home:", home_team)
    print("Away:", away_team)

    if team_name.lower() in [home_team.lower(), away_team.lower()]:
        next_game = game
        break

if not next_game:
    print("No upcoming games found for that team.")
else:
    # Convert to local time
    utc = datetime.strptime(next_game['startTimeUTC'], "%Y-%m-%dT%H:%M:%SZ")
    local = utc.replace(tzinfo=tz.UTC).astimezone(tz.tzlocal())
    time_str = local.strftime("%A, %B %d @ %-I:%M %p %Z")

    # Format matchup and venue
    away = f"{next_game['awayTeam']['placeName']['default']} {next_game['awayTeam']['commonName']['default']}"
    home = f"{next_game['homeTeam']['placeName']['default']} {next_game['homeTeam']['commonName']['default']}"
    matchup = f"{away} @ {home}"
    venue = next_game['venue']['default']
    time_with_venue = f"{time_str} @ {venue}"

    # Make sure output directory exists
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "matchup"])
        writer.writerow([time_str, matchup])

    print("✅ Wrote next game to CSV:", matchup)
