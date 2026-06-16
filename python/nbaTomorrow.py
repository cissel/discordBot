#!/usr/bin/env python3
# nbaTomorrow.py - fetch tomorrow's NBA games from ESPN API

import requests
import pandas as pd
import os
import sys
from datetime import date, timedelta

OUT = os.path.expanduser("~/discordBot/outputs/sports/nba/gamesTomorrow.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

tomorrow = date.today() + timedelta(days=1)
date_str = tomorrow.strftime("%Y%m%d")

try:
    resp = requests.get(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        params={"dates": date_str},
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("events", [])
except Exception as e:
    print(f"nbaTomorrow: API error: {e}")
    pd.DataFrame(columns=["matchup", "time"]).to_csv(OUT, index=False)
    sys.exit(0)

if not events:
    print("No NBA games tomorrow.")
    pd.DataFrame(columns=["matchup", "time"]).to_csv(OUT, index=False)
    sys.exit(0)

rows = []
for event in events:
    status_desc = event["status"]["type"]["description"]
    # Skip already-final games
    if status_desc in ("Final", "Final/OT", "Postponed", "Canceled"):
        continue
    competition = event["competitions"][0]
    competitors = competition["competitors"]

    away = next(c for c in competitors if c["homeAway"] == "away")
    home = next(c for c in competitors if c["homeAway"] == "home")

    away_name   = away["team"]["displayName"]
    home_name   = home["team"]["displayName"]
    away_record = away.get("records", [{}])[0].get("summary", "")
    home_record = home.get("records", [{}])[0].get("summary", "")
    venue       = competition.get("venue", {}).get("fullName", "")

    away_str = f"{away_name} ({away_record})" if away_record else away_name
    home_str = f"{home_name} ({home_record})" if home_record else home_name
    matchup  = f"{away_str} @ {home_str}"

    rows.append({"matchup": matchup, "time": f"{status_desc} @ {venue}"})

df = pd.DataFrame(rows, columns=["matchup", "time"])
df.to_csv(OUT, index=False)
print(f"Saved {len(df)} games to {OUT}")
