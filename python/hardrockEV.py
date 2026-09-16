#!/usr/bin/env python3
"""
hardrockEV.py

Read-only +EV monitor for HardRock Bet NFL odds (moneyline, spread, total,
and player props), compared against independent "fair" probability sources
to flag bets where our model beats the book's devigged implied probability.

This is a ONE-SHOT snapshot tool: run it and it writes a CSV of every bet
currently offered on HardRock's NFL slate with a computed edge. It does NOT
place bets and never touches HardRock's betslip/wager APIs -- it only reads
the same publicly-served HTML/JSON that unauthenticated visitors to
hardrock.bet get served, via HardRock's third-party odds-widget partner
(isportgenius / GTG Network), which is a materially different -- and lower
risk -- category than the "bot" clause in HardRock's own ToS (which is about
automated wager submission, not reading a public odds display).

Fair-probability models, per market:
  - Moneyline: ESPN BPI game-projection win probability (pregame).
  - Spread / Total: DraftKings' own line, pulled via ESPN's odds endpoint
    and devigged the same way HardRock's line is. This is a genuine
    cross-book comparison (two real markets, not a homebrew model) --
    honest about what it is: relative value vs. another book's price, not
    a "true" probability. FL bettors can't act on DK directly, but the
    comparison still tells you when HardRock's number is an outlier.
  - Player props (Anytime/2+/3+ TD, Passing TDs, Receptions, Pass
    Completions): Sleeper's weekly projection API gives a per-player mean
    rate stat (rush_td, rec_td, pass_td, rec, pass_cmp) for the upcoming
    game. Treated as a Poisson rate and used to price the O/U or Yes/No
    line directly: P(X > line) = 1 - Poisson.cdf(floor(line), lambda).
    This is a simplification (real prop distributions are usually
    overdispersed vs. Poisson -- more Negative-Binomial in shape) but it's
    a real, honestly-labeled first-pass model, not a guess.
  - Longest Reception / First TD / Last TD: NOT modeled. Longest Reception
    is an order statistic (max of a game's receptions), not a simple count,
    and First/Last TD is a mutually-exclusive-across-the-whole-game-field
    problem -- neither is well served by a per-player Poisson rate, so
    both are left modelStatus='no_model' rather than faked.

Confidence column = model win probability (0-100%) for the side we like,
per James's spec (raw model prob, not a bucketed label).

Output:
  - outputs/sports/nfl/betting/hardrock_ev_<YYYYMMDD_HHMM>.csv -- one snapshot
    per run (used by /nfl bets for the "right now" view).
  - outputs/sports/nfl/betting/hardrock_bets_log.csv -- APPEND-ONLY master
    log, every bet from every run (+EV and -EV alike), so both sides can be
    studied for calibration over time, not just the flagged bets. Same
    outcomeId recurs across scrapes as odds move, which is intentional --
    it's what makes line-movement/CLV analysis possible later.

Columns: scrapedAt, betNumber, nflGame, betDescription, payout, confidence,
expectedValue -- plus diagnostic columns kept at the end so the 5 requested
columns stay first and stable.

Usage:
  venv/bin/python3 python/hardrockEV.py
"""
import os
import html as html_lib
import re
import time
import unicodedata
from datetime import datetime, timezone

import requests
import pandas as pd
from scipy.stats import poisson

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/betting")
MASTER_LOG_PATH = os.path.join(OUT_DIR, "hardrock_bets_log.csv")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
# ESPN's undocumented API 403s on a browser-style User-Agent but is happy
# with requests' default UA (or none at all) -- so ESPN calls omit HEADERS.
ISG_BASE = "https://hardrock-hub-web.isportgenius.com.au"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_PREDICTOR = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/"
    "{event_id}/competitions/{event_id}/predictor"
)
ESPN_ODDS = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/"
    "{event_id}/competitions/{event_id}/odds"
)
SLEEPER_STATE = "https://api.sleeper.app/v1/state/nfl"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
SLEEPER_GRAPHQL = "https://sleeper.com/graphql"

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)

# ESPN abbreviation -> Sleeper abbreviation, where they diverge.
ESPN_TO_SLEEPER_TEAM = {"WSH": "WAS"}

# HardRock/isportgenius sometimes shows a common nickname instead of the
# legal first name Sleeper's roster API uses. Keyed by normalized name.
PLAYER_NAME_ALIASES = {
    "josh palmer": "joshua palmer",
    "cameron skattebo": "cam skattebo",
}

