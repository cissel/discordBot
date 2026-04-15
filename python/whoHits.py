#!/usr/bin/env python3
"""
pitcher_vs_hitters.py
Usage: python3 pitcher_vs_hitters.py "Sandy Alcantara" /path/to/outputs
"""

import sys, os, time, datetime, requests, pandas as pd
from io import StringIO
from pathlib import Path

# ── 0. Args ───────────────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    sys.exit("Usage: python3 pitcher_vs_hitters.py \"First Last\" [/path/to/outputs]")

pitcher_name = sys.argv[1].strip()
out_base     = sys.argv[2].strip() if len(sys.argv) >= 3 else str(Path.cwd() / "outputs")
out_dir      = Path(out_base) / "sports" / "mlb"
out_dir.mkdir(parents=True, exist_ok=True)
out_file     = out_dir / "top10_vs_pitcher.csv"

# Clear any previous result so a failed run never serves stale data
if out_file.exists():
    out_file.unlink()

print(f"[INFO] Pitcher: {pitcher_name}")
print(f"[INFO] Output:  {out_file}")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://baseballsavant.mlb.com/statcast_search",
}

# ── 1. Resolve MLBAM ID ───────────────────────────────────────────────────────
print("[INFO] Resolving player ID...")
resp = requests.get(
    f"https://baseballsavant.mlb.com/player/search-all?search={requests.utils.quote(pitcher_name)}",
    headers=HEADERS, timeout=15
)
resp.raise_for_status()
players = resp.json()
if not players:
    sys.exit(f"[ERROR] No player found for '{pitcher_name}'.")

player   = players[0]
mlbam_id = player["id"]
print(f"[INFO] Found: {player['name']} (ID: {mlbam_id}, Team: {player.get('name_display_club','?')})")

# ── 2. Fetch Statcast CSV per season ─────────────────────────────────────────
PA_EVENTS = {
    "single","double","triple","home_run",
    "field_out","strikeout","strikeout_double_play",
    "walk","intent_walk","hit_by_pitch",
    "sac_fly","sac_bunt","sac_fly_double_play",
    "force_out","grounded_into_double_play",
    "fielders_choice","fielders_choice_out",
    "double_play","triple_play","field_error","catcher_interf"
}

current_year = datetime.date.today().year

def fetch_season(year: int) -> pd.DataFrame | None:
    url = (
        "https://baseballsavant.mlb.com/statcast_search/csv"
        f"?all=true&hfSea={year}%7C&player_type=pitcher"
        f"&pitchers_lookup%5B%5D={mlbam_id}"
        f"&type=details&hfGT=R%7C&min_results=0&min_pas=0"
        f"&sort_col=pitches&sort_order=desc"
    )
    print(f"[INFO] Fetching {year}...", end=" ", flush=True)
    t0 = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        raw = r.text.strip()
        if not raw or raw.startswith("<!") or raw.startswith("{"):
            print(f"no data")
            return None
        df = pd.read_csv(StringIO(raw), low_memory=False)
        if "events" not in df.columns:
            print(f"no events column")
            return None
        pa = df[df["events"].isin(PA_EVENTS)][["batter", "events"]].copy()
        print(f"{len(pa)} PA events ({time.time()-t0:.1f}s)")
        return pa if len(pa) > 0 else None
    except Exception as e:
        print(f"error: {e}")
        return None

frames = [fetch_season(yr) for yr in range(current_year - 4, current_year + 1)]
frames = [f for f in frames if f is not None]

if not frames:
    sys.exit(f"[ERROR] No Statcast data found for '{pitcher_name}'.")

pa = pd.concat(frames, ignore_index=True)
print(f"[INFO] Total PA events: {len(pa)} across {pa['batter'].nunique()} batters")

# ── 3. Aggregate per batter — stats only, no names yet ───────────────────────
NON_AB = {"walk","intent_walk","hit_by_pitch","sac_fly","sac_bunt",
           "sac_fly_double_play","catcher_interf"}

