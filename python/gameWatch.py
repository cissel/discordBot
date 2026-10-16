"""
gameWatch.py - ESPN scoreboard lookup + scorebug formatting for the
/game watch voice-channel-status feature (see commands.py game_group).

Pure data/formatting helpers, no discord.py imports, so this is easy to
unit-test in isolation (python3 gameWatch.py <sport> <team>).

Status format (fixed across all leagues per James's spec):
  "<emoji> <LEAGUE> | <away> <away_score>-<home_score> <home> | <game status>"
e.g.
  "🏈 NFL | TB 24-16 DAL | Q4 2:35"
  "⚾ MLB | LAD 4-7 MIL | Top 9th"
  "🏒 NHL | FLA 3-5 BUF | Halftime"  (n/a example, NHL uses intermission text)
  "🏈 NFL | TB @ DAL | 10/13 - 8:00 PM EDT"   (pregame)
  "⚾ MLB | LAD 4-7 MIL | FINAL"
"""

import csv
import json
import os
import re
import time

import requests

ESPN_SCOREBOARD_URLS = {
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    "nhl": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
}

# Daily schedule snapshots already produced by nflToday.py / mlbToday.py /
# nhlToday.py / nbaToday.py (via the /ball pipeline, refreshed ~9am ET daily).
# Used to drive the /game watch autocomplete dropdowns - cheap local file
# reads instead of an ESPN call per keystroke. Each file has a 'matchup' and
# a 'time' column (column order varies by sport's writer script, but we
# always read by name so that doesn't matter).
GAMES_TODAY_CSV = {
    "nfl": os.path.expanduser("~/discordBot/outputs/sports/nfl/gamesToday.csv"),
    "mlb": os.path.expanduser("~/discordBot/outputs/sports/mlb/gamesToday.csv"),
    "nhl": os.path.expanduser("~/discordBot/outputs/sports/nhl/gamesToday.csv"),
    "nba": os.path.expanduser("~/discordBot/outputs/sports/nba/gamesToday.csv"),
}
# Same 12h staleness window /ball uses before trusting these files (so a
# dead cron job shows "no games" instead of yesterday's stale schedule).
_GAMES_TODAY_MAX_AGE_SECONDS = 12 * 60 * 60

# ── active-watch persistence ──────────────────────────────────────────────
# Survives a bot restart mid-game: /scorebug watch writes an entry here,
# /scorebug unwatch (and a watch loop ending on its own - final score, game
# not found, permission revoked) clears it. On startup main.py reads this
# back via restorable_watches() and relaunches the poll loop for anything
# still valid - same pattern as outputs/aerospace/launch_alerts.json uses
# for launch-alert state.
WATCH_STATE_PATH = os.path.expanduser("~/discordBot/outputs/sports/scorebug_watches.json")
# Don't resume anything older than this - e.g. the bot was down overnight
# and the persisted game is long since over/no longer today's slate.
_WATCH_STATE_MAX_AGE_SECONDS = 20 * 60 * 60


