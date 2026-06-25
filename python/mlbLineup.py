#!/usr/bin/env python3
"""
mlbLineup.py  -  Daily start/sit card for dock ellis fan club (World Sillies)
Writes ~/discordBot/outputs/sports/mlb/fantasy/lineup.json

Each active roster slot gets:
  - recent_avg   : avg fantasy pts last 7 games (from game logs)
  - season_avg   : full season avg fantasy pts
  - trend_pct    : (recent - season) / |season| * 100
  - matchup_ops  : historical OPS vs today's probable pitcher (from mismatch csv)
  - matchup_pa   : PA sample size for that matchup
  - score        : composite 0-100 start score
  - signal       : START / SIT / WATCH
  - signal_reason: short human-readable reason
  - opponent_pitcher: name of today's probable pitcher (batters) or opponent team (pitchers)
  - injury_status
"""

import os, sys, json, csv, subprocess, unicodedata
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

import sys as _sys
_sys.path.insert(0, str(BASE / "python"))
try:
    from predictFantasy import get_ml_scores as _get_ml_scores
except Exception:
    _get_ml_scores = None

# ── paths ──────────────────────────────────────────────────────────────────────
BASE        = Path(os.path.expanduser("~/discordBot"))
PYTHON      = str(BASE / "venv/bin/python3")
PP          = str(BASE / "python")
OP          = str(BASE / "outputs")

ROSTER_CSV      = BASE / "outputs/sports/mlb/fantasy/roster.csv"
MISMATCH_CSV    = BASE / "outputs/sports/mlb/mismatch.csv"
MISMATCH_TODAY  = BASE / "outputs/sports/mlb/mismatchToday.csv"
STARTERS_CSV    = BASE / "outputs/sports/mlb/probableStartersToday.csv"
BATTER_LOGS     = BASE / "outputs/sports/mlb/fantasy/playerData/batter_game_logs.csv"
PITCHER_LOGS    = BASE / "outputs/sports/mlb/fantasy/playerData/pitcher_game_logs.csv"
OUT_JSON        = BASE / "outputs/sports/mlb/fantasy/lineup.json"

MY_TEAM = "dock ellis fan club"

BATTER_SLOTS  = {"C","1B","2B","3B","SS","OF","UTIL"}
PITCHER_SLOTS = {"SP","RP","P"}
BENCH_SLOTS   = {"BE","IL"}

# ── helpers ────────────────────────────────────────────────────────────────────
def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").strip().lower()

def run(script, *args):
    """Run a python helper script, ignore errors."""
    try:
        subprocess.run([PYTHON, os.path.join(PP, script), *args],
                       check=False, timeout=120)
    except Exception as e:
        print(f"  [warn] {script}: {e}", file=sys.stderr)

def refresh_data(day="today"):
    """Freshen the CSVs we depend on."""
    print("  refreshing roster...")
    run("worldSilliesRoster.py")
    print("  refreshing probable starters...")
    run("mlbProbPitchers.py", day)
    # only regenerate mismatch if stale (>3h)
    mcsv = MISMATCH_TODAY if day == "today" else MISMATCH_CSV
    needs = True
    if mcsv.exists():
        age = (datetime.now() - datetime.fromtimestamp(mcsv.stat().st_mtime)).total_seconds()
        if age < 10800:
            needs = False
    if needs:
        print("  refreshing mismatch data (this takes ~30s)...")
        run("mlbProbPitchers.py", day)
        run("mlbMismatch.py", day)
    else:
        print("  mismatch cache fresh, skipping.")

# ── load game logs ─────────────────────────────────────────────────────────────
def load_logs():
    bl = pd.read_csv(BATTER_LOGS)  if BATTER_LOGS.exists()  else pd.DataFrame()
    pl = pd.read_csv(PITCHER_LOGS) if PITCHER_LOGS.exists() else pd.DataFrame()
    if not bl.empty:
        bl["game_date"] = pd.to_datetime(bl["game_date"], errors="coerce")
    if not pl.empty:
        pl["game_date"] = pd.to_datetime(pl["game_date"], errors="coerce")
    return bl, pl

def player_trend(name, logs, n_recent=7):
    """Return (season_avg, recent_avg, trend_pct, games_total, games_recent)."""
    if logs.empty:
        return None, None, None, 0, 0
    matches = logs[logs["player_name"].apply(norm) == norm(name)]
    if matches.empty:
        # fuzzy: last name match
        last = norm(name).split()[-1]
        matches = logs[logs["player_name"].apply(lambda x: norm(x).split()[-1]) == last]
    if matches.empty:
        return None, None, None, 0, 0
    matches = matches.sort_values("game_date")
    recent  = matches.tail(n_recent)
    s_avg   = round(float(matches["fantasy_pts"].mean()), 2)
    r_avg   = round(float(recent["fantasy_pts"].mean()),  2)
    trend   = round((r_avg - s_avg) / max(abs(s_avg), 0.01) * 100, 1)
    return s_avg, r_avg, trend, len(matches), len(recent)

