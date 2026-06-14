#!/usr/bin/env python3
"""
mlbMismatch.py
Reads probableStarters.csv, fetches each pitcher's Statcast data vs the
opposing team's batters (probable lineup → active roster fallback),
aggregates 5-year matchup stats, writes mismatch.csv sorted by OPS.
Uses ThreadPoolExecutor to parallelize Statcast fetches.
"""

import csv, sys, time, datetime, requests, pandas as pd
from io import StringIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Paths ─────────────────────────────────────────────────────────────────────
MLB_DIR = Path("~/discordBot/outputs/sports/mlb").expanduser()

# accepts optional arg: "today" or "tomorrow" (default: tomorrow)
_which          = sys.argv[1] if len(sys.argv) > 1 else "tomorrow"
STARTERS_CSV    = MLB_DIR / ("probableStartersToday.csv" if _which == "today" else "probableStarters.csv")
OUT_CSV         = MLB_DIR / ("mismatchToday.csv"         if _which == "today" else "mismatch.csv")
OUT_PITCHER_CSV = MLB_DIR / ("mismatchPitcherToday.csv"  if _which == "today" else "mismatchPitcher.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://baseballsavant.mlb.com/statcast_search",
}

MIN_PA       = 5
CURRENT_YEAR = datetime.date.today().year
YEARS        = list(range(CURRENT_YEAR - 4, CURRENT_YEAR + 1))

MLB_TEAM_IDS = {
    108,109,110,111,112,113,114,115,116,117,
    118,119,120,121,133,134,135,136,137,138,
    139,140,141,142,143,144,145,146,147,158
}

PA_EVENTS = {
    "single","double","triple","home_run",
    "field_out","strikeout","strikeout_double_play",
    "walk","intent_walk","hit_by_pitch",
    "sac_fly","sac_bunt","sac_fly_double_play",
    "force_out","grounded_into_double_play",
    "fielders_choice","fielders_choice_out",
    "double_play","triple_play","field_error","catcher_interf"
}
NON_AB = {
    "walk","intent_walk","hit_by_pitch",
    "sac_fly","sac_bunt","sac_fly_double_play","catcher_interf"
}

# ── 1. Load probable starters ─────────────────────────────────────────────────
def load_starters() -> list[dict]:
    if not STARTERS_CSV.exists():
        sys.exit(f"[ERROR] {STARTERS_CSV} not found - run mlbProbPitchers.py first.")
    with open(STARTERS_CSV) as f:
        return list(csv.DictReader(f))

# ── 2. Resolve MLBAM ID via Savant search ─────────────────────────────────────
_id_cache: dict = {}

def resolve_player_id(name: str) -> int | None:
    if name in _id_cache:
        return _id_cache[name]
    try:
        r = requests.get(
            f"https://baseballsavant.mlb.com/player/search-all?search={requests.utils.quote(name)}",
            headers=HEADERS, timeout=10
        )
        results = r.json()
        if results:
            pid = results[0]["id"]
            _id_cache[name] = pid
            return pid
    except Exception as e:
        print(f"  [WARN] ID lookup failed for '{name}': {e}")
    return None

# ── 3. Get opposing team's batters ────────────────────────────────────────────
_team_id_cache: dict = {}

def get_team_id_by_name(team_name: str) -> int | None:
    if team_name in _team_id_cache:
        return _team_id_cache[team_name]
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1/teams?sportId=1", timeout=10)
        for team in r.json().get("teams", []):
            if team["name"].lower() == team_name.lower():
                _team_id_cache[team_name] = team["id"]
                return team["id"]
    except Exception as e:
        print(f"  [WARN] Team lookup failed for '{team_name}': {e}")
    return None

def get_probable_lineup(team_id: int, game_date: str) -> list[dict]:
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&date={game_date}&teamId={team_id}&hydrate=lineups",
            timeout=10
        )
        for date_block in r.json().get("dates", []):
            for game in date_block.get("games", []):
                for side in ("homePlayers", "awayPlayers"):
                    players = game.get("lineups", {}).get(side, [])
                    if players:
                        return [
                            {"id": p["id"], "name": p.get("fullName", str(p["id"]))}
                            for p in players
                            if p.get("primaryPosition", {}).get("type") != "Pitcher"
                        ]
    except Exception as e:
        print(f"  [WARN] Lineup fetch failed: {e}")
    return []

