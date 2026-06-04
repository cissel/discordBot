from nhlpy import NHLClient
from datetime import datetime, timedelta
from dateutil import tz
import csv, os

client = NHLClient()
tomorrow = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')
schedule = client.schedule.get_schedule(date=tomorrow)

out_path = os.path.expanduser("~/discordBot/outputs/sports/nhl/gamesTomorrow.csv")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["time", "matchup"])

    if not schedule['games']:
        print("no hockey tomorrow")
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