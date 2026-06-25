#!/usr/bin/env python3
"""
mlbCompare.py  --  Sabermetric player comparison for World Sillies
Usage: python3 mlbCompare.py "Player1" "Player2" ["Player3"] ["Player4"] [today|tomorrow]

Uses MLB Stats API (fast, reliable) - no Statcast CSV fetches.
Writes ~/discordBot/outputs/sports/mlb/fantasy/compare.json
"""

import sys, os, json, datetime, requests, unicodedata, csv
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────
BASE        = Path(os.path.expanduser("~/discordBot"))
MLB_DIR     = BASE / "outputs/sports/mlb"
FANTASY_DIR = BASE / "outputs/sports/mlb/fantasy"
OUT_JSON    = FANTASY_DIR / "compare.json"

import sys as _sys
_sys.path.insert(0, str(BASE / "python"))
try:
    from predictFantasy import get_ml_scores as _get_ml_scores
except Exception:
    _get_ml_scores = None

CURRENT_YEAR = datetime.date.today().year
TODAY        = datetime.date.today()

MLB_API = "https://statsapi.mlb.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; discordbot/1.0)",
}

# League-average baselines (2025-26 approximate)
LG_OPS    = 0.718
LG_OBP    = 0.315
LG_SLG    = 0.403
LG_AVG    = 0.243
LG_K_PCT  = 0.228
LG_BB_PCT = 0.085
LG_ERA    = 4.20
LG_WHIP   = 1.28
LG_K9     = 8.8
LG_BB9    = 3.2

# ── helpers ────────────────────────────────────────────────────────────────────
def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").strip().lower()

def safe(v, default=None):
    try:
        return float(v) if v not in (None, "", "-.--", "--") else default
    except:
        return default

def get(url, params=None, timeout=8):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

# ── player search ──────────────────────────────────────────────────────────────
def search_player(name: str) -> dict | None:
    """Returns {id, fullName, primaryPosition, currentTeam} or None."""
    try:
        data = get(f"{MLB_API}/people/search", {"names": name, "hydrate": "currentTeam"})
        people = data.get("people", [])
        if not people:
            # fallback: savant search (fast endpoint, just ID lookup not CSV)
            r2 = requests.get(
                f"https://baseballsavant.mlb.com/player/search-all?search={requests.utils.quote(name)}",
                headers=HEADERS, timeout=8
            )
            results = r2.json()
            if not results:
                return None
            p = results[0]
            return {
                "id":       p["id"],
                "fullName": p.get("name", name),
                "pos":      p.get("pos", ""),
                "team":     p.get("name_display_club", ""),
            }
        p = people[0]
        return {
            "id":       p["id"],
            "fullName": p.get("fullName", name),
            "pos":      p.get("primaryPosition", {}).get("abbreviation", ""),
            "team":     p.get("currentTeam", {}).get("name", ""),
        }
    except Exception as e:
        print(f"  [warn] search failed for '{name}': {e}", file=sys.stderr)
        return None