def get_active_roster(team_id: int) -> list[dict]:
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=active",
            timeout=10
        )
        return [
            {"id": p["person"]["id"], "name": p["person"]["fullName"]}
            for p in r.json().get("roster", [])
            if p.get("position", {}).get("type") != "Pitcher"
        ]
    except Exception as e:
        print(f"  [WARN] Roster fetch failed: {e}")
    return []

def get_opposing_batters(opposing_team_name: str, game_date: str) -> list[dict]:
    team_id = get_team_id_by_name(opposing_team_name)
    if not team_id:
        return []
    lineup = get_probable_lineup(team_id, game_date)
    if lineup:
        print(f"  [INFO] Using probable lineup ({len(lineup)} batters)")
        return lineup
    print(f"  [INFO] No lineup posted - falling back to active roster")
    return get_active_roster(team_id)

# ── 4. Fetch one season of Statcast (used in thread pool) ────────────────────
def fetch_one_season(pitcher_id: int, year: int) -> pd.DataFrame | None:
    url = (
        "https://baseballsavant.mlb.com/statcast_search/csv"
        f"?all=true&hfSea={year}%7C&player_type=pitcher"
        f"&pitchers_lookup%5B%5D={pitcher_id}"
        f"&type=details&hfGT=R%7C&min_results=0&min_pas=0"
        f"&sort_col=pitches&sort_order=desc"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)  # hard cutoff per season
        r.raise_for_status()
        raw = r.text.strip()
        if not raw or raw.startswith("<!") or raw.startswith("{"):
            return None
        df = pd.read_csv(StringIO(raw), low_memory=False)
        if "events" not in df.columns:
            return None
        pa = df[df["events"].isin(PA_EVENTS)][["batter", "events"]].copy()
        return pa if len(pa) > 0 else None
    except Exception:
        return None

# ── 5. Pull all 5 seasons for a pitcher concurrently ─────────────────────────
_statcast_cache: dict = {}

def fetch_pitcher_statcast(pitcher_id: int, pitcher_name: str) -> pd.DataFrame:
    if pitcher_id in _statcast_cache:
        return _statcast_cache[pitcher_id]

    print(f"  [INFO] Fetching Statcast ({len(YEARS)} seasons in parallel)...")
    frames = []
    # Fire all 5 year requests simultaneously
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_one_season, pitcher_id, yr): yr for yr in YEARS}
        for future in as_completed(futures, timeout=40):  # skip pitcher if all years stall
            yr = futures[future]
            try:
                result = future.result()
            except Exception:
                result = None
            if result is not None:
                frames.append(result)
                print(f"    [OK] {yr}: {len(result)} PA events")
            else:
                print(f"    [--] {yr}: no data")

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["batter","events"])
    _statcast_cache[pitcher_id] = result
    print(f"  [INFO] {len(result)} total PA events for {pitcher_name}")
    return result

# ── 6. Aggregate stats for one pitcher vs one batter ─────────────────────────
def calc_matchup(pa_df: pd.DataFrame, batter_id: int) -> dict | None:
    ev = pa_df[pa_df["batter"] == batter_id]["events"]
    if len(ev) < MIN_PA:
        return None

    pa  = len(ev)
    ab  = (~ev.isin(NON_AB)).sum()
    h   = ev.isin({"single","double","triple","home_run"}).sum()
    bb  = ev.isin({"walk","intent_walk"}).sum()
    hbp = (ev == "hit_by_pitch").sum()
    sf  = ev.isin({"sac_fly","sac_fly_double_play"}).sum()
    hr  = (ev == "home_run").sum()
    k   = ev.isin({"strikeout","strikeout_double_play"}).sum()
    tb  = (ev.isin({"single"}).sum() * 1 +
           (ev == "double").sum()    * 2 +
           (ev == "triple").sum()    * 3 +
           hr                        * 4)

    avg = round(h / ab, 3)                        if ab > 0            else None
    obp = round((h+bb+hbp) / (ab+bb+hbp+sf), 3)  if (ab+bb+hbp+sf)>0 else None
    slg = round(tb / ab, 3)                        if ab > 0            else None
    ops = round((obp or 0) + (slg or 0), 3)

    return {"PA":pa,"AB":ab,"H":h,"HR":hr,"BB":bb,"K":k,"AVG":avg,"OBP":obp,"SLG":slg,"OPS":ops}