def agg_batter(grp):
    ev  = grp["events"]
    ab  = (~ev.isin(NON_AB)).sum()
    h   = ev.isin({"single","double","triple","home_run"}).sum()
    bb  = ev.isin({"walk","intent_walk"}).sum()
    hbp = (ev == "hit_by_pitch").sum()
    sf  = ev.isin({"sac_fly","sac_fly_double_play"}).sum()
    hr  = (ev == "home_run").sum()
    d   = (ev == "double").sum()
    t   = (ev == "triple").sum()
    tb  = ev.isin({"single"}).sum()*1 + d*2 + t*3 + hr*4
    pa_n = len(ev)
    avg = round(h/ab, 3)                        if ab > 0            else None
    obp = round((h+bb+hbp)/(ab+bb+hbp+sf), 3)  if (ab+bb+hbp+sf)>0 else None
    slg = round(tb/ab, 3)                        if ab > 0            else None
    ops = round((obp or 0)+(slg or 0), 3)
    return pd.Series({"PA":pa_n,"AB":ab,"H":h,"2B":d,"3B":t,"HR":hr,
                       "BB":bb,"HBP":hbp,"AVG":avg,"OBP":obp,"SLG":slg,"OPS":ops})

stats = (
    pa.groupby("batter")
      .apply(agg_batter, include_groups=False)
      .reset_index()
)

# Filter and sort — take top 30 candidates so we still have 10 after
# filtering out minor leaguers / retired players
candidates = (
    stats[stats["PA"] >= 5]
      .sort_values(["OPS","AVG"], ascending=False)
      .head(30)
      .reset_index(drop=True)
)

if len(candidates) == 0:
    sys.exit("[ERROR] No batters met the minimum 5 PA threshold.")

# ── 4. Look up player info, filter to MLB-only, take top 10 ──────────────────
print(f"[INFO] Looking up player info for up to {len(candidates)} candidates...")

# The 30 MLB team IDs — anything outside this is MiLB, independent, or foreign
MLB_TEAM_IDS = {
    108,109,110,111,112,113,114,115,116,117,
    118,119,120,121,133,134,135,136,137,138,
    139,140,141,142,143,144,145,146,147,158
}

def get_player_info(mlbam_batter_id: int) -> tuple | None:
    """
    Returns (full_name, current_team_name) if the player is on an active MLB roster,
    or None if they are in the minors, retired, or inactive.
    """
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{int(mlbam_batter_id)}"
            f"?hydrate=currentTeam",
            timeout=5
        )
        person       = r.json()["people"][0]
        name         = person.get("fullName", str(int(mlbam_batter_id)))
        current_team = person.get("currentTeam", {})
        team_id      = current_team.get("id")
        team_name    = current_team.get("name", "Unknown")

        if team_id not in MLB_TEAM_IDS:
            print(f"[INFO] Filtering out {name} — not on an MLB roster (team={team_name}, id={team_id})")
            return None

        return name, team_name
    except:
        return None

MAX_NAME_LEN = 20  # characters before abbreviating

def abbreviate(full_name: str) -> str:
    """'Vladimir Guerrero Jr.' → 'V. Guerrero Jr.' — only if name exceeds MAX_NAME_LEN"""
    if len(full_name) <= MAX_NAME_LEN:
        return full_name
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name
    first = parts[0]
    rest  = parts[1:]
    abbreviated = f"{first[0]}. {' '.join(rest)}"
    if len(abbreviated) > MAX_NAME_LEN:
        abbreviated = abbreviated[:MAX_NAME_LEN - 1] + "…"
    return abbreviated

# Resolve player info and filter to MLB-only
rows = []
for _, row in candidates.iterrows():
    info = get_player_info(row["batter"])
    if info is None:
        continue
    full_name, team = info
    row = row.copy()
    row["Batter"] = abbreviate(full_name)
    row["Team"]   = team
    rows.append(row)
    if len(rows) == 10:
        break

if not rows:
    sys.exit("[ERROR] No active MLB batters found in matchup data.")

top10 = pd.DataFrame(rows).reset_index(drop=True)

print(f"[INFO] Active MLB batters in top 10: {len(top10)}")
print(top10[["Batter","Team","PA","AVG","OBP","SLG","OPS"]].to_string(index=False))

# ── 5. Write CSV ──────────────────────────────────────────────────────────────
out_cols = ["Batter","Team","PA","AB","H","2B","3B","HR","BB","HBP","AVG","OBP","SLG","OPS"]
top10[out_cols].to_csv(out_file, index=False)
print(f"[INFO] Written: {out_file}")
print(f"[DONE] {pitcher_name}")