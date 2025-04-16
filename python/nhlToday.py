from nhlpy import NHLClient
from datetime import datetime
from dateutil import tz
import csv

client = NHLClient()
today = datetime.today().strftime('%Y-%m-%d')
schedule = client.schedule.get_schedule(date=today)

with open("/Users/jamescissel/discordBot/outputs/sports/nhl/gamesToday.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["time", "matchup"])

    if not schedule['games']:
        print("No games today.")
    else:
        for game in schedule['games']:
            # Build matchup string
            away = f"{game['awayTeam']['placeName']['default']} {game['awayTeam']['commonName']['default']}"
            home = f"{game['homeTeam']['placeName']['default']} {game['homeTeam']['commonName']['default']}"
            matchup = f"{away} @ {home}"

            # Local time
            utc = datetime.strptime(game['startTimeUTC'], "%Y-%m-%dT%H:%M:%SZ")
            local = utc.replace(tzinfo=tz.UTC).astimezone(tz.tzlocal())
            time_str = local.strftime("%-I:%M %p %Z")  # macOS-friendly

            # Venue
            venue = game['venue']['default']
            time_with_venue = f"{time_str} @ {venue}"

            writer.writerow([time_with_venue, matchup])