# ── 7. Aggregate pitcher score vs full opposing lineup ────────────────────────
def calc_pitcher_lineup_score(pa_df: pd.DataFrame, batters: list[dict]) -> dict | None:
    """
    Score a pitcher against every batter in the opposing lineup.
    Returns a composite dict, or None if fewer than 3 batters have >= MIN_PA history.

    Composite OPS-against is PA-weighted across all qualifying matchups so that
    a pitcher who has faced the 3-4-5 hitters 20 times each outweighs one who
    has only 5 PA against the 8-hole.
    """
    qualifying = []   # matchups that clear MIN_PA
    zero_pa    = 0    # batters with no history at all

    for batter in batters:
        ev = pa_df[pa_df["batter"] == batter["id"]]["events"]
        total_pa = len(ev)
        if total_pa == 0:
            zero_pa += 1
            continue
        if total_pa < MIN_PA:
            continue  # some history but too thin — skip, don't penalise

        ab  = (~ev.isin(NON_AB)).sum()
        h   = ev.isin({"single","double","triple","home_run"}).sum()
        bb  = ev.isin({"walk","intent_walk"}).sum()
        hbp = (ev == "hit_by_pitch").sum()
        sf  = ev.isin({"sac_fly","sac_fly_double_play"}).sum()
        hr  = (ev == "home_run").sum()
        k   = ev.isin({"strikeout","strikeout_double_play"}).sum()
        tb  = (ev.isin({"single"}).sum()       * 1 +
               (ev == "double").sum()          * 2 +
               (ev == "triple").sum()          * 3 +
               hr                             * 4)

        obp = (h + bb + hbp) / (ab + bb + hbp + sf) if (ab + bb + hbp + sf) > 0 else 0.0
        slg = tb / ab                                  if ab > 0               else 0.0
        ops = obp + slg

        qualifying.append({
            "name": batter["name"],
            "pa": total_pa,
            "ops": ops,
            "h": int(h), "hr": int(hr), "k": int(k), "bb": int(bb),
        })

    n_qual  = len(qualifying)
    n_total = len(batters)
    if n_qual < 3:
        return None   # not enough history to say anything meaningful

    # PA-weighted composite OPS-against
    total_pa_weight = sum(m["pa"] for m in qualifying)
    w_ops = sum(m["ops"] * m["pa"] for m in qualifying) / total_pa_weight

    # Best individual matchup (highest OPS-against = toughest for the pitcher)
    worst = max(qualifying, key=lambda m: m["ops"])
    # Best individual matchup for the pitcher (lowest OPS-against)
    best  = min(qualifying, key=lambda m: m["ops"])

    return {
        "coverage":       f"{n_qual}/{n_total}",   # "6/9"
        "n_qualifying":   n_qual,
        "n_total":        n_total,
        "zero_pa":        zero_pa,
        "composite_ops":  round(w_ops, 3),
        "total_pa":       total_pa_weight,
        "best_batter":    best["name"],
        "best_ops":       round(best["ops"], 3),
        "best_pa":        best["pa"],
        "worst_batter":   worst["name"],
        "worst_ops":      round(worst["ops"], 3),
        "worst_pa":       worst["pa"],
    }


