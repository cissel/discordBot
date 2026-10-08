#!/usr/bin/env python3
"""
hardrockMlbEV.py

Read-only +EV monitor for HardRock Bet MLB odds (moneyline, run line, total,
and player props), compared against independent "fair" probability sources
to flag bets where our model beats the book's devigged implied probability.
Same pattern as hardrockEV.py (NFL) -- see that script's docstring for the
full rationale on why this is read-only/log-only and never places wagers.

Fair-probability models, per market:
  - Moneyline: ESPN BPI game-projection win probability (pregame).
  - Run line / Total: DraftKings' own line, pulled via ESPN's odds endpoint
    and devigged the same way HardRock's line is -- a genuine cross-book
    comparison, not a homebrew model, same honesty caveat as the NFL script
    (FL bettors can't act on DK directly, but it flags when HardRock's
    number is an outlier vs. a real second market).
  - Player props (Hits, Runs, RBIs, Home Runs, Doubles, Triples, Total
    Bases, Stolen Bases, Singles, Hits+Runs+RBI for batters; Strikeouts,
    Earned Runs, Hits Allowed for pitchers): MLB Stats API season rate
    stats (hits/gamesPlayed, etc. for batters; strikeOuts/gamesStarted etc.
    for starting pitchers), treated as a Poisson rate and used to price the
    O/U line: P(X > line) = 1 - Poisson.cdf(floor(line), lambda). This is a
    simplification in two ways, both honestly flagged: (1) real prop
    distributions are usually overdispersed vs. Poisson, and (2) it's a
    full-season rate, not adjusted for the specific opposing pitcher/park/
    weather -- a real second-pass improvement would fold in the pitcher
    matchup (see mlbMismatch.py's Statcast-based matchup work) but this is
    an honest, labeled first pass, not a guess. "Hits+Runs+RBI" sums the
    three independent Poisson lambdas (ignoring the real correlation
    between them) -- a further simplification worth revisiting if this
    market shows up as a bad calibration outlier during grading. Batters
    need >=15 games played and pitchers >=3 starts before their rate is
    trusted (below that, rows are logged as modelStatus='insufficient_sample'
    rather than pricing a rookie's 1-for-6 start as a huge "edge").

Confidence column = model win probability (0-100%) for the side we like,
per James's spec (raw model prob, not a bucketed label).

Output:
  - outputs/sports/mlb/betting/hardrock_mlb_ev_<YYYYMMDD_HHMM>.csv -- one
    snapshot per run (used by /mlb bets for the "right now" view).
  - outputs/sports/mlb/betting/hardrock_mlb_bets_log.csv -- APPEND-ONLY
    master log, every bet from every run (+EV and -EV alike), so both
    sides can be studied for calibration over time, not just flagged bets.
    Same outcomeId recurs across scrapes as odds move, which is
    intentional -- it's what makes line-movement/CLV analysis possible.

Columns: scrapedAt, betNumber, mlbGame, betDescription, payout, confidence,
expectedValue -- plus diagnostic columns kept at the end so the 5 requested
columns stay first and stable.

Usage:
  venv/bin/python3 python/hardrockMlbEV.py
"""
import os
import html as html_lib
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
from scipy.stats import poisson

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/mlb/betting")
MASTER_LOG_PATH = os.path.join(OUT_DIR, "hardrock_mlb_bets_log.csv")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
# ESPN's undocumented API 403s on a browser-style User-Agent but is happy
# with requests' default UA (or none at all) -- so ESPN calls omit HEADERS.
ISG_BASE = "https://hardrock-hub-web.isportgenius.com.au"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_PREDICTOR = (
    "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb/events/"
    "{event_id}/competitions/{event_id}/predictor"
)
ESPN_ODDS = (
    "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb/events/"
    "{event_id}/competitions/{event_id}/odds"
)
MLBAPI_PLAYERS = "https://statsapi.mlb.com/api/v1/sports/1/players"
MLBAPI_PEOPLE = "https://statsapi.mlb.com/api/v1/people"
MLBAPI_TEAMS = "https://statsapi.mlb.com/api/v1/teams?sportId=1"

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)