# ── load mismatch data ─────────────────────────────────────────────────────────
def load_mismatch(day="today"):
    path = MISMATCH_TODAY if day == "today" else MISMATCH_CSV
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["batter_norm"]  = df["batter"].apply(norm)
    df["pitcher_norm"] = df["pitcher"].apply(norm)
    return df

# ── load probable starters ─────────────────────────────────────────────────────
def load_starters():
    """Returns dict: norm(team_name) -> pitcher_name"""
    if not STARTERS_CSV.exists():
        return {}
    out = {}
    with open(STARTERS_CSV) as f:
        for row in csv.DictReader(f):
            # key by matchup string so we can look up by opponent team
            out[norm(row["team"])] = row["pitcher_name"]
    return out

# ── scoring ────────────────────────────────────────────────────────────────────
def score_batter(season_avg, recent_avg, trend_pct, matchup_ops, matchup_pa, injured):
    """Return 0-100 composite start score.
    Calibrated so a league-average batter (2.5 pts/g) with neutral trend
    and no matchup data scores ~50 (WATCH territory).
    """
    if injured:
        return 0

    # base: season avg - 2.5 pts/g = 50, 4+ pts/g = 80
    base = min((season_avg or 0) / 4.0, 1.0) * 60

    # trend component: ±15 points
    t = max(min((trend_pct or 0), 100), -100)
    trend_score = (t / 100) * 15

    # matchup component: ±25 points  (OPS > .800 = batter favored)
    if matchup_ops is not None and matchup_pa and matchup_pa >= 5:
        ops_norm = max(min((matchup_ops - 0.650) / 0.850, 1.0), -1.0)
        matchup_score = ops_norm * 25
    else:
        matchup_score = 0  # no data = neutral

    total = base + trend_score + matchup_score
    return max(0, min(100, round(total)))

def score_pitcher(season_avg, recent_avg, trend_pct, injured):
    """Return 0-100 composite start score for pitchers.
    Calibrated so a league-average SP (12 pts/g) with neutral trend scores ~50.
    """
    if injured:
        return 0
    # base: 12 pts/g = 50, 20+ pts/g = 80
    base        = min((season_avg or 0) / 20.0, 1.0) * 65
    t           = max(min((trend_pct or 0), 100), -100)
    trend_score = (t / 100) * 20
    total       = base + trend_score
    return max(0, min(100, round(total)))

def signal_from_score(score):
    if score >= 65:  return "START ✅"
    if score >= 42:  return "WATCH 👀"
    return "SIT ❌"

def reason_batter(season_avg, recent_avg, trend_pct, matchup_ops, matchup_pa, injured, pitcher_name):
    if injured:
        return "Injured - keep on bench"
    parts = []
    if recent_avg is not None:
        arrow = "🔥" if (trend_pct or 0) >= 15 else ("❄️" if (trend_pct or 0) <= -15 else "➡️")
        parts.append(f"{arrow} {recent_avg:.1f} pts/g last 7 (season {season_avg:.1f})")
    if matchup_ops is not None and matchup_pa and matchup_pa >= 5:
        label = "favored" if matchup_ops >= 0.800 else ("neutral" if matchup_ops >= 0.600 else "tough matchup")
        parts.append(f"vs {pitcher_name}: OPS {matchup_ops:.3f} in {matchup_pa} PA ({label})")
    elif pitcher_name:
        parts.append(f"vs {pitcher_name}: no matchup history")
    return " · ".join(parts) if parts else "No data"