# ── 8. Main ───────────────────────────────────────────────────────────────────
def main():
    if OUT_CSV.exists():
        OUT_CSV.unlink()
    if OUT_PITCHER_CSV.exists():
        OUT_PITCHER_CSV.unlink()

    starters = load_starters()
    print(f"[INFO] {len(starters)} probable starters - fetching Statcast in parallel...\n")

    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    game_date = datetime.date.today().strftime("%Y-%m-%d") if _which == "today" else tomorrow

    # ── Resolve all pitcher IDs upfront (fast, sequential) ───────────────────
    for entry in starters:
        entry["pitcher_id"] = resolve_player_id(entry["pitcher_name"])

    # ── Fetch Statcast for all pitchers concurrently (4 at a time) ───────────
    def fetch_entry(entry):
        pid = entry.get("pitcher_id")
        if not pid:
            return entry["pitcher_name"], pd.DataFrame(columns=["batter","events"])
        df = fetch_pitcher_statcast(pid, entry["pitcher_name"])
        return entry["pitcher_name"], df

    statcast_by_name: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fetch_entry, e): e for e in starters}
        for future in as_completed(futures):
            name, df = future.result()
            statcast_by_name[name] = df
            print(f"[DONE] Statcast loaded for {name}")

    # ── Process matchups ──────────────────────────────────────────────────────
    print("\n[INFO] Computing matchups...")
    all_rows         = []   # per-pair rows (batter-favored CSV)
    pitcher_rows     = []   # per-pitcher rows (pitcher-favored CSV)

    for entry in starters:
        pitcher_name = entry["pitcher_name"]
        pitcher_team = entry["pitcher_team"] if "pitcher_team" in entry else entry.get("team","?")
        matchup      = entry["matchup"]
        home_away    = entry["home_away"]

        away_team, home_team = [t.strip() for t in matchup.split("@")]
        opposing_team = home_team if home_away == "away" else away_team

        pa_df = statcast_by_name.get(pitcher_name, pd.DataFrame(columns=["batter","events"]))
        if pa_df.empty:
            continue

        batters = get_opposing_batters(opposing_team, game_date)
        if not batters:
            continue

        # ── Per-pair rows (existing batter-favored logic) ─────────────────
        for batter in batters:
            stats = calc_matchup(pa_df, batter["id"])
            if stats is None:
                continue
            all_rows.append({
                "pitcher":       pitcher_name,
                "pitcher_team":  pitcher_team,
                "batter":        batter["name"],
                "opposing_team": opposing_team,
                "matchup":       matchup,
                **stats
            })

        # ── Per-pitcher composite score (new pitcher-favored logic) ───────
        score = calc_pitcher_lineup_score(pa_df, batters)
        if score is None:
            print(f"  [SKIP] {pitcher_name}: <3 qualifying matchups vs {opposing_team}")
            continue
        pitcher_rows.append({
            "pitcher":        pitcher_name,
            "pitcher_team":   pitcher_team,
            "opposing_team":  opposing_team,
            "matchup":        matchup,
            **score,
        })
        print(f"  [SCORE] {pitcher_name} vs {opposing_team}: "
              f"composite OPS {score['composite_ops']} ({score['coverage']} batters, "
              f"{score['total_pa']} total PA)")

    # ── Write batter-favored CSV (sorted by OPS desc = batter best at top) ──
    if not all_rows:
        print("[WARN] No per-pair matchup data found.")
    else:
        all_rows.sort(key=lambda r: r["OPS"], reverse=True)
        fieldnames = [
            "pitcher","pitcher_team","batter","opposing_team","matchup",
            "PA","AB","H","HR","BB","K","AVG","OBP","SLG","OPS"
        ]
        with open(OUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\n[INFO] {len(all_rows)} batter-pair matchups written to {OUT_CSV}")
        print(f"\n[TOP 5 BATTER-FAVORED]")
        for r in all_rows[:5]:
            print(f"  {r['batter']} vs {r['pitcher']}: OPS {r['OPS']} ({r['H']}H {r['HR']}HR in {r['PA']}PA)")
        print(f"\n[TOP 5 PITCHER-FAVORED (individual pairs)]")
        for r in all_rows[-5:][::-1]:
            print(f"  {r['batter']} vs {r['pitcher']}: OPS {r['OPS']} ({r['H']}H {r['HR']}HR in {r['PA']}PA)")

    # ── Write pitcher-favored CSV (sorted by composite OPS asc = best pitcher) ──
    if not pitcher_rows:
        print("[WARN] No pitcher composite scores computed.")
    else:
        pitcher_rows.sort(key=lambda r: r["composite_ops"])
        fieldnames_p = [
            "pitcher","pitcher_team","opposing_team","matchup",
            "composite_ops","coverage","n_qualifying","n_total","zero_pa","total_pa",
            "best_batter","best_ops","best_pa",
            "worst_batter","worst_ops","worst_pa",
        ]
        with open(OUT_PITCHER_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_p)
            writer.writeheader()
            writer.writerows(pitcher_rows)
        print(f"\n[INFO] {len(pitcher_rows)} pitcher scores written to {OUT_PITCHER_CSV}")
        print(f"\n[TOP 5 PITCHER-FAVORED (composite lineup)]")
        for r in pitcher_rows[:5]:
            print(f"  {r['pitcher']} vs {r['opposing_team']}: "
                  f"OPS-against {r['composite_ops']} ({r['coverage']} batters, "
                  f"best: {r['best_batter']} {r['best_ops']})")

if __name__ == "__main__":
    main()