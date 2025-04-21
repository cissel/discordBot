import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dateutil import parser
import csv
from pathlib import Path

def get_next_cape_kennedy_launch():
    url = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
    params = {
        'limit': 50,
        'ordering': 'window_start',
        'mode': 'detailed'
    }

    try:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            return f"🚨 Request failed with status code {response.status_code}"

        data = response.json()
        filtered = [
            launch for launch in data['results']
            if any(x in launch['pad']['location']['name'].lower() for x in ["cape canaveral", "kennedy"])
        ]

        if not filtered:
            return "🚀 No upcoming launches found at Cape Canaveral or Kennedy."

        filtered.sort(key=lambda x: x['window_start'])
        next_launch = filtered[0]

        # Parse UTC time
        launch_time_utc = parser.isoparse(next_launch['window_start']).astimezone(timezone.utc)

        # Convert to Eastern Time
        launch_time_et = launch_time_utc.astimezone(ZoneInfo("America/New_York"))

        # T-minus delta
        now = datetime.now(timezone.utc)
        delta = launch_time_utc - now
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        # Create output path
        output_path = Path("/Users/jamescissel/discordBot/outputs/space/next_launch.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write launch info to CSV
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["T-minus", "Name", "Window (UTC)", "Window (ET)", "Provider", "Pad"])
            writer.writerow([
                f"{days}d {hours}h {minutes}m {seconds}s",
                next_launch['name'],
                launch_time_utc.strftime("%Y-%m-%d %H:%M:%S %Z"),
                launch_time_et.strftime("%Y-%m-%d %I:%M %p %Z"),
                next_launch['launch_service_provider']['name'],
                f"{next_launch['pad']['name']} ({next_launch['pad']['location']['name']})"
            ])

        # Print the path for confirmation
        print(output_path.resolve())

    except Exception as e:
        return f"⚠️ Error fetching launch data: {e}"

# Run it
if __name__ == "__main__":
    print(get_next_cape_kennedy_launch())
