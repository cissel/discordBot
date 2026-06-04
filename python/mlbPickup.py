#!/usr/bin/env python3
"""
mlbPickup.py - World Sillies free agent pickup recommendations
Scans the FA pool, scores every player using the same sabermetric model
as mlbCompare.py, and surfaces the top pickups for today or tomorrow.

Usage: python3 mlbPickup.py [today|tomorrow] [batters|pitchers|all]
Writes: ~/discordBot/outputs/sports/mlb/fantasy/pickup.json
"""

import sys, os, json, datetime, requests, unicodedata, csv
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── reuse scoring logic from mlbCompare ───────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from mlbCompare import (
    search_player,
    get_batter_stats, get_pitcher_stats,
    get_last_n_games_batter, get_last_n_games_pitcher,
    get_matchup, get_pitcher_opponent,
    score_batter_stream, score_batter_roster,
    score_pitcher_stream, score_pitcher_roster,
    stream_signal, roster_signal,
    norm,
)

BASE        = Path(os.path.expanduser("~/discordBot"))
FANTASY_DIR = BASE / "outputs/sports/mlb/fantasy"
FA_CSV      = FANTASY_DIR / "freeagents.csv"
OUT_JSON    = FANTASY_DIR / "pickup.json"

CURRENT_YEAR = datetime.date.today().year
MLB_API      = "https://statsapi.mlb.com/api/v1"
HEADERS      = {"User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)"}

def get(url, params=None, timeout=8):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def load_free_agents(mode: str) -> list[dict]:
    """Load and filter FA list from ESPN CSV."""
    if not FA_CSV.exists():
        print(f"[pickup] no freeagents.csv - run worldSilliesFA.py first", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(FA_CSV) as f:
        for row in csv.DictReader(f):
            if row.get("injury_status", "") in ("FIFTEEN_DAY_DL", "SIXTY_DAY_DL", "SUSPENSION"):
                continue  # skip injured/suspended
            pos = row.get("position", "").upper()
            is_pitcher = pos in ("SP", "RP")
            if mode == "batters" and is_pitcher:
                continue
            if mode == "pitchers" and not is_pitcher:
                continue
            rows.append(row)
    return rows

def score_player(fa_row: dict, day: str) -> dict | None:
    """Score a single free agent. Returns result dict or None on failure."""
    name = fa_row["player_name"]
    pos  = fa_row.get("position", "").upper()
    is_pitcher = pos in ("SP", "RP")
    pct_owned  = float(fa_row.get("percent_owned", 0) or 0)
    proj_pts   = float(fa_row.get("projected_total_points", 0) or 0)

    try:
        info = search_player(name)
        if not info:
            return None

        pid  = info["id"]
        team = info.get("team", fa_row.get("pro_team", ""))

        if is_pitcher:
            season = get_pitcher_stats(pid)
            recent = get_last_n_games_pitcher(pid, 14)
            opp    = get_pitcher_opponent(info["fullName"], day) or get_pitcher_opponent(name, day)
            starting = fa_row.get("starting_tomorrow", "False") == "True" if day == "tomorrow" else True
            ss, sr = score_pitcher_stream(recent, opp)
            rs, rr = score_pitcher_roster(season)

            # bonus for confirmed starter
            if starting and is_pitcher and pos == "SP":
                ss = min(100, ss + 8)
                sr.insert(0, "✅ confirmed starter")

            return {
                "name":           info["fullName"],
                "pos":            pos,
                "team":           team,
                "pct_owned":      pct_owned,
                "proj_pts":       proj_pts,
                "is_pitcher":     True,
                "starting":       starting,
                "opponent":       opp,
                "stream_score":   ss,
                "roster_score":   rs,
                "stream_signal":  stream_signal(ss),
                "roster_signal":  roster_signal(rs),
                "stream_reasons": sr[:3],
                "roster_reasons": rr[:3],
                "season_era":     season.get("era"),
                "season_whip":    season.get("whip"),
                "season_k9":      season.get("k9"),
                "recent_era":     recent.get("era"),
                "recent_whip":    recent.get("whip"),
            }
        else:
            season  = get_batter_stats(pid)
            recent  = get_last_n_games_batter(pid, 14)
            matchup = get_matchup(info["fullName"], day) or get_matchup(name, day)
            ss, sr  = score_batter_stream(recent, season, matchup)
            rs, rr  = score_batter_roster(season)

            return {
                "name":             info["fullName"],
                "pos":              pos,
                "team":             team,
                "pct_owned":        pct_owned,
                "proj_pts":         proj_pts,
                "is_pitcher":       False,
                "stream_score":     ss,
                "roster_score":     rs,
                "stream_signal":    stream_signal(ss),
                "roster_signal":    roster_signal(rs),
                "stream_reasons":   sr[:3],
                "roster_reasons":   rr[:3],
                "season_ops":       season.get("ops"),
                "season_avg":       season.get("avg"),
                "season_iso":       season.get("iso"),
                "season_hr":        season.get("hr"),
                "season_sb":        season.get("sb"),
                "recent_ops":       recent.get("ops"),
                "matchup_pitcher":  matchup["pitcher"]     if matchup else None,
                "matchup_ops":      matchup["matchup_ops"] if matchup else None,
            }

    except Exception as e:
        print(f"  [warn] {name}: {e}", file=sys.stderr)
        return None

def main():
    args = sys.argv[1:]
    day  = "today"
    mode = "all"
    for a in args:
        if a in ("today", "tomorrow"):
            day = a
        elif a in ("batters", "pitchers", "all"):
            mode = a

    print(f"[pickup] scanning FAs for {day} ({mode})")

    fas = load_free_agents(mode)
    print(f"[pickup] {len(fas)} eligible free agents to score")

    # score all FAs in parallel - cap at 8 workers to be polite
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(score_player, fa, day): fa["player_name"] for fa in fas}
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            done += 1
            try:
                result = future.result()
                if result:
                    results.append(result)
                    print(f"  [{done}/{len(fas)}] {name}: stream={result['stream_score']} roster={result['roster_score']}")
                else:
                    print(f"  [{done}/{len(fas)}] {name}: skipped")
            except Exception as e:
                print(f"  [{done}/{len(fas)}] {name}: error - {e}", file=sys.stderr)

    # split and rank
    batters  = sorted([r for r in results if not r["is_pitcher"]], key=lambda x: -(x["stream_score"] + x["roster_score"]))
    pitchers = sorted([r for r in results if r["is_pitcher"]],     key=lambda x: -(x["stream_score"] + x["roster_score"]))

    top_batters  = batters[:5]
    top_pitchers = pitchers[:5]

    out = {
        "day":          day,
        "mode":         mode,
        "generated":    datetime.datetime.now().isoformat(),
        "top_batters":  top_batters,
        "top_pitchers": top_pitchers,
        "total_scored": len(results),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\n[pickup] TOP BATTER PICKUPS ({day})")
    for i, p in enumerate(top_batters[:3], 1):
        print(f"  {i}. {p['name']} ({p['team']}) - stream {p['stream_score']} / roster {p['roster_score']}")
        for r in p["stream_reasons"]:
            print(f"     {r}")

    print(f"\n[pickup] TOP PITCHER PICKUPS ({day})")
    for i, p in enumerate(top_pitchers[:3], 1):
        print(f"  {i}. {p['name']} ({p['team']}) - stream {p['stream_score']} / roster {p['roster_score']}")
        for r in p["stream_reasons"]:
            print(f"     {r}")

    print(f"\nok - {len(results)} players scored, written to {OUT_JSON}")

if __name__ == "__main__":
    main()