# Player-prop market -> which Sleeper projection stat key(s) feed the
# Poisson lambda, and whether the printed line already IS the market's
# implicit threshold (Anytime/2+/3+ TD) or a real book-set O/U line
# (Passing TDs, Receptions, Pass Completions).
PROP_STAT_KEYS = {
    "Anytime TD": ["rush_td", "rec_td"],
    "To Score 2+ TDs": ["rush_td", "rec_td"],
    "To Score 3+ TDs": ["rush_td", "rec_td"],
    "Passing TDs": ["pass_td"],
    "Receptions": ["rec"],
    "Pass Completions": ["pass_cmp"],
}
# Markets we deliberately do NOT model (see module docstring) -- listed so
# the intent is explicit in code, not just an absence.
PROP_NO_MODEL_MARKETS = {"Longest Reception", "First TD", "Last TD"}


# --------------------------------------------------------------------------
# HardRock odds scraping (main lines: moneyline / spread / total)
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
    """Pull HardRock's current NFL slate (spread/total/ML) for every game.

    Returns a list of dicts, one per outcome (6 per game: spread x2, total x2,
    ML x2), keyed by match_id/event_id plus team names from the hr-event-name.
    """
    url = f"{ISG_BASE}/seo/american-football/nfl/upcoming-games"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text

    rows = []
    for m in MAIN_LINE_PATTERN.finditer(html):
        d = m.groupdict()
        # The bet-point (spread number / total number) sits in a sibling
        # <span class="bet-type">...</span> in the surrounding block; for
        # spread/total the data-event-name is Cover/Over/Under and the
        # printed line (+5.0 etc.) is not in the data-* attrs, so pull it
        # from the immediate following markup.
        end = m.end()
        window = html[end:end + 400]
        point_match = re.search(r'<span class="bet-type">([^<]*)</span>', window)
        bet_point = point_match.group(1).strip() if point_match else ""
        d["bet_point"] = bet_point
        rows.append(d)
    return rows