def _load_watch_state() -> dict:
    if not os.path.exists(WATCH_STATE_PATH):
        return {}
    try:
        with open(WATCH_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_watch_state(state: dict) -> None:
    os.makedirs(os.path.dirname(WATCH_STATE_PATH), exist_ok=True)
    try:
        with open(WATCH_STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def persist_watch(channel_id: int, sport: str, team: str) -> None:
    """Record an active /scorebug watch to disk, keyed by channel id."""
    state = _load_watch_state()
    state[str(channel_id)] = {"sport": sport, "team": team, "started_at": time.time()}
    _save_watch_state(state)


def clear_watch(channel_id: int) -> None:
    """Remove a channel's persisted watch. Safe to call even if nothing is
    persisted for it (idempotent) - called from both /scorebug unwatch and
    the watch loop's own cleanup, whichever happens first."""
    state = _load_watch_state()
    if str(channel_id) in state:
        del state[str(channel_id)]
        _save_watch_state(state)


def restorable_watches() -> dict[int, dict]:
    """Return {channel_id: {'sport':..., 'team':...}} for watches that were
    active when the bot last went down, dropping (and persisting the drop
    of) anything older than _WATCH_STATE_MAX_AGE_SECONDS."""
    state = _load_watch_state()
    now = time.time()
    out = {}
    changed = False
    for cid_str, entry in list(state.items()):
        age = now - entry.get("started_at", 0)
        if age > _WATCH_STATE_MAX_AGE_SECONDS:
            del state[cid_str]
            changed = True
            continue
        try:
            out[int(cid_str)] = {"sport": entry["sport"], "team": entry["team"]}
        except (KeyError, ValueError):
            del state[cid_str]
            changed = True
    if changed:
        _save_watch_state(state)
    return out

_RECORD_SUFFIX_RE = re.compile(r"\s*\(\d+-\d+\)\s*$")
# ESPN's pregame shortDetail is "M/D - 7:40 PM EDT" - since /game watch only
# ever looks at today's slate, the date prefix is redundant; strip it down
# to just "7:40 PM EDT".
_PREGAME_DATE_PREFIX_RE = re.compile(r"^\d{1,2}/\d{1,2}\s*-\s*")
# ESPN renders pregame times as "7:00 PM EDT" - collapse the space before
# AM/PM ("7:00PM EDT") while keeping the space before the timezone.
_AMPM_SPACE_RE = re.compile(r"(\d)\s+([AaPp][Mm])\b")

LEAGUE_EMOJI = {
    "nfl": "<:nfl:1537614790615105677>",
    "mlb": "<:mlb:1491277235196399757>",
    "nhl": "<:nhl:1556479631748767875>",
    "nba": "<:nba:1495930595694346360>",
}
LEAGUE_NAME = {"nfl": "NFL", "mlb": "MLB", "nhl": "NHL", "nba": "NBA"}

# James's local teams - use the team's own custom-emoji logo in place of its
# plain abbreviation wherever it appears in a scorebug. Keyed by (sport, ESPN
# abbreviation) since abbreviations can repeat across sports.
TEAM_EMOJI = {
    ("nhl", "FLA"): "<:floridaPanthersShield:1354680697683574874>",
    ("nfl", "JAX"): "<:jagsHead:1354679128108044348>",
    ("nba", "ORL"): "<:Orlando_Magic:1491259637587116283>",
}

# Leagues whose emoji already has visual padding baked in (the NBA emoji is
# narrow with built-in left/right whitespace) - skip the space we'd normally
# put between the emoji and the following " | " separator for these.
LEAGUE_EMOJI_NO_TRAILING_SPACE = {"nba"}

# Common nicknames/aliases -> ESPN team abbreviation, so "/game watch jags"
# or "/game watch panthers" works without typing the exact city/abbr. Only
# needs to disambiguate within a sport (NHL "FLA" vs NFL team abbrs don't
# collide since each sport is looked up separately).
TEAM_ALIASES = {
    "nfl": {
        "jags": "JAX", "jaguars": "JAX", "jax": "JAX",
    },
    "nhl": {
        "panthers": "FLA", "cats": "FLA", "fla": "FLA",
    },
    "mlb": {
        # World Sillies is a fantasy league, not a specific MLB team -
        # no default alias here; users pass a real team.
    },
}


def _matches_team(team: dict, query: str) -> bool:
    q = query.strip().lower()
    candidates = [
        team.get("abbreviation", ""),
        team.get("displayName", ""),
        team.get("shortDisplayName", ""),
        team.get("name", ""),
        team.get("location", ""),
    ]
    return any(q == c.lower() or (len(q) > 2 and q in c.lower()) for c in candidates if c)


def find_game(sport: str, team_query: str) -> dict | None:
    """Look up today's ESPN scoreboard for `sport` and return the event
    dict for the game involving `team_query` (name/city/abbr/alias),
    preferring an in-progress game over a pre/post one if somehow more
    than one match comes back (shouldn't happen - a team plays once/day).
    Returns None if no game found.
    """
    sport = sport.lower()
    if sport not in ESPN_SCOREBOARD_URLS:
        raise ValueError(f"unsupported sport: {sport}")

    alias = TEAM_ALIASES.get(sport, {}).get(team_query.strip().lower())
    query = alias or team_query

    resp = requests.get(ESPN_SCOREBOARD_URLS[sport], timeout=20)
    resp.raise_for_status()
    events = resp.json().get("events", [])

    matches = []
    for e in events:
        comp = e["competitions"][0]
        for c in comp["competitors"]:
            if _matches_team(c["team"], query):
                matches.append(e)
                break

    if not matches:
        return None
    # prefer live > upcoming > final
    priority = {"in": 0, "pre": 1, "post": 2}
    matches.sort(key=lambda e: priority.get(e["competitions"][0]["status"]["type"]["state"], 3))
    return matches[0]


def _clean_matchup(matchup: str) -> str:
    """Strip W-L records nbaToday.py bakes into team names, e.g.
    'New York Knicks (0-3) @ Toronto Raptors (1-1)' -> 'New York Knicks @ Toronto Raptors'.
    No-op for sports whose writer doesn't include records."""
    parts = matchup.split(" @ ")
    return " @ ".join(_RECORD_SUFFIX_RE.sub("", p).strip() for p in parts)


def leagues_with_games_today() -> list[str]:
    """Return the subset of ESPN_SCOREBOARD_URLS keys that have a non-empty,
    fresh gamesToday.csv - drives the /game watch league autocomplete so
    only leagues actually playing today show up as choices."""
    available = []
    for sport, csv_path in GAMES_TODAY_CSV.items():
        if not os.path.exists(csv_path):
            continue
        age = time.time() - os.path.getmtime(csv_path)
        if age > _GAMES_TODAY_MAX_AGE_SECONDS:
            continue
        try:
            with open(csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            continue
        if rows:
            available.append(sport)
    return available


def list_games_today(sport: str) -> list[dict]:
    """Return today's games for `sport` as [{'matchup': 'AWAY @ HOME', 'time': str}, ...]
    read from the local gamesToday.csv (same file /ball uses), cleaned of
    any W-L records some writers bake into the team name."""
    sport = sport.lower()
    csv_path = GAMES_TODAY_CSV.get(sport)
    if not csv_path or not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return []
    games = []
    for row in rows:
        matchup = (row.get("matchup") or "").strip()
        if not matchup:
            continue
        games.append({"matchup": _clean_matchup(matchup), "time": (row.get("time") or "").strip()})
    return games


def team_query_from_matchup(matchup: str) -> str:
    """Given a cleaned 'AWAY @ HOME' matchup string (as returned by a /game
    watch game-dropdown choice), return a team_query usable with find_game -
    just the away team's name, since find_game only needs to match one side
    of the game to identify it."""
    return matchup.split(" @ ")[0].strip()


def parse_event(event: dict, sport: str) -> dict:
    """Pull out the fields we need from a raw ESPN event dict."""
    comp = event["competitions"][0]
    status = comp["status"]
    stype = status["type"]
    teams = {}
    for c in comp["competitors"]:
        teams[c["homeAway"]] = {
            "abbr": c["team"].get("abbreviation", "?"),
            "score": c.get("score", "0"),
        }
    return {
        "sport": sport.lower(),
        "event_id": event["id"],
        "state": stype["state"],              # "pre" | "in" | "post"
        "type_name": stype.get("name", ""),    # e.g. "STATUS_HALFTIME"
        "description": stype.get("description", ""),
        "short_detail": stype.get("shortDetail", ""),  # e.g. "Final", "Top 9th", "7:40 PM EST"
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "situation": comp.get("situation"),    # MLB only: {'outs','onFirst','onSecond','onThird',...}
        "home": teams.get("home", {}),
        "away": teams.get("away", {}),
    }


# MLB live-status symbols (James's manual scorebug convention):
# 🔻<inning> / 🔺<inning> for bottom/top of the inning; outs as 2 dots -
# ⚪ = out recorded, ⚫ = out not yet recorded (fills left-to-right as outs
# happen); bases shown 3rd-2nd-1st (left-to-right, mirrors the diamond as
# seen from home plate) - ▪️ empty, ◽ occupied.
_MLB_OUT_RECORDED = "⚪"
_MLB_OUT_PENDING = "⚫"
_MLB_BASE_EMPTY = "▪️"
_MLB_BASE_OCCUPIED = "◽"


def _mlb_live_segment(parsed: dict) -> str:
    """Build the inning/outs/bases segment for a live MLB game, e.g.
    '🔻6 | ⚪ ⚫ | ▪️▪️▪️' (bottom 6th, 1 out recorded, bases empty).
    Falls back to ESPN's plain shortDetail ('Top 9th') if situation data
    isn't available yet (can lag a few seconds behind the period/score)."""
    situation = parsed.get("situation")
    period = parsed.get("period")
    type_name = parsed.get("type_name", "").upper()

    if not period:
        return parsed["short_detail"] or "In Progress"

    is_bottom = "BOTTOM" in type_name or "BOT_" in type_name or parsed["short_detail"].lower().startswith("bot")
    arrow = "🔻" if is_bottom else "🔺"
    inning_part = f"{arrow}{period}"

    if not situation:
        # no live situation payload yet - still show inning, skip outs/bases
        return inning_part

    outs = situation.get("outs", 0) or 0
    outs_part = " ".join(_MLB_OUT_RECORDED if i < outs else _MLB_OUT_PENDING for i in range(2))

    bases_part = "".join(
        _MLB_BASE_OCCUPIED if situation.get(key) else _MLB_BASE_EMPTY
        for key in ("onThird", "onSecond", "onFirst")
    )

    return f"{inning_part} | {outs_part} | {bases_part}"


def _live_status_text(parsed: dict) -> str:
    """Game-status segment for an in-progress game, per-sport conventions.
    Baseball has no clock (innings only, top/bottom already in ESPN's
    shortDetail); football/hockey use period+clock, falling back to
    ESPN's own description for dead-clock states (halftime, intermission,
    end-of-period) since those don't have a meaningful clock value.
    """
    sport = parsed["sport"]
    period = parsed.get("period")
    clock = (parsed.get("clock") or "").strip()
    type_name = parsed.get("type_name", "").upper()

    if sport == "mlb":
        return _mlb_live_segment(parsed)

    if sport == "nfl":
        if "HALFTIME" in type_name:
            return "Halftime"
        if not period:
            return parsed["short_detail"] or "In Progress"
        label = f"Q{period}" if period <= 4 else "OT"
        return f"{label} {clock}".strip()

    if sport == "nba":
        if "HALFTIME" in type_name:
            return "Halftime"
        if not period:
            return parsed["short_detail"] or "In Progress"
        label = f"Q{period}" if period <= 4 else "OT"
        return f"{label} {clock}".strip()

    if sport == "nhl":
        if "END_PERIOD" in type_name or "END_OF_PERIOD" in type_name or clock in ("0:00", ""):
            # intermission or between-whistle dead clock - ESPN's own
            # description text ("End of 1st Period") reads better than
            # us guessing a clock value that isn't meaningful right now
            return parsed["short_detail"] or parsed.get("description") or "Intermission"
        if not period:
            return parsed["short_detail"] or "In Progress"
        if period <= 3:
            label = f"P{period}"
        elif period == 4:
            label = "OT"
        else:
            label = "SO"
        return f"{label} {clock}".strip()

    return parsed["short_detail"] or "In Progress"


def _team_label(sport: str, abbr: str) -> str:
    """Return the team's custom emoji logo if it's one of James's local
    teams (see TEAM_EMOJI), otherwise its plain ESPN abbreviation."""
    return TEAM_EMOJI.get((sport, abbr), abbr)


def format_scorebug(parsed: dict) -> str:
    """Render the fixed-format voice-channel-status string:
      '<emoji> <LEAGUE> | <away> <away_score>-<home_score> <home> | <status>'
    """
    sport = parsed["sport"]
    emoji = LEAGUE_EMOJI.get(sport, "")
    away, home = parsed["away"], parsed["home"]
    state = parsed["state"]
    if emoji and sport in LEAGUE_EMOJI_NO_TRAILING_SPACE:
        prefix = f"{emoji}|"
    elif emoji:
        prefix = f"{emoji} |"
    else:
        prefix = f"{LEAGUE_NAME.get(sport, sport.upper())} |"

    away_label = _team_label(sport, away.get("abbr", "?"))
    home_label = _team_label(sport, home.get("abbr", "?"))

    if state == "pre":
        matchup = f"{away_label} @ {home_label}"
        status_text = _PREGAME_DATE_PREFIX_RE.sub("", parsed["short_detail"]) or "Scheduled"
        status_text = _AMPM_SPACE_RE.sub(r"\1\2", status_text)
    elif state == "post":
        matchup = f"{away_label} {away.get('score','0')}-{home.get('score','0')} {home_label}"
        status_text = (parsed["short_detail"] or "Final").upper()
    else:  # "in"
        matchup = f"{away_label} {away.get('score','0')}-{home.get('score','0')} {home_label}"
        status_text = _live_status_text(parsed)

    return f"{prefix} {matchup} | {status_text}"


if __name__ == "__main__":
    import sys
    sport = sys.argv[1] if len(sys.argv) > 1 else "nfl"
    team = sys.argv[2] if len(sys.argv) > 2 else "jags"
    ev = find_game(sport, team)
    if not ev:
        print(f"no game found for {sport}/{team}")
    else:
        parsed = parse_event(ev, sport)
        print(parsed)
        print(format_scorebug(parsed))
