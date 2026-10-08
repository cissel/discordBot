#!/usr/bin/env python3
# nbaToday.py - fetch today's NBA games from ESPN API

import requests
import pandas as pd
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

OUT = os.path.expanduser("~/discordBot/outputs/sports/nba/gamesToday.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# NOTE (Sep 2026): must pass an explicit dates= param. With no date param at
# all, ESPN's scoreboard endpoint falls back to returning the NEXT upcoming
# event (even weeks out, e.g. preseason) instead of an empty list when
# nothing is scheduled today - this silently mislabeled a future game as
# "today's" game during the off-season for months.
date_str = date.today().strftime("%Y%m%d")

resp = requests.get(
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    params={"dates": date_str},
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
    # Skip completed games - nothing actionable to display
    if status_desc in ("Final", "Final/OT", "Postponed", "Canceled"):
        continue
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
        time_str = f"{status_desc} @ {venue}"
    else:
        # Scheduled game - show actual tip-off time instead of just "Scheduled"
        game_time_utc = event.get("date") or competition.get("date")
        if game_time_utc:
            try:
                dt = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                dt_et = dt.astimezone(ET)
                tip_str = dt_et.strftime("%-I:%M %p ET")
            except Exception:
                tip_str = status_desc
        else:
            tip_str = status_desc
        time_str = f"{tip_str} @ {venue}"

    rows.append({"matchup": matchup, "time": time_str})

df = pd.DataFrame(rows, columns=["matchup", "time"])
df.to_csv(OUT, index=False)
print(f"Saved {len(df)} games to {OUT}")
print(df.to_string(index=False))