def fetch_hardrock_props():
    """Pull HardRock's current NFL player props (all games, all markets)."""
    url = f"{ISG_BASE}/get-props"
    params = {
        "sportUrl": "american-football",
        "leagueUrl": "nfl",
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

    # Player name context: each player's block starts with a tm-name span,
    # and every betslip-btn after it (until the next tm-name span) belongs
    # to that player.
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
            d["bet_side"] = bet_side  # e.g. "Yes"/"No"/"O 4.5"/"U 20.5"
            rows.append(d)
    return rows


# --------------------------------------------------------------------------
# ESPN fair-probability side (moneyline BPI model + DraftKings odds)
# --------------------------------------------------------------------------
def fetch_espn_week_events():
    """Return (events, week_number) for the CURRENT UPCOMING NFL week.

    ESPN's scoreboard endpoint defaults to whatever week it considers
    "current", which during a live week can still be the just-finished one
    (all games 'post') for a day or two. We want whichever week actually has
    games in 'pre' or 'in' state, so scan forward a few weeks and pick the
    first one that isn't all in the past.
    """
    for wk in range(1, 19):
        r = requests.get(
            ESPN_SCOREBOARD,
            params={"week": wk, "seasontype": 2, "year": datetime.now().year},
            timeout=20,
        )
        if r.status_code != 200:
            continue
        d = r.json()
        events_raw = d.get("events", [])
        if not events_raw:
            continue
        states = [e["status"]["type"]["state"] for e in events_raw]
        if all(s == "post" for s in states):
            continue  # this week is fully over, try the next one
        events = []
        for e in events_raw:
            comp = e["competitions"][0]
            teams = {}
            for c in comp["competitors"]:
                teams[c["homeAway"]] = {
                    "abbr": c["team"]["abbreviation"],
                    "name": c["team"]["displayName"],
                }
            events.append({
                "event_id": e["id"],
                "name": e["name"],
                "date": e["date"],
                "state": e["status"]["type"]["state"],
                "home": teams.get("home"),
                "away": teams.get("away"),
            })
        return events, wk
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
    """Return the first listed book's (DraftKings, in practice) spread/total/
    ML odds for this event, or None. Used as an independent second book for
    spread/total comparison since HardRock is FL's only legal book and we
    can't line-shop directly."""
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
        return {
            "provider": item.get("provider", {}).get("name"),
            "spread": item.get("spread"),  # negative = home favored, e.g. -4.5
            "overUnder": item.get("overUnder"),
            "overOdds": item.get("overOdds"),
            "underOdds": item.get("underOdds"),
            "home_moneyline": item.get("homeTeamOdds", {}).get("moneyLine"),
            "away_moneyline": item.get("awayTeamOdds", {}).get("moneyLine"),
            "home_spread_odds": item.get("homeTeamOdds", {}).get("spreadOdds"),
            "away_spread_odds": item.get("awayTeamOdds", {}).get("spreadOdds"),
        }
    except requests.RequestException:
        return None


def build_espn_lookup():
    """event -> {home_abbr, away_abbr, home_wp, away_wp, book odds}, keyed by
    a normalized 'AWAY NAME@HOME NAME' string so we can join HardRock's
    'Team A @ Team B' hr_event_name to it."""
    events, week = fetch_espn_week_events()
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
    return lookup, week


def match_hr_game_to_espn(hr_event_name, espn_lookup):
    """hr_event_name looks like 'Detroit Lions @ Buffalo Bills'. ESPN keys
    are 'Away Name@Home Name' built the same way, so exact string match
    should hit directly; fall back to fuzzy team-name containment."""
    if hr_event_name in espn_lookup:
        return espn_lookup[hr_event_name]
    if "@" not in hr_event_name:
        return None
    away_raw, home_raw = [s.strip() for s in hr_event_name.split("@", 1)]
    for key, val in espn_lookup.items():
        if away_raw in val["away_name"] or val["away_name"] in away_raw:
            if home_raw in val["home_name"] or val["home_name"] in home_raw:
                return val
    return None


# --------------------------------------------------------------------------
# Sleeper player-projection lookup (for prop fair-probability models)
# --------------------------------------------------------------------------
def norm_name(s):
    s = html_lib.unescape(str(s))
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return PLAYER_NAME_ALIASES.get(s, s)


def fetch_sleeper_player_index():
    """player_id -> {full_name, norm_name, team, position} for offensive
    skill players (QB/RB/WR/TE), the only positions HardRock's shown props
    cover."""
    r = requests.get(SLEEPER_PLAYERS, timeout=60)
    r.raise_for_status()
    d = r.json()
    idx = {}
    for pid, p in d.items():
        pos = p.get("position")
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        full_name = p.get("full_name")
        if not full_name:
            continue
        idx[pid] = {
            "full_name": full_name,
            "norm_name": norm_name(full_name),
            "team": p.get("team"),
            "position": pos,
        }
    return idx


def fetch_sleeper_week_projections(player_ids, season, week, chunk_size=200):
    """Batch-fetch Sleeper's weekly projection stat dict per player_id."""
    query = """
      query get_player_score_and_projections_batch(
        $sport: String!, $season: String!, $season_type: String!,
        $week: Int!, $player_ids: [String!]!
      ) {
        proj: stats_for_players_in_week(
          sport: $sport, season: $season, category: "proj",
          season_type: $season_type, week: $week, player_ids: $player_ids
        ) {
          player_id
          stats
        }
      }
    """
    projections = {}
    ids = list(player_ids)
    for i in range(0, len(ids), chunk_size):
        batch = ids[i:i + chunk_size]
        payload = {
            "operationName": "get_player_score_and_projections_batch",
            "variables": {
                "sport": "nfl", "season": str(season), "season_type": "regular",
                "week": int(week), "player_ids": batch,
            },
            "query": query,
        }
        try:
            r = requests.post(SLEEPER_GRAPHQL, json=payload, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json().get("data", {})
            for row in data.get("proj") or []:
                projections[row["player_id"]] = row.get("stats") or {}
        except requests.RequestException:
            continue
    return projections


def match_player_to_sleeper(player_name, allowed_teams, sleeper_index):
    """Find the Sleeper player_id for player_name restricted to a set of
    team abbreviations (the two teams in that game), to disambiguate common
    names. Returns player_id or None if zero/multiple matches."""
    target = norm_name(player_name)
    allowed_sleeper_teams = {ESPN_TO_SLEEPER_TEAM.get(t, t) for t in allowed_teams}
    candidates = [
        pid for pid, info in sleeper_index.items()
        if info["norm_name"] == target and info["team"] in allowed_sleeper_teams
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


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
    """'+5.0'/'-5.0' -> 5.0 (spread MAGNITUDE, sign dropped -- both sides of
    a spread market print the same number of points, just for/against, so
    comparing magnitudes against DK's abs(spread) is the correct check);
    'O 54.0'/'U 54.0' -> 54.0."""
    m = re.search(r"[-+]?\d+(\.\d+)?", bet_point_str or "")
    return abs(float(m.group())) if m else None


def parse_prop_side_and_line(bet_side_str):
    """'O 4.5' -> ('over', 4.5); 'U 20.5' -> ('under', 20.5);
    'Yes' -> ('yes', None); 'No' -> ('no', None)."""
    s = (bet_side_str or "").strip()
    if s.lower() in ("yes", "no"):
        return s.lower(), None
    m = re.match(r"([OU])\s*([\d.]+)", s, re.IGNORECASE)
    if m:
        side = "over" if m.group(1).upper() == "O" else "under"
        return side, float(m.group(2))
    return None, None


# --------------------------------------------------------------------------
# Main line (ML/spread/total) EV computation
# --------------------------------------------------------------------------
def build_moneyline_and_spread_total_rows(hr_rows, espn_lookup):
    """Moneyline: fair prob from ESPN BPI. Spread/Total: fair prob from
    devigging DraftKings' own line (pulled via ESPN's odds endpoint) as an
    independent second book -- a real cross-book comparison, not a model."""
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

        # away is outcomes[0], home is outcomes[1] -- matches the widget's
        # consistent HTML display order (verified against ESPN home/away).
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
                desc = f'{desc_team} {o.get("bet_point","")} spread'
                if book and book.get("spread") is not None and book.get("home_spread_odds") and book.get("away_spread_odds"):
                    dk_home_implied = american_to_implied_prob(book["home_spread_odds"])
                    dk_away_implied = american_to_implied_prob(book["away_spread_odds"])
                    dk_fair_away, dk_fair_home = devig_two_way(dk_away_implied, dk_home_implied)
                    hr_point = parse_over_under_point(o.get("bet_point"))
                    dk_point = abs(book["spread"])
                    # Only compare if both books are pricing (approximately)
                    # the same number -- if the line has moved materially,
                    # a straight prob comparison isn't apples-to-apples.
                    if hr_point is not None and abs(hr_point - dk_point) <= 1.0:
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
                    if hr_point is not None and abs(hr_point - dk_point) <= 1.0:
                        is_over = event_name_tag.lower() == "over"
                        model_prob = dk_fair_over if is_over else dk_fair_under
                        model_status = "draftkings_devig"
                    else:
                        model_status = "line_moved_no_compare"

            ev = expected_value_per_dollar(model_prob, o["odds"]) if model_prob is not None else None

            out.append({
                "betNumber": bet_num,
                "nflGame": hr_event_name,
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
# Player prop EV computation (Poisson rate model off Sleeper projections)
# --------------------------------------------------------------------------
def build_prop_rows(prop_rows, espn_lookup, sleeper_index, sleeper_proj, start_bet_num=1):
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

        if market in PROP_NO_MODEL_MARKETS:
            model_status = "no_model"
        elif market in PROP_STAT_KEYS:
            espn = match_hr_game_to_espn(hr_event_name, espn_lookup)
            if espn is None:
                model_status = "no_espn_match"
            else:
                allowed_teams = {espn["home_abbr"], espn["away_abbr"]}
                pid = match_player_to_sleeper(o["player"], allowed_teams, sleeper_index)
                if pid is None:
                    model_status = "player_not_matched"
                    unmatched_players.add(o["player"])
                elif pid not in sleeper_proj:
                    model_status = "no_projection"
                else:
                    stats = sleeper_proj[pid]
                    lam = sum(stats.get(k, 0) or 0 for k in PROP_STAT_KEYS[market])
                    if lam <= 0:
                        model_status = "zero_projected_rate"
                    elif side in ("over", "yes") and line is not None:
                        # e.g. Passing TDs O 1.5 -> P(X > 1.5) = P(X >= 2)
                        model_prob = 1 - poisson.cdf(int(line), lam)
                        model_status = "sleeper_poisson"
                    elif side in ("over", "yes") and line is None:
                        # Anytime TD ("Yes", no explicit numeric line -> >=1)
                        model_prob = 1 - poisson.cdf(0, lam)
                        model_status = "sleeper_poisson"
                    elif side == "under" and line is not None:
                        model_prob = poisson.cdf(int(line), lam)
                        model_status = "sleeper_poisson"
                    elif side == "no":
                        model_prob = poisson.cdf(0, lam)
                        model_status = "sleeper_poisson"

        ev = expected_value_per_dollar(model_prob, o["odds"]) if model_prob is not None else None

        out.append({
            "betNumber": bet_num,
            "nflGame": hr_event_name,
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
              f"matched to a Sleeper roster entry, e.g.: "
              f"{sorted(unmatched_players)[:5]}")
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Fetching ESPN week scoreboard + BPI predictor + DK odds per game...")
    espn_lookup, week = build_espn_lookup()
    print(f"  week {week}: {len(espn_lookup)} ESPN games loaded, "
          f"{sum(1 for v in espn_lookup.values() if v['home_wp'] is not None)} with a BPI predictor, "
          f"{sum(1 for v in espn_lookup.values() if v['book'] is not None)} with book odds.")

    print("Fetching HardRock main lines (ML/spread/total)...")
    hr_main = fetch_hardrock_main_lines()
    print(f"  {len(hr_main)} main-line outcomes.")

    print("Fetching HardRock player props...")
    hr_props = fetch_hardrock_props()
    print(f"  {len(hr_props)} prop outcomes.")

    print("Fetching Sleeper player index + weekly projections...")
    sleeper_state = requests.get(SLEEPER_STATE, timeout=15).json()
    sleeper_season = sleeper_state.get("season")
    sleeper_week = week if week is not None else sleeper_state.get("week")
    sleeper_index = fetch_sleeper_player_index()
    prop_players = {o["player"] for o in hr_props}
    candidate_ids = set()
    for pname in prop_players:
        target = norm_name(pname)
        for pid, info in sleeper_index.items():
            if info["norm_name"] == target:
                candidate_ids.add(pid)
    sleeper_proj = fetch_sleeper_week_projections(candidate_ids, sleeper_season, sleeper_week)
    print(f"  {len(sleeper_index)} skill players indexed, "
          f"{len(candidate_ids)} candidate IDs matched by name, "
          f"{len(sleeper_proj)} with a week-{sleeper_week} projection.")

    main_rows = build_moneyline_and_spread_total_rows(hr_main, espn_lookup)
    prop_out_rows = build_prop_rows(
        hr_props, espn_lookup, sleeper_index, sleeper_proj,
        start_bet_num=len(main_rows) + 1,
    )

    all_rows = main_rows + prop_out_rows
    df = pd.DataFrame(all_rows)

    ts_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    df.insert(0, "scrapedAt", ts_iso)

    # keep the 5 requested columns first, diagnostics after
    lead_cols = ["scrapedAt", "betNumber", "nflGame", "betDescription", "payout", "confidence", "expectedValue"]
    other_cols = [c for c in df.columns if c not in lead_cols]
    df = df[lead_cols + other_cols]

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUT_DIR, f"hardrock_ev_{ts}.csv")
    df.to_csv(out_path, index=False)

    # Prune old per-run snapshots -- the master log below is now the
    # durable history; snapshots are just "what did the slate look like
    # most recently" for /nfl bets, so keep a short rolling window (last
    # 20 runs ~ under 2 days at the every-2h cron cadence) instead of
    # growing this directory forever.
    snapshot_files = sorted(
        f for f in os.listdir(OUT_DIR)
        if f.startswith("hardrock_ev_") and f.endswith(".csv")
    )
    KEEP_SNAPSHOTS = 20
    for stale in snapshot_files[:-KEEP_SNAPSHOTS]:
        os.remove(os.path.join(OUT_DIR, stale))

    # Append EVERY scraped bet (not just +EV) to a running master log, so
    # -EV bets are preserved too -- James wants to study calibration on both
    # sides, not just the flagged ones. Append-only, one row per scrape per
    # outcome; the same outcomeId will recur across scrapes as odds move,
    # which is the point (lets us later measure line movement / CLV).
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
        print(top[["nflGame", "betDescription", "payout", "confidence", "expectedValue", "modelStatus"]].to_string(index=False))
    print("\nNOTE: spread/total EV is relative to DraftKings' devigged line (a "
          "real second book, not a model) and is only computed when the two "
          "books are pricing within 1 point of each other. Longest Reception, "
          "First TD, and Last TD are not modeled (see module docstring) and "
          "always show modelStatus='no_model'. This is a read-only research "
          "snapshot -- log it over multiple weeks and check CLV/actual "
          "outcomes before trusting any single flagged bet.")


if __name__ == "__main__":
    main()