# ESPN abbreviation -> MLB Stats API abbreviation, where they diverge.
ESPN_TO_MLBAPI_TEAM = {"ARI": "AZ", "CHW": "CWS"}

# HardRock/isportgenius nickname vs. legal-name mismatches, if any surface.
# Keyed by normalized name (see §4b of the sportsbook-ev-analysis skill --
# expect to add entries here as new mismatches are found, don't assume a
# 'player_not_matched' row means the data doesn't exist).
PLAYER_NAME_ALIASES = {}

# Batter prop market -> season hitting-stat key(s) summed for the Poisson
# lambda, rate = sum(stat)/gamesPlayed.
BATTER_PROP_STAT_KEYS = {
    "Hits": ["hits"],
    "Runs": ["runs"],
    "RBIs": ["rbi"],
    "Home Runs": ["homeRuns"],
    "Doubles": ["doubles"],
    "Triples": ["triples"],
    "Total Bases": ["totalBases"],
    "Stolen Bases": ["stolenBases"],
    "Hits+Runs+RBI": ["hits", "runs", "rbi"],
    # Singles isn't a raw stat -- computed as hits - 2B - 3B - HR, handled
    # specially in the rate-building step below.
    "Singles": ["__singles__"],
}
# Pitcher prop market -> season pitching-stat key(s), rate = sum(stat)/gamesStarted.
PITCHER_PROP_STAT_KEYS = {
    "Strikeouts": ["strikeOuts"],
    "Earned Runs": ["earnedRuns"],
    "Hits Allowed": ["hits"],
}
PROP_STAT_KEYS = {**BATTER_PROP_STAT_KEYS, **PITCHER_PROP_STAT_KEYS}
PITCHER_MARKETS = set(PITCHER_PROP_STAT_KEYS)

# Minimum sample size before trusting a season rate as a Poisson lambda.
# Without this floor, a rookie with e.g. 1 double in 6 games played prices
# as a huge "edge" against the book purely from small-sample noise -- not a
# real signal. Below the floor the row is still logged (visibility) but
# marked insufficient_sample instead of getting a fabricated-looking EV.
MIN_BATTER_GAMES = 15
MIN_PITCHER_STARTS = 3


# --------------------------------------------------------------------------
# HardRock odds scraping (main lines: moneyline / run-line / total)
# --------------------------------------------------------------------------
MAIN_LINE_PATTERN = re.compile(
    r'data-outcome-id="(?P<outcome_id>[^"]+)"\s+'
    r'data-market-id="(?P<market_id>[^"]+)"\s+'
    r'data-odds="(?P<odds>[^"]+)"\s+'
    r'data-event-id="(?P<event_id>[^"]+)"\s+'
    r'data-match-time="(?P<match_time>[^"]+)"\s+'
    r'data-event-name="(?P<event_name>[^"]+)"\s+'
    r'data-sport="(?P<sport>[^"]+)"\s+'
    r'data-league="(?P<league>[^"]+)"\s+'
    r'data-match-id="(?P<match_id>[^"]+)"\s+'
    r'data-section="(?P<section>[^"]+)"\s+'
    r'data-hr-event-name="(?P<hr_event_name>[^"]+)"\s+'
    r'data-bet-type="(?P<bet_type>[^"]+)"'
)


def fetch_hardrock_main_lines():
    """Pull HardRock's current MLB slate (run-line/total/ML) for every game."""
    url = f"{ISG_BASE}/seo/baseball/mlb/upcoming-games"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text

    rows = []
    for m in MAIN_LINE_PATTERN.finditer(html):
        d = m.groupdict()
        end = m.end()
        window = html[end:end + 400]
        point_match = re.search(r'<span class="bet-type">([^<]*)</span>', window)
        bet_point = point_match.group(1).strip() if point_match else ""
        d["bet_point"] = bet_point
        rows.append(d)
    return rows