# ── season stats via MLB Stats API ────────────────────────────────────────────
def get_batter_stats(mlbam_id: int) -> dict:
    """Season hitting stats from MLB Stats API."""
    try:
        data = get(
            f"{MLB_API}/people/{mlbam_id}/stats",
            {"stats": "season", "group": "hitting", "season": CURRENT_YEAR}
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        pa  = int(s.get("plateAppearances", 0) or 0)
        ab  = int(s.get("atBats", 0) or 0)
        bb  = int(s.get("baseOnBalls", 0) or 0)
        k   = int(s.get("strikeOuts", 0) or 0)
        avg = safe(s.get("avg"))
        obp = safe(s.get("obp"))
        slg = safe(s.get("slg"))
        ops = safe(s.get("ops")) or ((obp or 0) + (slg or 0))
        hr  = int(s.get("homeRuns", 0) or 0)
        sb  = int(s.get("stolenBases", 0) or 0)
        iso = round((slg or 0) - (avg or 0), 3) if slg and avg else None
        bb_pct = round(bb / pa, 4) if pa > 0 else None
        k_pct  = round(k  / pa, 4) if pa > 0 else None
        return {
            "pa": pa, "ab": ab, "hr": hr, "sb": sb,
            "avg": avg, "obp": obp, "slg": slg, "ops": ops,
            "bb": bb, "k": k,
            "iso": iso, "bb_pct": bb_pct, "k_pct": k_pct,
        }
    except Exception as e:
        print(f"  [warn] batter stats failed for {mlbam_id}: {e}", file=sys.stderr)
        return {}

def get_pitcher_stats(mlbam_id: int) -> dict:
    """Season pitching stats from MLB Stats API."""
    try:
        data = get(
            f"{MLB_API}/people/{mlbam_id}/stats",
            {"stats": "season", "group": "pitching", "season": CURRENT_YEAR}
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s    = splits[0].get("stat", {})
        ip   = safe(s.get("inningsPitched"))
        era  = safe(s.get("era"))
        whip = safe(s.get("whip"))
        k    = int(s.get("strikeOuts", 0) or 0)
        bb   = int(s.get("baseOnBalls", 0) or 0)
        hr   = int(s.get("homeRuns", 0) or 0)
        bf   = int(s.get("battersFaced", 0) or 0)
        k9   = round(k  / (ip / 9), 2) if ip and ip > 0 else None
        bb9  = round(bb / (ip / 9), 2) if ip and ip > 0 else None
        k_pct  = round(k  / bf, 4) if bf > 0 else None
        bb_pct = round(bb / bf, 4) if bf > 0 else None
        return {
            "ip": ip, "era": era, "whip": whip,
            "k": k, "bb": bb, "hr": hr, "bf": bf,
            "k9": k9, "bb9": bb9,
            "k_pct": k_pct, "bb_pct": bb_pct,
        }
    except Exception as e:
        print(f"  [warn] pitcher stats failed for {mlbam_id}: {e}", file=sys.stderr)
        return {}

def get_last_n_games_batter(mlbam_id: int, n: int = 14) -> dict:
    """Last N games hitting stats."""
    try:
        data = get(
            f"{MLB_API}/people/{mlbam_id}/stats",
            {"stats": "lastXGames", "group": "hitting",
             "season": CURRENT_YEAR, "limit": n}
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s   = splits[0].get("stat", {})
        pa  = int(s.get("plateAppearances", 0) or 0)
        avg = safe(s.get("avg"))
        obp = safe(s.get("obp"))
        slg = safe(s.get("slg"))
        ops = safe(s.get("ops")) or ((obp or 0) + (slg or 0))
        hr  = int(s.get("homeRuns", 0) or 0)
        bb  = int(s.get("baseOnBalls", 0) or 0)
        k   = int(s.get("strikeOuts", 0) or 0)
        return {"pa": pa, "avg": avg, "obp": obp, "slg": slg,
                "ops": ops, "hr": hr, "bb": bb, "k": k}
    except Exception as e:
        print(f"  [warn] last-N batter failed for {mlbam_id}: {e}", file=sys.stderr)
        return {}

def get_last_n_games_pitcher(mlbam_id: int, n: int = 14) -> dict:
    """Last N games pitching stats."""
    try:
        data = get(
            f"{MLB_API}/people/{mlbam_id}/stats",
            {"stats": "lastXGames", "group": "pitching",
             "season": CURRENT_YEAR, "limit": n}
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return {}
        s    = splits[0].get("stat", {})
        ip   = safe(s.get("inningsPitched"))
        era  = safe(s.get("era"))
        whip = safe(s.get("whip"))
        k    = int(s.get("strikeOuts", 0) or 0)
        bb   = int(s.get("baseOnBalls", 0) or 0)
        bf   = int(s.get("battersFaced", 0) or 0)
        k_pct  = round(k  / bf, 4) if bf > 0 else None
        bb_pct = round(bb / bf, 4) if bf > 0 else None
        return {"ip": ip, "era": era, "whip": whip,
                "k": k, "bb": bb, "bf": bf,
                "k_pct": k_pct, "bb_pct": bb_pct}
    except Exception as e:
        print(f"  [warn] last-N pitcher failed for {mlbam_id}: {e}", file=sys.stderr)
        return {}

# ── matchup lookup (mismatch CSV, pre-computed) ────────────────────────────────
def get_matchup(player_name: str, day: str) -> dict | None:
    csv_path = MLB_DIR / ("mismatchToday.csv" if day == "today" else "mismatch.csv")
    if not csv_path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        df["batter_norm"] = df["batter"].apply(norm)
        hits = df[df["batter_norm"] == norm(player_name)]
        if hits.empty:
            return None
        row = hits.sort_values("PA", ascending=False).iloc[0]
        return {
            "pitcher":     row["pitcher"],
            "matchup_ops": float(row["OPS"]),
            "matchup_pa":  int(row["PA"]),
            "matchup_hr":  int(row.get("HR", 0)),
        }
    except:
        return None

def get_pitcher_opponent(pitcher_name: str, day: str) -> str | None:
    csv_path = MLB_DIR / ("probableStartersToday.csv" if day == "today" else "probableStarters.csv")
    if not csv_path.exists():
        return None
    try:
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                if norm(row.get("pitcher_name","")) == norm(pitcher_name):
                    matchup = row["matchup"]
                    team    = row.get("pitcher_team", row.get("team",""))
                    parts   = [p.strip() for p in matchup.replace(" @ "," vs ").split(" vs ")]
                    return next((p for p in parts if norm(p) != norm(team)), None)
    except:
        pass
    return None

# ── scoring ────────────────────────────────────────────────────────────────────
def score_batter_stream(recent: dict, season: dict, matchup: dict | None) -> tuple[int, list[str]]:
    score   = 50
    reasons = []

    # Recent form (last 14 games OPS vs league)
    ops = recent.get("ops")
    if ops is not None:
        diff  = (ops - LG_OPS) / LG_OPS
        score += diff * 28
        if ops >= 0.900:
            reasons.append(f"🔥 scorching last 14G (OPS {ops:.3f})")
        elif ops >= 0.800:
            reasons.append(f"✅ hot last 14G (OPS {ops:.3f})")
        elif ops >= 0.700:
            reasons.append(f"➡️ avg last 14G (OPS {ops:.3f})")
        elif ops >= 0.550:
            reasons.append(f"⚠️ cold last 14G (OPS {ops:.3f})")
        else:
            reasons.append(f"❄️ ice cold last 14G (OPS {ops:.3f})")

    # Recent HR pace
    hr_recent = recent.get("hr", 0)
    pa_recent = recent.get("pa", 1)
    if hr_recent >= 3:
        score += 6
        reasons.append(f"💣 {hr_recent} HR last 14G")
    elif hr_recent >= 2:
        score += 3
        reasons.append(f"💣 {hr_recent} HR last 14G")

    # Matchup vs today's/tomorrow's pitcher
    if matchup:
        mops = matchup["matchup_ops"]
        pa   = matchup["matchup_pa"]
        ptch = matchup["pitcher"]
        conf = "low conf" if pa < 10 else ("med conf" if pa < 20 else "high conf")
        if mops >= 1.000:
            score += 18
            reasons.append(f"🎯 owns {ptch} (OPS {mops:.3f}/{pa}PA, {conf})")
        elif mops >= 0.800:
            score += 9
            reasons.append(f"✅ good vs {ptch} (OPS {mops:.3f}/{pa}PA, {conf})")
        elif mops >= 0.600:
            reasons.append(f"➡️ neutral vs {ptch} (OPS {mops:.3f}/{pa}PA, {conf})")
        elif mops >= 0.350:
            score -= 9
            reasons.append(f"⚠️ struggles vs {ptch} (OPS {mops:.3f}/{pa}PA, {conf})")
        else:
            score -= 18
            reasons.append(f"❌ dominated by {ptch} (OPS {mops:.3f}/{pa}PA, {conf})")
    else:
        reasons.append("➡️ no matchup history on file")

    return max(0, min(100, round(score))), reasons

def score_batter_roster(season: dict) -> tuple[int, list[str]]:
    score   = 50
    reasons = []

    pa     = season.get("pa", 0)
    ops    = season.get("ops")
    obp    = season.get("obp")
    slg    = season.get("slg")
    avg    = season.get("avg")
    iso    = season.get("iso")
    bb_pct = season.get("bb_pct")
    k_pct  = season.get("k_pct")
    hr     = season.get("hr", 0)
    sb     = season.get("sb", 0)

    if pa < 50:
        reasons.append(f"⚠️ small sample ({pa} PA this season)")

    # OPS - primary value driver
    if ops is not None:
        diff   = (ops - LG_OPS) / LG_OPS
        score += diff * 32
        if ops >= 0.900:
            reasons.append(f"🔥 elite OPS {ops:.3f} (lg avg {LG_OPS:.3f})")
        elif ops >= 0.800:
            reasons.append(f"✅ above-avg OPS {ops:.3f}")
        elif ops >= 0.700:
            reasons.append(f"➡️ avg OPS {ops:.3f}")
        else:
            reasons.append(f"❌ below-avg OPS {ops:.3f}")

    # OBP (on-base is irreplaceable)
    if obp is not None:
        diff   = (obp - LG_OBP) / LG_OBP
        score += diff * 10
        if obp >= 0.370:
            reasons.append(f"👁️ elite OBP {obp:.3f}")

    # ISO power
    if iso is not None:
        if iso >= 0.220:
            score += 7
            reasons.append(f"💣 plus power (ISO {iso:.3f})")
        elif iso >= 0.160:
            score += 3
            reasons.append(f"💪 solid power (ISO {iso:.3f})")
        elif iso <= 0.080:
            score -= 4
            reasons.append(f"🪶 light power (ISO {iso:.3f})")

    # BB/K discipline
    if bb_pct is not None and k_pct is not None and k_pct > 0:
        ratio = bb_pct / k_pct
        if ratio >= 0.45:
            score += 5
            reasons.append(f"🎯 good discipline (BB% {bb_pct:.1%} / K% {k_pct:.1%})")
        elif k_pct >= 0.28:
            score -= 5
            reasons.append(f"⚠️ high K% {k_pct:.1%}")

    # SB upside
    if sb >= 15:
        score += 5
        reasons.append(f"💨 speed threat ({sb} SB)")
    elif sb >= 8:
        score += 2
        reasons.append(f"💨 {sb} SB")

    # HR pace
    if pa > 0:
        hr_per_pa = hr / pa
        if hr_per_pa >= 0.045:
            score += 5
            reasons.append(f"💥 {hr} HR in {pa} PA (elite HR rate)")
        elif hr_per_pa >= 0.030:
            score += 2
            reasons.append(f"💥 {hr} HR in {pa} PA")

    return max(0, min(100, round(score))), reasons

def score_pitcher_stream(recent: dict, opponent: str | None) -> tuple[int, list[str]]:
    score   = 50
    reasons = []

    era  = recent.get("era")
    whip = recent.get("whip")
    k_pct  = recent.get("k_pct")
    bb_pct = recent.get("bb_pct")
    ip   = recent.get("ip")

    if era is not None:
        diff   = (LG_ERA - era) / LG_ERA
        score += diff * 25
        if era <= 2.50:
            reasons.append(f"🔥 dominant last 14G (ERA {era:.2f})")
        elif era <= 3.50:
            reasons.append(f"✅ sharp last 14G (ERA {era:.2f})")
        elif era <= 4.50:
            reasons.append(f"➡️ avg last 14G (ERA {era:.2f})")
        else:
            reasons.append(f"❄️ struggling last 14G (ERA {era:.2f})")

    if whip is not None:
        diff   = (LG_WHIP - whip) / LG_WHIP
        score += diff * 12
        if whip <= 1.00:
            reasons.append(f"🌀 elite WHIP {whip:.2f} last 14G")
        elif whip >= 1.50:
            score -= 5
            reasons.append(f"⚠️ WHIP {whip:.2f} last 14G")

    if k_pct is not None and k_pct >= 0.28:
        score += 6
        reasons.append(f"💨 K% {k_pct:.1%} last 14G")

    if bb_pct is not None and bb_pct >= 0.12:
        score -= 8
        reasons.append(f"⚠️ command issues (BB% {bb_pct:.1%} last 14G)")

    if opponent:
        reasons.append(f"vs {opponent}")

    return max(0, min(100, round(score))), reasons

def score_pitcher_roster(season: dict) -> tuple[int, list[str]]:
    score   = 50
    reasons = []

    era    = season.get("era")
    whip   = season.get("whip")
    k9     = season.get("k9")
    bb9    = season.get("bb9")
    k_pct  = season.get("k_pct")
    bb_pct = season.get("bb_pct")
    ip     = season.get("ip")
    bf     = season.get("bf", 0)

    if bf < 60:
        reasons.append(f"⚠️ small sample ({bf} BF this season)")

    if era is not None:
        diff   = (LG_ERA - era) / LG_ERA
        score += diff * 28
        if era <= 3.00:
            reasons.append(f"🔥 elite ERA {era:.2f} (lg avg {LG_ERA:.2f})")
        elif era <= 3.80:
            reasons.append(f"✅ above-avg ERA {era:.2f}")
        elif era <= 4.50:
            reasons.append(f"➡️ avg ERA {era:.2f}")
        else:
            reasons.append(f"❌ below-avg ERA {era:.2f}")

    if whip is not None:
        diff   = (LG_WHIP - whip) / LG_WHIP
        score += diff * 14
        if whip <= 1.10:
            reasons.append(f"🌀 elite WHIP {whip:.2f}")
        elif whip >= 1.45:
            reasons.append(f"⚠️ WHIP {whip:.2f}")

    if k9 is not None:
        diff   = (k9 - LG_K9) / LG_K9
        score += diff * 12
        if k9 >= 10.5:
            reasons.append(f"💨 elite K/9 {k9:.1f}")
        elif k9 >= 9.0:
            reasons.append(f"✅ above-avg K/9 {k9:.1f}")
        elif k9 <= 6.5:
            score -= 5
            reasons.append(f"⚠️ low K/9 {k9:.1f}")

    if bb9 is not None:
        diff   = (LG_BB9 - bb9) / LG_BB9
        score += diff * 8
        if bb9 >= 4.5:
            score -= 5
            reasons.append(f"⚠️ command concerns (BB/9 {bb9:.1f})")
        elif bb9 <= 2.0:
            reasons.append(f"🎯 plus command (BB/9 {bb9:.1f})")

    if ip is not None and ip >= 50:
        reasons.append(f"📅 {ip:.0f} IP this season (durable)")

    return max(0, min(100, round(score))), reasons

# ── signal labels ──────────────────────────────────────────────────────────────
def stream_signal(s: int) -> str:
    if s >= 72: return "STRONG START ✅✅"
    if s >= 58: return "START ✅"
    if s >= 45: return "WATCH 👀"
    if s >= 32: return "SIT ❌"
    return "AVOID ❌❌"

def roster_signal(s: int) -> str:
    if s >= 72: return "MUST ADD 🔥"
    if s >= 58: return "ADD ✅"
    if s >= 45: return "MONITOR 👀"
    if s >= 32: return "PASS ❌"
    return "AVOID ❌❌"

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: mlbCompare.py \"Player1\" \"Player2\" [today|tomorrow]", file=sys.stderr)
        sys.exit(1)

    day = "today"
    if args[-1].lower() in ("today", "tomorrow"):
        day   = args[-1].lower()
        names = args[:-1]
    else:
        names = args
    names = names[:4]

    print(f"[compare] {len(names)} players for {day}: {', '.join(names)}")

    out_players = []

    _ml_bat = _get_ml_scores("batters",  ("daily",)) if _get_ml_scores else {}
    _ml_pit = _get_ml_scores("pitchers", ("daily",)) if _get_ml_scores else {}
    def _ml_lookup(name, is_pitcher):
        d = _ml_pit.get(norm(name), {}) if is_pitcher else _ml_bat.get(norm(name), {})
        return d.get("ml_pts_daily"), None

    for name in names:
        print(f"  resolving {name}...")
        info = search_player(name)
        if not info:
            out_players.append({
                "input_name": name, "name": name,
                "error": "player not found",
                "stream_score": 0, "roster_score": 0,
                "stream_signal": "NOT FOUND ❓",
                "roster_signal": "NOT FOUND ❓",
            })
            continue

        pid  = info["id"]
        pos  = (info.get("pos") or "").upper()
        team = info.get("team", "")
        is_pitcher = any(p in pos for p in ("SP","RP","P")) or pos in ("1","11","12")

        print(f"  [{name}] ID={pid} pos={pos} pitcher={is_pitcher}")

        if is_pitcher:
            season = get_pitcher_stats(pid)
            recent = get_last_n_games_pitcher(pid, 14)
            opp    = get_pitcher_opponent(info["fullName"], day) or get_pitcher_opponent(name, day)
            ss, sr = score_pitcher_stream(recent, opp)
            rs, rr = score_pitcher_roster(season)
            out_players.append({
                "input_name":    name,
                "name":          info["fullName"],
                "team":          team,
                "pos":           pos,
                "is_pitcher":    True,
                "day":           day,
                "season_era":    season.get("era"),
                "season_whip":   season.get("whip"),
                "season_k9":     season.get("k9"),
                "season_bb9":    season.get("bb9"),
                "season_ip":     season.get("ip"),
                "recent_era":    recent.get("era"),
                "recent_whip":   recent.get("whip"),
                "opponent":      opp,
                "stream_score":  ss,
                "roster_score":  rs,
                "stream_signal": stream_signal(ss),
                "ml_pts_daily":  _ml_lookup(info["fullName"], True)[0],
                "ml_pts_weekly": _ml_lookup(info["fullName"], True)[1],
                "roster_signal": roster_signal(rs),
                "stream_reasons": sr,
                "roster_reasons": rr,
            })
        else:
            season  = get_batter_stats(pid)
            recent  = get_last_n_games_batter(pid, 14)
            matchup = get_matchup(info["fullName"], day) or get_matchup(name, day)
            ss, sr  = score_batter_stream(recent, season, matchup)
            rs, rr  = score_batter_roster(season)
            out_players.append({
                "input_name":    name,
                "name":          info["fullName"],
                "team":          team,
                "pos":           pos,
                "is_pitcher":    False,
                "day":           day,
                "season_ops":    season.get("ops"),
                "season_obp":    season.get("obp"),
                "season_slg":    season.get("slg"),
                "season_avg":    season.get("avg"),
                "season_iso":    season.get("iso"),
                "season_bb_pct": season.get("bb_pct"),
                "season_k_pct":  season.get("k_pct"),
                "season_hr":     season.get("hr"),
                "season_sb":     season.get("sb"),
                "season_pa":     season.get("pa"),
                "recent_ops":    recent.get("ops"),
                "recent_hr":     recent.get("hr"),
                "recent_pa":     recent.get("pa"),
                "matchup_ops":   matchup["matchup_ops"] if matchup else None,
                "matchup_pa":    matchup["matchup_pa"]  if matchup else None,
                "matchup_pitcher": matchup["pitcher"]   if matchup else None,
                "stream_score":  ss,
                "roster_score":  rs,
                "stream_signal": stream_signal(ss),
                "ml_pts_daily":  _ml_lookup(info["fullName"], False)[0],
                "ml_pts_weekly": _ml_lookup(info["fullName"], False)[1],
                "roster_signal": roster_signal(rs),
                "stream_reasons": sr,
                "roster_reasons": rr,
            })

    out_players.sort(key=lambda x: -x["stream_score"])

    out = {
        "day":       day,
        "generated": datetime.datetime.now().isoformat(),
        "players":   out_players,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\n  {'STREAM':6} {'ROSTER':6}  NAME")
    for p in out_players:
        print(f"  {p['stream_score']:>6} {p['roster_score']:>6}   {p['name']} ({p.get('team','')})")
    print(f"\nok - {OUT_JSON}")

if __name__ == "__main__":
    main()
