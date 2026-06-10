"""
mlbOwnership.py
Builds a merged CSV of MLB fantasy players with ownership % + season fantasy pts.
- Free agents: pulled fresh from ESPN via worldSilliesFA for all positions
- Rostered players: pulled directly from ESPN API with real percent_owned values
Output: outputs/sports/mlb/fantasy/ownership.csv
Columns: player_name, position, pct_owned, fantasy_pts, avg_pts_per_game, team, type (FA/Rostered)
"""

import csv
import os
import subprocess
import sys
import unicodedata
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))

BASE     = os.path.expanduser("~/discordBot")
FA_ALL   = os.path.join(BASE, "outputs/sports/mlb/fantasy/freeagents_all.csv")
FA_EACH  = os.path.join(BASE, "outputs/sports/mlb/fantasy/freeagents.csv")
BAT_SUM  = os.path.join(BASE, "outputs/sports/mlb/fantasy/playerData/batter_season_summary.csv")
PTCH_SUM = os.path.join(BASE, "outputs/sports/mlb/fantasy/playerData/pitcher_season_summary.csv")
OUT_CSV  = os.path.join(BASE, "outputs/sports/mlb/fantasy/ownership.csv")
FA_PY    = os.path.join(BASE, "python/worldSilliesFA.py")
PYTHON   = sys.executable

POSITIONS = ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP"]

def normalize(name):
    return unicodedata.normalize("NFD", str(name)).encode("ascii", "ignore").decode("ascii").strip().lower()

# ── 1. refresh FA data for all positions ──────────────────────────────────────
import pandas as pd

frames = []
for pos in POSITIONS:
    try:
        subprocess.run([PYTHON, FA_PY, pos, "100"], capture_output=True, timeout=30)
        if os.path.exists(FA_EACH):
            frames.append(pd.read_csv(FA_EACH))
    except Exception as e:
        print(f"  FA fetch failed for {pos}: {e}", flush=True)

if frames:
    fa_df = pd.concat(frames).drop_duplicates(subset=["player_name"])
    fa_df.to_csv(FA_ALL, index=False)
    print(f"FA pool: {len(fa_df)} players", flush=True)
else:
    fa_df = pd.read_csv(FA_ALL) if os.path.exists(FA_ALL) else pd.DataFrame()
    print("Using cached FA data", flush=True)

# ── 2. fetch rostered players with real percent_owned from ESPN ───────────────
from espn_api.baseball import League

rostered = []
try:
    league = League(
        league_id=1858112591, year=2026,
        espn_s2=os.getenv("ESPN_S2"), swid=os.getenv("ESPN_SWID")
    )
    for team in league.teams:
        for p in team.roster:
            rostered.append({
                "player_name": p.name,
                "position":    p.position,
                "team":        p.proTeam,
                "pct_owned":   round(float(p.percent_owned or 0), 2),
            })
    print(f"Rostered players: {len(rostered)}", flush=True)
except Exception as e:
    print(f"ESPN roster fetch failed: {e}", flush=True)

# ── 3. load season summary stats ──────────────────────────────────────────────
bat_stats  = {}
if os.path.exists(BAT_SUM):
    for row in csv.DictReader(open(BAT_SUM)):
        bat_stats[normalize(row["player_name"])] = row

ptch_stats = {}
if os.path.exists(PTCH_SUM):
    for row in csv.DictReader(open(PTCH_SUM)):
        ptch_stats[normalize(row["player_name"])] = row

def get_stats(name, position):
    key = normalize(name)
    if position in {"SP", "RP"}:
        s = ptch_stats.get(key, {})
    else:
        s = bat_stats.get(key, {})
    return float(s.get("fantasy_pts", 0) or 0), float(s.get("avg_pts_per_game", 0) or 0)

# ── 4. build rows ──────────────────────────────────────────────────────────────
rows = []

# Free agents
for _, fa in fa_df.iterrows():
    name = str(fa.get("player_name", "") or "")
    pos  = str(fa.get("position", "") or "")
    if not name:
        continue
    fpts, avg = get_stats(name, pos)
    rows.append({
        "player_name":      name,
        "position":         pos,
        "team":             str(fa.get("pro_team", "") or ""),
        "pct_owned":        round(float(fa.get("percent_owned", 0) or 0), 2),
        "fantasy_pts":      round(fpts, 1),
        "avg_pts_per_game": round(avg, 2),
        "type":             "FA",
    })

# Rostered players with real ESPN ownership %
seen = {normalize(r["player_name"]) for r in rows}
for p in rostered:
    name = str(p.get("player_name", "") or "")
    pos  = str(p.get("position", "") or "")
    if not name or normalize(name) in seen:
        continue
    fpts, avg = get_stats(name, pos)
    rows.append({
        "player_name":      name,
        "position":         pos,
        "team":             p.get("team", ""),
        "pct_owned":        p["pct_owned"],
        "fantasy_pts":      round(fpts, 1),
        "avg_pts_per_game": round(avg, 2),
        "type":             "Rostered",
    })
    seen.add(normalize(name))

# ── 5. write output ────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
fieldnames = ["player_name", "position", "team", "pct_owned", "fantasy_pts", "avg_pts_per_game", "type"]
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f"Written {len(rows)} players to {OUT_CSV}", flush=True)