def fetch_hardrock_props():
    """Pull HardRock's current MLB player props (all games, all markets)."""
    url = f"{ISG_BASE}/get-props"
    params = {
        "sportUrl": "baseball",
        "leagueUrl": "mlb",
        "filterSeasonType": "",
        "filterVal": "",
        "filterValPer": "",
        "filterOverUnder": "",
        "theme": "",
        "oddsFormat": "",
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    html = data.get("props_html", "")

    player_blocks = list(re.finditer(r'<span class="tm-name\s*">([^<]+)</span>', html))
    outcome_pattern = MAIN_LINE_PATTERN

    rows = []
    for i, pb in enumerate(player_blocks):
        player = pb.group(1).strip()
        start = pb.end()
        end = player_blocks[i + 1].start() if i + 1 < len(player_blocks) else len(html)
        segment = html[start:end]
        for m in outcome_pattern.finditer(segment):
            d = m.groupdict()
            oend = m.end()
            window = segment[oend:oend + 400]
            point_match = re.search(r'<span class="bet-type">([^<]*)</span>', window)
            bet_side = point_match.group(1).strip() if point_match else ""
            d["player"] = player
            d["bet_side"] = bet_side  # e.g. "O 4.5"/"U 20.5"
            rows.append(d)
    return rows


# --------------------------------------------------------------------------
# ESPN fair-probability side (moneyline BPI model + DraftKings odds)
# --------------------------------------------------------------------------
def fetch_espn_day_events(max_days_forward=2):
    """Return (events, date_str) for the current MLB slate.

    MLB plays daily, so unlike the NFL week-hunt this just needs to confirm
    "today" (ESPN's default, no dates param) actually has games that aren't
    all final -- if the whole slate is already 'post' (e.g. cron runs late
    at night after the last game ended), scan forward a day or two the same
    way hardrockEV.py scans forward weeks for NFL.
    """
    for offset in range(max_days_forward + 1):
        params = {}
        if offset > 0:
            date_str = (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y%m%d")
            params["dates"] = date_str
        r = requests.get(ESPN_SCOREBOARD, params=params, timeout=20)
        if r.status_code != 200:
            continue
        d = r.json()
        events_raw = d.get("events", [])
        if not events_raw:
            continue
        states = [e["status"]["type"]["state"] for e in events_raw]
        if all(s == "post" for s in states):
            continue  # this day is fully over, try the next one
        events = []
        for e in events_raw:
            comp = e["competitions"][0]
            teams = {}
            for c in comp["competitors"]:
                teams[c["homeAway"]] = {
                    "abbr": c["team"]["abbreviation"],
                    "name": c["team"]["name"],  # nickname, e.g. "Reds" -- matches HR's hr_event_name style
                }
            events.append({
                "event_id": e["id"],
                "name": e["name"],
                "date": e["date"],
                "state": e["status"]["type"]["state"],
                "home": teams.get("home"),
                "away": teams.get("away"),
            })
        return events, d.get("day", {}).get("date")
    return [], None


def fetch_espn_predictor(event_id):
    """Return {'home_wp':..., 'away_wp':...} pregame BPI win prob, or None."""
    url = ESPN_PREDICTOR.format(event_id=event_id)
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        home_stats = {s["name"]: s["value"] for s in d.get("homeTeam", {}).get("statistics", [])}
        away_stats = {s["name"]: s["value"] for s in d.get("awayTeam", {}).get("statistics", [])}
        home_wp = home_stats.get("gameProjection")
        away_wp = away_stats.get("gameProjection")
        if home_wp is None or away_wp is None:
            return None
        return {"home_wp": home_wp / 100.0, "away_wp": away_wp / 100.0}
    except requests.RequestException:
        return None


def fetch_espn_book_odds(event_id):
    """Return DraftKings' run-line/total/ML odds for this event, or None.

    MLB's odds shape differs from NFL's: each side's run-line PRICE lives at
    <side>TeamOdds.current.spread.american (the vig for that side's -1.5/
    +1.5 line), and the POINT itself lives at
    <side>TeamOdds.current.pointSpread.american (signed, e.g. '-1.5').
    There's no separate 'spreadOdds' field like NFL uses.
    """
    url = ESPN_ODDS.format(event_id=event_id)
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        items = d.get("items", [])
        if not items:
            return None
        item = items[0]  # priority-1 book (DraftKings as of Sept 2026)
        away = item.get("awayTeamOdds", {})
        home = item.get("homeTeamOdds", {})

        def _american(side, field):
            try:
                return float(side.get("current", {}).get(field, {}).get("american"))
            except (TypeError, ValueError):
                return None

        return {
            "provider": item.get("provider", {}).get("name"),
            "overUnder": item.get("overUnder"),
            "overOdds": item.get("overOdds"),
            "underOdds": item.get("underOdds"),
            "home_moneyline": home.get("moneyLine"),
            "away_moneyline": away.get("moneyLine"),
            "away_spread_point": _american(away, "pointSpread"),
            "home_spread_point": _american(home, "pointSpread"),
            "away_spread_odds": _american(away, "spread"),
            "home_spread_odds": _american(home, "spread"),
        }
    except requests.RequestException:
        return None


def build_espn_lookup():
    """event -> {home_abbr, away_abbr, home_wp, away_wp, book odds}, keyed by
    a normalized 'AWAY NICKNAME@HOME NICKNAME' string so we can join
    HardRock's 'Team A @ Team B' hr_event_name to it."""
    events, slate_date = fetch_espn_day_events()
    lookup = {}
    for e in events:
        pred = fetch_espn_predictor(e["event_id"])
        book = fetch_espn_book_odds(e["event_id"])
        key_names = f'{e["away"]["name"]}@{e["home"]["name"]}'
        lookup[key_names] = {
            "espn_event_id": e["event_id"],
            "home_abbr": e["home"]["abbr"],
            "away_abbr": e["away"]["abbr"],
            "home_name": e["home"]["name"],
            "away_name": e["away"]["name"],
            "home_wp": pred["home_wp"] if pred else None,
            "away_wp": pred["away_wp"] if pred else None,
            "state": e["state"],
            "book": book,
        }
        time.sleep(0.15)  # be polite to ESPN's undocumented API
    return lookup, slate_date


def match_hr_game_to_espn(hr_event_name, espn_lookup):
    """hr_event_name looks like 'Twins @ Angels'. ESPN keys are
    'Away Nickname@Home Nickname' built the same way, so exact string match
    (after stripping HR's spaces around @) should hit directly; fall back to
    fuzzy team-name containment."""
    compact = hr_event_name.replace(" @ ", "@")
    if compact in espn_lookup:
        return espn_lookup[compact]
    if "@" not in hr_event_name:
        return None
    away_raw, home_raw = [s.strip() for s in hr_event_name.split("@", 1)]
    for key, val in espn_lookup.items():
        if away_raw in val["away_name"] or val["away_name"] in away_raw:
            if home_raw in val["home_name"] or val["home_name"] in home_raw:
                return val
    return None


# --------------------------------------------------------------------------
# MLB Stats API player index + season rate stats (for prop fair-prob models)
# --------------------------------------------------------------------------
def norm_name(s):
    s = html_lib.unescape(str(s))
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return PLAYER_NAME_ALIASES.get(s, s)


def fetch_mlbapi_team_abbrs():
    """MLB team id -> abbreviation (statsapi's own, e.g. AZ not ARI)."""
    r = requests.get(MLBAPI_TEAMS, timeout=20)
    r.raise_for_status()
    return {t["id"]: t["abbreviation"] for t in r.json().get("teams", [])}


def fetch_mlbapi_player_index(season):
    """normalized_name -> {id, full_name, team_abbr, position} for hitters
    and pitchers active this season."""
    team_abbrs = fetch_mlbapi_team_abbrs()
    r = requests.get(MLBAPI_PLAYERS, params={"season": season}, timeout=30)
    r.raise_for_status()
    idx = {}
    for p in r.json().get("people", []):
        full_name = p.get("fullName")
        if not full_name:
            continue
        team_id = p.get("currentTeam", {}).get("id")
        idx[norm_name(full_name)] = {
            "id": p["id"],
            "full_name": full_name,
            "team_abbr": team_abbrs.get(team_id),
            "position": p.get("primaryPosition", {}).get("abbreviation"),
        }
    return idx


def fetch_mlbapi_season_stats(player_ids, season, chunk_size=100):
    """Batch-fetch season hitting+pitching stat dicts, keyed by player id.
    Returns {pid: {"hitting": {...} or None, "pitching": {...} or None}}."""
    out = {}
    ids = list(player_ids)
    for i in range(0, len(ids), chunk_size):
        batch = ids[i:i + chunk_size]
        params = {
            "personIds": ",".join(str(x) for x in batch),
            "hydrate": f"stats(group=[hitting,pitching],type=season,season={season})",
        }
        try:
            r = requests.get(MLBAPI_PEOPLE, params=params, timeout=30)
            if r.status_code != 200:
                continue
            for person in r.json().get("people", []):
                pid = person["id"]
                rec = {"hitting": None, "pitching": None}
                for st in person.get("stats", []):
                    group = st.get("group", {}).get("displayName")
                    splits = st.get("splits", [])
                    if not splits:
                        continue
                    stat = splits[0].get("stat", {})
                    if group in rec:
                        rec[group] = stat
                out[pid] = rec
        except requests.RequestException:
            continue
    return out


def match_player_to_mlbapi(player_name, allowed_teams, player_index):
    """Find the MLB Stats API player entry for player_name restricted to
    the two teams in that game, to disambiguate common names. Returns the
    index dict or None if zero/multiple matches."""
    target = norm_name(player_name)
    info = player_index.get(target)
    if info is None:
        return None
    if info["team_abbr"] not in allowed_teams:
        return None
    return info


def build_prop_lambda(market, stats):
    """stats = {"hitting": {...}|None, "pitching": {...}|None}. Returns
    (lambda, denom_status) where denom_status flags why lambda is None if
    the needed stat group/games count is missing."""
    if market in PITCHER_MARKETS:
        p = stats.get("pitching")
        if not p:
            return None, "no_pitching_stats"
        games = p.get("gamesStarted") or 0
        if games <= 0:
            return None, "no_starts"
        if games < MIN_PITCHER_STARTS:
            return None, "insufficient_sample"
        total = sum(p.get(k, 0) or 0 for k in PITCHER_PROP_STAT_KEYS[market])
        return total / games, None
    h = stats.get("hitting")
    if not h:
        return None, "no_hitting_stats"
    games = h.get("gamesPlayed") or 0
    if games <= 0:
        return None, "no_games_played"
    if games < MIN_BATTER_GAMES:
        return None, "insufficient_sample"
    if market == "Singles":
        total = (h.get("hits", 0) or 0) - (h.get("doubles", 0) or 0) \
            - (h.get("triples", 0) or 0) - (h.get("homeRuns", 0) or 0)
    else:
        total = sum(h.get(k, 0) or 0 for k in BATTER_PROP_STAT_KEYS[market])
    return max(total, 0) / games, None


# --------------------------------------------------------------------------
# Odds math: American -> decimal -> implied prob -> devig (multiplicative)
# --------------------------------------------------------------------------
def american_to_decimal(odds_str):
    val = float(str(odds_str).strip().replace("+", ""))
    if val > 0:
        return 1 + val / 100.0
    else:
        return 1 + 100.0 / abs(val)


def american_to_implied_prob(odds_str):
    return 1.0 / american_to_decimal(odds_str)


def devig_two_way(prob_a, prob_b):
    """Multiplicative devig for a two-outcome market. Returns (fair_a, fair_b)."""
    total = prob_a + prob_b
    if total <= 0:
        return prob_a, prob_b
    return prob_a / total, prob_b / total


def american_payout_profit_per_dollar(odds_str):
    """Net profit per $1 staked (not including stake return)."""
    val = float(str(odds_str).strip().replace("+", ""))
    if val > 0:
        return val / 100.0
    else:
        return 100.0 / abs(val)


def expected_value_per_dollar(model_prob, odds_str):
    """EV per $1 staked = p*profit - (1-p)*1."""
    profit = american_payout_profit_per_dollar(odds_str)
    return model_prob * profit - (1 - model_prob) * 1.0


def parse_over_under_point(bet_point_str):
    """'+1.5'/'-1.5' -> 1.5 (run-line MAGNITUDE, sign dropped -- both sides
    of a run-line market print the same number of runs, just for/against,
    so comparing magnitudes against DK's abs(point) is the correct check);
    'O 8.5'/'U 8.5' -> 8.5."""
    m = re.search(r"[-+]?\d+(\.\d+)?", bet_point_str or "")
    return abs(float(m.group())) if m else None


def parse_prop_side_and_line(bet_side_str):
    """'O 4.5' -> ('over', 4.5); 'U 20.5' -> ('under', 20.5)."""
    s = (bet_side_str or "").strip()
    m = re.match(r"([OU])\s*([\d.]+)", s, re.IGNORECASE)
    if m:
        side = "over" if m.group(1).upper() == "O" else "under"
        return side, float(m.group(2))
    return None, None


# --------------------------------------------------------------------------
# Main line (ML/run-line/total) EV computation
# --------------------------------------------------------------------------
def build_moneyline_and_total_runline_rows(hr_rows, espn_lookup):
    by_market = {}
    for row in hr_rows:
        key = (row["match_id"], row["market_id"])
        by_market.setdefault(key, []).append(row)

    out = []
    bet_num = 1
    for (match_id, market_id), outcomes in by_market.items():
        if len(outcomes) != 2:
            continue  # skip malformed/incomplete market groupings
        hr_event_name = outcomes[0]["hr_event_name"]
        event_name_tag = outcomes[0]["event_name"]  # "Win"/"Cover"/"Over"/"Under"
        section = outcomes[0]["section"]
        espn = match_hr_game_to_espn(hr_event_name, espn_lookup)
        book = espn.get("book") if espn else None

        implied = [american_to_implied_prob(o["odds"]) for o in outcomes]
        hr_fair_a, hr_fair_b = devig_two_way(implied[0], implied[1])
        hr_fair_probs = [hr_fair_a, hr_fair_b]

        is_moneyline = event_name_tag.lower() == "win"
        is_spread = event_name_tag.lower() == "cover"
        is_total = event_name_tag.lower() in ("over", "under")

        away_raw, home_raw = [s.strip() for s in hr_event_name.split("@", 1)] \
            if "@" in hr_event_name else (None, None)

        for idx, o in enumerate(outcomes):
            model_prob = None
            model_status = "no_model"
            desc = None

            if is_moneyline:
                desc_team = away_raw if idx == 0 else home_raw
                desc = f"{desc_team} ML"
                if espn and espn.get("home_wp") is not None:
                    model_prob = espn["away_wp"] if idx == 0 else espn["home_wp"]
                    model_status = "espn_bpi"

            elif is_spread:
                desc_team = away_raw if idx == 0 else home_raw
                desc = f'{desc_team} {o.get("bet_point","")} run line'
                if (book and book.get("away_spread_point") is not None
                        and book.get("home_spread_point") is not None
                        and book.get("away_spread_odds") is not None
                        and book.get("home_spread_odds") is not None):
                    dk_away_implied = american_to_implied_prob(book["away_spread_odds"])
                    dk_home_implied = american_to_implied_prob(book["home_spread_odds"])
                    dk_fair_away, dk_fair_home = devig_two_way(dk_away_implied, dk_home_implied)
                    hr_point = parse_over_under_point(o.get("bet_point"))
                    dk_point = abs(book["away_spread_point"])
                    if hr_point is not None and abs(hr_point - dk_point) <= 0.5:
                        model_prob = dk_fair_away if idx == 0 else dk_fair_home
                        model_status = "draftkings_devig"
                    else:
                        model_status = "line_moved_no_compare"

            elif is_total:
                desc = f'{o.get("bet_point","")}'
                if book and book.get("overUnder") is not None and book.get("overOdds") and book.get("underOdds"):
                    dk_over_implied = american_to_implied_prob(book["overOdds"])
                    dk_under_implied = american_to_implied_prob(book["underOdds"])
                    dk_fair_over, dk_fair_under = devig_two_way(dk_over_implied, dk_under_implied)
                    hr_point = parse_over_under_point(o.get("bet_point"))
                    dk_point = book["overUnder"]
                    if hr_point is not None and abs(hr_point - dk_point) <= 0.5:
                        is_over = event_name_tag.lower() == "over"
                        model_prob = dk_fair_over if is_over else dk_fair_under
                        model_status = "draftkings_devig"
                    else:
                        model_status = "line_moved_no_compare"

            ev = expected_value_per_dollar(model_prob, o["odds"]) if model_prob is not None else None

            out.append({
                "betNumber": bet_num,
                "mlbGame": hr_event_name,
                "betDescription": desc,
                "payout": o["odds"],
                "confidence": round(model_prob * 100, 1) if model_prob is not None else None,
                "expectedValue": round(ev, 4) if ev is not None else None,
                "market": section,
                "modelStatus": model_status,
                "bookImpliedProbDevigged": round(hr_fair_probs[idx], 4),
                "hardrockOddsRaw": o["odds"],
                "matchTime": o["match_time"],
                "espnEventId": espn["espn_event_id"] if espn else None,
                "outcomeId": o["outcome_id"],
            })
            bet_num += 1
    return out


# --------------------------------------------------------------------------
# Player prop EV computation (Poisson rate model off MLB Stats API season rates)
# --------------------------------------------------------------------------
def build_prop_rows(prop_rows, espn_lookup, player_index, player_stats, start_bet_num=1):
    out = []
    bet_num = start_bet_num
    unmatched_players = set()

    for o in prop_rows:
        hr_event_name = o["hr_event_name"]
        market = o["event_name"]
        side, line = parse_prop_side_and_line(o["bet_side"])
        bet_desc = f'{o["player"]} - {market} ({o["bet_side"]})'

        model_prob = None
        model_status = "no_model"

        if market not in PROP_STAT_KEYS:
            model_status = "no_model"
        else:
            espn = match_hr_game_to_espn(hr_event_name, espn_lookup)
            if espn is None:
                model_status = "no_espn_match"
            else:
                allowed_teams = {
                    ESPN_TO_MLBAPI_TEAM.get(espn["home_abbr"], espn["home_abbr"]),
                    ESPN_TO_MLBAPI_TEAM.get(espn["away_abbr"], espn["away_abbr"]),
                }
                info = match_player_to_mlbapi(o["player"], allowed_teams, player_index)
                if info is None:
                    model_status = "player_not_matched"
                    unmatched_players.add(o["player"])
                elif info["id"] not in player_stats:
                    model_status = "no_stats"
                else:
                    lam, denom_status = build_prop_lambda(market, player_stats[info["id"]])
                    if denom_status is not None:
                        model_status = denom_status
                    elif lam is None or lam <= 0:
                        model_status = "zero_projected_rate"
                    elif side == "over" and line is not None:
                        model_prob = 1 - poisson.cdf(int(line), lam)
                        model_status = "mlbapi_poisson"
                    elif side == "under" and line is not None:
                        model_prob = poisson.cdf(int(line), lam)
                        model_status = "mlbapi_poisson"

        ev = expected_value_per_dollar(model_prob, o["odds"]) if model_prob is not None else None

        out.append({
            "betNumber": bet_num,
            "mlbGame": hr_event_name,
            "betDescription": bet_desc,
            "payout": o["odds"],
            "confidence": round(model_prob * 100, 1) if model_prob is not None else None,
            "expectedValue": round(ev, 4) if ev is not None else None,
            "market": o["section"],
            "modelStatus": model_status,
            "bookImpliedProbDevigged": None,
            "hardrockOddsRaw": o["odds"],
            "matchTime": o["match_time"],
            "espnEventId": None,
            "outcomeId": o["outcome_id"],
        })
        bet_num += 1

    if unmatched_players:
        print(f"  (props) {len(unmatched_players)} player name(s) could not be "
              f"matched to an MLB Stats API roster entry, e.g.: "
              f"{sorted(unmatched_players)[:5]}")
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    season = datetime.now().year

    print("Fetching ESPN day scoreboard + BPI predictor + DK odds per game...")
    espn_lookup, slate_date = build_espn_lookup()
    print(f"  slate {slate_date}: {len(espn_lookup)} ESPN games loaded, "
          f"{sum(1 for v in espn_lookup.values() if v['home_wp'] is not None)} with a BPI predictor, "
          f"{sum(1 for v in espn_lookup.values() if v['book'] is not None)} with book odds.")

    print("Fetching HardRock main lines (ML/run-line/total)...")
    hr_main = fetch_hardrock_main_lines()
    print(f"  {len(hr_main)} main-line outcomes.")

    print("Fetching HardRock player props...")
    hr_props = fetch_hardrock_props()
    print(f"  {len(hr_props)} prop outcomes.")

    print("Fetching MLB Stats API player index + season rate stats...")
    player_index = fetch_mlbapi_player_index(season)
    prop_players = {o["player"] for o in hr_props}
    candidate_ids = set()
    for pname in prop_players:
        info = player_index.get(norm_name(pname))
        if info:
            candidate_ids.add(info["id"])
    player_stats = fetch_mlbapi_season_stats(candidate_ids, season)
    print(f"  {len(player_index)} players indexed, "
          f"{len(candidate_ids)} candidate IDs matched by name, "
          f"{len(player_stats)} with season-{season} stats loaded.")

    main_rows = build_moneyline_and_total_runline_rows(hr_main, espn_lookup)
    prop_out_rows = build_prop_rows(
        hr_props, espn_lookup, player_index, player_stats,
        start_bet_num=len(main_rows) + 1,
    )

    all_rows = main_rows + prop_out_rows
    df = pd.DataFrame(all_rows)

    ts_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    df.insert(0, "scrapedAt", ts_iso)

    lead_cols = ["scrapedAt", "betNumber", "mlbGame", "betDescription", "payout", "confidence", "expectedValue"]
    other_cols = [c for c in df.columns if c not in lead_cols]
    df = df[lead_cols + other_cols]

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUT_DIR, f"hardrock_mlb_ev_{ts}.csv")
    df.to_csv(out_path, index=False)

    # Prune old per-run snapshots -- the master log is the durable history.
    snapshot_files = sorted(
        f for f in os.listdir(OUT_DIR)
        if f.startswith("hardrock_mlb_ev_") and f.endswith(".csv")
    )
    KEEP_SNAPSHOTS = 20
    for stale in snapshot_files[:-KEEP_SNAPSHOTS]:
        os.remove(os.path.join(OUT_DIR, stale))

    # Append EVERY scraped bet (not just +EV) to a running master log.
    write_header = not os.path.exists(MASTER_LOG_PATH)
    df.to_csv(MASTER_LOG_PATH, mode="a", header=write_header, index=False)
    log_total = sum(1 for _ in open(MASTER_LOG_PATH)) - 1  # minus header
    print(f"Appended {len(df)} rows to master log: {MASTER_LOG_PATH} "
          f"(now {log_total} rows total across all scrapes).")

    n_ev = df["expectedValue"].notna().sum()
    n_pos_ev = (df["expectedValue"] > 0).sum()
    print(f"\nWrote: {out_path}")
    print(f"Total bets: {len(df)} | with a computed EV: {n_ev} | +EV: {n_pos_ev}")
    print("\nBy market / model status:")
    print(df.groupby(["market", "modelStatus"]).size().to_string())
    if n_pos_ev:
        top = df[df["expectedValue"] > 0].sort_values("expectedValue", ascending=False).head(15)
        print("\n=== Top +EV bets ===")
        print(top[["mlbGame", "betDescription", "payout", "confidence", "expectedValue", "modelStatus"]].to_string(index=False))
    print("\nNOTE: run-line/total EV is relative to DraftKings' devigged line (a "
          "real second book, not a model) and is only computed when the two "
          "books are pricing within 0.5 runs of each other. Player-prop EV "
          "uses full-season rate stats (not opponent-adjusted). This is a "
          "read-only research snapshot -- log it over multiple weeks and "
          "check CLV/actual outcomes before trusting any single flagged bet.")


if __name__ == "__main__":
    main()