def reason_pitcher(season_avg, recent_avg, trend_pct, injured, opponent):
    if injured:
        return "Injured - keep on bench"
    parts = []
    if recent_avg is not None:
        arrow = "🔥" if (trend_pct or 0) >= 15 else ("❄️" if (trend_pct or 0) <= -15 else "➡️")
        parts.append(f"{arrow} {recent_avg:.1f} pts/g last 7 (season {season_avg:.1f})")
    if opponent:
        parts.append(f"vs {opponent}")
    return " · ".join(parts) if parts else "No data"

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "today"

    print(f"[lineup] refreshing data for {day}...")
    refresh_data(day)

    # load roster
    if not ROSTER_CSV.exists():
        print("ERROR: roster.csv not found", file=sys.stderr)
        sys.exit(1)

    roster_df = pd.read_csv(ROSTER_CSV)
    my_roster = roster_df[roster_df["team_name"].str.strip().str.lower() == MY_TEAM.lower()]
    if my_roster.empty:
        print(f"ERROR: team '{MY_TEAM}' not found in roster.csv", file=sys.stderr)
        sys.exit(1)

    # load supporting data
    batter_logs, pitcher_logs = load_logs()
    mismatch   = load_mismatch(day)
    starters   = load_starters()

    # ML scores
    _ml_bat = _get_ml_scores("batters", ("daily", "weekly")) if _get_ml_scores else {}
    _ml_pit = _get_ml_scores("pitchers", ("daily", "weekly")) if _get_ml_scores else {}

    results = {"batters": [], "pitchers": [], "bench": [], "generated": datetime.now().isoformat()}

    for _, row in my_roster.iterrows():
        name     = row["player_name"]
        slot     = row["slot_position"]
        pos      = row["position"]
        pts      = float(row["points"]) if pd.notna(row.get("points")) else 0.0
        inj_raw  = str(row.get("injury_status","ACTIVE")).upper()
        injured  = inj_raw not in ("ACTIVE","NORMAL","")

        is_bench = slot in BENCH_SLOTS

        # ── batters ────────────────────────────────────────────────────────────
        if slot in BATTER_SLOTS or (is_bench and slot not in PITCHER_SLOTS and pos not in ("SP","RP","P")):
            s_avg, r_avg, trend, g_tot, g_rec = player_trend(name, batter_logs)

            # find today's opposing pitcher
            pitcher_name = None
            matchup_ops  = None
            matchup_pa   = None

            # look through mismatch for this batter
            if not mismatch.empty:
                hits = mismatch[mismatch["batter_norm"] == norm(name)]
                if not hits.empty:
                    best = hits.sort_values("PA", ascending=False).iloc[0]
                    pitcher_name = best["pitcher"]
                    matchup_ops  = float(best["OPS"])
                    matchup_pa   = int(best["PA"])

            # fallback: get pitcher from starters by team
            if pitcher_name is None:
                # we don't know batter's team from mismatch easily - skip
                pass

            sc = score_batter(s_avg, r_avg, trend, matchup_ops, matchup_pa, injured)
            sig = signal_from_score(sc) if not is_bench else ("BENCHED" if not injured else "SIT ❌")

            entry = {
                "name":             name,
                "slot":             slot,
                "position":         pos,
                "injury_status":    inj_raw,
                "season_avg":       s_avg,
                "recent_avg":       r_avg,
                "trend_pct":        trend,
                "games_total":      g_tot,
                "games_recent":     g_rec,
                "matchup_ops":      matchup_ops,
                "matchup_pa":       matchup_pa,
                "opponent_pitcher": pitcher_name,
                "score":            sc,
                "signal":           sig,
                "today_pts":        pts,
                "reason":           reason_batter(s_avg, r_avg, trend, matchup_ops, matchup_pa, injured, pitcher_name),
                "ml_pts_daily":     _ml_bat.get(norm(name), {}).get("ml_pts_daily"),
                "ml_pts_weekly":    _ml_bat.get(norm(name), {}).get("ml_pts_weekly"),
            }
            if is_bench:
                results["bench"].append(entry)
            else:
                results["batters"].append(entry)

        # ── pitchers ───────────────────────────────────────────────────────────
        elif slot in PITCHER_SLOTS or (is_bench and pos in ("SP","RP","P")):
            s_avg, r_avg, trend, g_tot, g_rec = player_trend(name, pitcher_logs)

            # find opponent from starters csv (pitcher's team)
            opponent = None
            if STARTERS_CSV.exists():
                with open(STARTERS_CSV) as f:
                    for srow in csv.DictReader(f):
                        if norm(srow["pitcher_name"]) == norm(name):
                            # opponent is the other team in matchup string
                            matchup = srow["matchup"]  # "Team A @ Team B"
                            parts   = matchup.replace(" @ ", " vs ").split(" vs ")
                            pitcher_team = srow["team"]
                            opponent = next((p.strip() for p in parts if norm(p.strip()) != norm(pitcher_team)), None)
                            break

            sc  = score_pitcher(s_avg, r_avg, trend, injured)
            sig = signal_from_score(sc) if not is_bench else "BENCHED"

            entry = {
                "name":             name,
                "slot":             slot,
                "position":         pos,
                "injury_status":    inj_raw,
                "season_avg":       s_avg,
                "recent_avg":       r_avg,
                "trend_pct":        trend,
                "games_total":      g_tot,
                "games_recent":     g_rec,
                "opponent":         opponent,
                "score":            sc,
                "signal":           sig,
                "today_pts":        pts,
                "reason":           reason_pitcher(s_avg, r_avg, trend, injured, opponent),
                "ml_pts_daily":     _ml_pit.get(norm(name), {}).get("ml_pts_daily"),
                "ml_pts_weekly":    _ml_pit.get(norm(name), {}).get("ml_pts_weekly"),
            }
            if is_bench:
                results["bench"].append(entry)
            else:
                results["pitchers"].append(entry)

    # sort active slots by score descending
    results["batters"].sort(key=lambda x: -x["score"])
    results["pitchers"].sort(key=lambda x: -x["score"])

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(results, f, indent=2)

    n_b = len(results["batters"])
    n_p = len(results["pitchers"])
    n_bench = len(results["bench"])
    print(f"ok: {n_b} batters · {n_p} pitchers · {n_bench} bench/IL")

if __name__ == "__main__":
    main()
