#!/usr/bin/env python3
"""
worldSilliesSzn.py
Fetches each team's cumulative fantasy points by day for the full season
from a single ESPN mMatchupScore API call.

Outputs:
  ~/discordBot/outputs/sports/mlb/fantasy/szn_daily.csv
    columns: team_id, team_name, wins, losses, pf, playoff_seed, scoring_period, date, daily_pts

Usage:
  python worldSilliesSzn.py [--force]
"""

import csv
import datetime
import sys
import time
from pathlib import Path

import requests

LEAGUE_ID    = 1858112591
YEAR         = 2026
SEASON_START = datetime.date(2026, 3, 27)

BASE    = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
           f"/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)"}

OUT_DIR  = Path("~/discordBot/outputs/sports/mlb/fantasy").expanduser()
OUT_CSV  = OUT_DIR / "szn_daily.csv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 7200  # 2 hours


def period_to_date(p: int) -> datetime.date:
    return SEASON_START + datetime.timedelta(days=p - 1)


def fetch() -> None:
    r = requests.get(
        BASE,
        params={"view": ["mMatchupScore", "mStandings", "mTeam"]},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()

    # Build team metadata
    team_meta: dict[int, dict] = {}
    for t in d.get("teams", []):
        tid     = t["id"]
        name    = t.get("name", "?").strip()
        overall = t.get("record", {}).get("overall", {})
        team_meta[tid] = {
            "name":         name,
            "wins":         overall.get("wins", 0),
            "losses":       overall.get("losses", 0),
            "pf":           overall.get("pointsFor", 0.0),
            "playoff_seed": t.get("playoffSeed", 99),
        }

    # Collect pointsByScoringPeriod per team from schedule
    team_daily: dict[int, dict[int, float]] = {tid: {} for tid in team_meta}
    for s in d.get("schedule", []):
        for side in ("home", "away"):
            sd  = s.get(side, {})
            tid = sd.get("teamId")
            if tid and tid in team_daily:
                for sp_str, pts in sd.get("pointsByScoringPeriod", {}).items():
                    team_daily[tid][int(sp_str)] = pts

    # Write CSV
    rows = []
    for tid, meta in team_meta.items():
        for sp, pts in sorted(team_daily[tid].items()):
            rows.append({
                "team_id":       tid,
                "team_name":     meta["name"],
                "wins":          meta["wins"],
                "losses":        meta["losses"],
                "pf":            round(meta["pf"], 1),
                "playoff_seed":  meta["playoff_seed"],
                "scoring_period": sp,
                "date":          period_to_date(sp).isoformat(),
                "daily_pts":     round(pts, 2),
            })

    rows.sort(key=lambda r: (r["playoff_seed"], r["scoring_period"]))

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "team_id", "team_name", "wins", "losses", "pf",
            "playoff_seed", "scoring_period", "date", "daily_pts",
        ])
        w.writeheader()
        w.writerows(rows)

    n_teams = len(team_meta)
    n_rows  = len(rows)
    print(f"[szn] wrote {n_rows} rows ({n_teams} teams) to {OUT_CSV}")


def main() -> None:
    force = "--force" in sys.argv

    if not force and OUT_CSV.exists():
        age = time.time() - OUT_CSV.stat().st_mtime
        if age < CACHE_TTL:
            print(f"[szn] cache fresh ({age / 3600:.1f}h old) - skipping fetch")
            return

    fetch()


if __name__ == "__main__":
    main()
