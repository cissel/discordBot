#!/usr/bin/env python3
# nbaToday.py - fetch today's NBA games from ESPN API

import requests
import pandas as pd
import os

OUT = os.path.expanduser("~/discordBot/outputs/sports/nba/gamesToday.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

resp = requests.get(
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    timeout=15
)
resp.raise_for_status()
events = resp.json().get("events", [])

if not events:
    print("No NBA games today.")
    pd.DataFrame(columns=["matchup", "time"]).to_csv(OUT, index=False)
    raise SystemExit(0)

rows = []
for event in events:
    status_desc = event["status"]["type"]["description"]
    competition  = event["competitions"][0]
    competitors  = competition["competitors"]

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

    if status_desc not in ("Scheduled", ""):
        try:
            away_score = int(away["score"])
            home_score = int(home["score"])
            matchup += f" - {away_score}-{home_score}"
        except (KeyError, ValueError):
            pass

    rows.append({"matchup": matchup, "time": f"{status_desc} @ {venue}"})

df = pd.DataFrame(rows)
df.to_csv(OUT, index=False)
print(f"Saved {len(df)} games to {OUT}")
print(df.to_string(index=False))