#!/usr/bin/env python3
"""
pgaSeasonStandings.py
Fetches PGA Tour season standings from ESPN API.
Writes outputs/sports/pga/season_standings.csv

Columns: rank, name, fedex_pts, earnings, scoring_avg, wins, top10s
"""

import csv, sys, json, urllib.request
from pathlib import Path

OUT_DIR = Path("~/discordBot/outputs/sports/pga").expanduser()
OUT_CSV = OUT_DIR / "season_standings.csv"
OUT_DIR.mkdir(parents=True, exist_ok=True)

URL     = "http://site.api.espn.com/apis/site/v2/sports/golf/pga/statistics?season=2026&category=FedExCupPoints"
HEADERS = {"User-Agent": "discordBot/1.0"}

def fetch():
    req = urllib.request.Request(URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[pgaSeasonStandings] fetch error: {e}", file=sys.stderr)
        return None

def main():
    data = fetch()
    if not data:
        sys.exit(1)

    cats = data.get("stats", {}).get("categories", [])
    cat_map = {c["name"]: c for c in cats}

    # index each category's leaders by athlete id for easy lookup
    def leaders_by_id(cat_name):
        cat = cat_map.get(cat_name, {})
        return {l["athlete"]["id"]: l for l in cat.get("leaders", [])}

    fedex   = leaders_by_id("cupPoints")
    money   = leaders_by_id("officialAmount")
    scoring = leaders_by_id("scoringAverage")
    wins    = leaders_by_id("wins")
    top10   = leaders_by_id("topTenFinishes")

    # Use FedEx Cup points as the primary ordering
    ranked = sorted(fedex.values(), key=lambda x: -x["value"])[:15]

    rows = []
    for i, entry in enumerate(ranked, start=1):
        aid  = entry["athlete"]["id"]
        name = entry["athlete"]["displayName"]
        pts  = entry["displayValue"]
        earn = money.get(aid,   {}).get("displayValue", "-")
        avg  = scoring.get(aid, {}).get("displayValue", "-")
        w    = wins.get(aid,    {}).get("displayValue", "0")
        t10  = top10.get(aid,   {}).get("displayValue", "0")
        rows.append({
            "rank":        i,
            "name":        name,
            "fedex_pts":   pts,
            "earnings":    earn,
            "scoring_avg": avg,
            "wins":        w,
            "top10s":      t10,
        })

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank","name","fedex_pts","earnings","scoring_avg","wins","top10s"])
        w.writeheader()
        w.writerows(rows)

    print(f"[pgaSeasonStandings] wrote {len(rows)} players to {OUT_CSV}")

if __name__ == "__main__":
    main()
