"""maptapScores.py

Parses MapTap results out of the server message log (outputs/metrics/server_messages.csv)
and writes a tidy (date, player, final_score) CSV for plotting.

MapTap posts are always self-reported share messages of the form:
    www.maptap.gg <Month> <Day>
    <round1><emoji> <round2><emoji> ... <round5><emoji>
    Final score: <int>

The date in the message text is the day the round was PLAYED (no year), which can
differ from the day it was POSTED (e.g. posting a previous day's result after
midnight). We attach the year from the post timestamp, then roll back a year if
the parsed month/day would otherwise land more than a few days in the future
relative to the post (handles New Year's Eve/Day edge cases).

Duplicate posts (same player re-sharing the same day's result) are deduped,
keeping the first-seen score for that player/date.
"""

import csv
import os
import re
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    NY_TZ = ZoneInfo("America/New_York")
except Exception:
    NY_TZ = timezone(timedelta(hours=-4))

DATE_RE  = re.compile(r"maptap\.gg\s+([A-Za-z]+)\s+(\d{1,2})", re.IGNORECASE)
SCORE_RE = re.compile(r"Final score:\s*(\d+)", re.IGNORECASE)

# Minimum number of logged games a player needs to appear on the chart. Players
# with only a single stray game clutter the plot as isolated dots with no trend.
MIN_GAMES = 2

# Manual identity aliases: different display names known to be the same real person.
# Keys are normalized (lowercase, punctuation-stripped) source names; values are the
# canonical display name they should be unified under.
NAME_ALIASES = {
    "james": "Hale",
}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _ny_dt(iso_ts: str) -> datetime:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY_TZ)


def _norm_key(name: str) -> str:
    name = re.sub(r"[^\w\s]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", name).strip().lower()


def _clean_display(name: str) -> str:
    """Strip leading/trailing emoji/symbol decoration (e.g. '🧢 Zooksy 🧢' -> 'Zooksy')
    so plot legends don't show tofu boxes for missing emoji glyphs in the R font."""
    core = re.sub(r"^[^\w]+|[^\w]+$", "", name, flags=re.UNICODE)
    return core if core else name


def parse_maptap_records(csv_in) -> list:
    """Parse csv_in (server_messages.csv) into the full, unfiltered list of
    canonicalized MapTap records: [{date, player, final_score}, ...], sorted by
    (date, player). This is the single source of truth for a player's history -
    both the chart builder and the live high-score check derive from it, so
    identity aliasing (NAME_ALIASES) and dedup rules stay consistent everywhere.

    Raises FileNotFoundError if csv_in doesn't exist.
    """
    if not os.path.exists(csv_in):
        raise FileNotFoundError(csv_in)

    with open(csv_in, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    canon = {}

    def canon_name(raw: str) -> str:
        raw = _clean_display(raw)
        key = _norm_key(raw)
        if key in NAME_ALIASES:
            raw = NAME_ALIASES[key]
            key = _norm_key(raw)
        if key in canon:
            return canon[key]
        canon[key] = raw
        return raw

    records = {}  # (date, norm_key) -> {date, player, final_score}

    for row in rows:
        msg = row.get("message", "") or ""
        dm = DATE_RE.search(msg)
        sm = SCORE_RE.search(msg)
        if not dm or not sm:
            continue

        month_name = dm.group(1).lower()
        month = MONTHS.get(month_name)
        if month is None:
            continue
        day = int(dm.group(2))

        try:
            post_dt = _ny_dt(row["datetime"])
        except Exception:
            continue

        year = post_dt.year
        try:
            played_date = datetime(year, month, day).date()
        except ValueError:
            continue
        # If parsed date is more than 3 days ahead of the post date, it must be last year
        if (played_date - post_dt.date()).days > 3:
            played_date = datetime(year - 1, month, day).date()

        player = canon_name(row.get("user_display_name") or row.get("user_name") or "unknown")
        score = int(sm.group(1))

        key = (played_date, _norm_key(player))
        if key in records:
            continue  # keep first-seen for this player/date
        records[key] = {"date": played_date, "player": player, "final_score": score}

    return sorted(records.values(), key=lambda r: (r["date"], r["player"]))


def _canon_key_for(raw_name: str) -> str:
    """Normalize+alias a raw display name to the same identity key used in
    parse_maptap_records, without needing a full parse pass."""
    cleaned = _clean_display(raw_name)
    key = _norm_key(cleaned)
    if key in NAME_ALIASES:
        key = _norm_key(NAME_ALIASES[key])
    return key


def check_new_high(csv_in, user_display_name: str, message_text: str, post_dt: datetime) -> dict | None:
    """Live-message hook: given a just-posted message, determine whether it's a
    MapTap score share and, if so, whether it's a new personal high score for
    that player.

    Returns None if message_text isn't a MapTap score share (or is unparseable).
    Otherwise returns:
        {"player": <canonical display name>, "score": int,
         "is_first_game": bool, "is_new_high": bool, "prior_max": int|None}
    is_first_game=True means this is the player's only logged game so far -
    callers should NOT congratulate on a "high score" that has no prior baseline.
    """
    dm = DATE_RE.search(message_text)
    sm = SCORE_RE.search(message_text)
    if not dm or not sm:
        return None

    month = MONTHS.get(dm.group(1).lower())
    if month is None:
        return None
    day = int(dm.group(2))

    if post_dt.tzinfo is None:
        post_dt = post_dt.replace(tzinfo=timezone.utc)
    post_dt = post_dt.astimezone(NY_TZ)

    year = post_dt.year
    try:
        played_date = datetime(year, month, day).date()
    except ValueError:
        return None
    if (played_date - post_dt.date()).days > 3:
        played_date = datetime(year - 1, month, day).date()

    score = int(sm.group(1))

    records = parse_maptap_records(csv_in)
    target_key = _canon_key_for(user_display_name)
    player_rows = [r for r in records if _norm_key(r["player"]) == target_key]
    if not player_rows:
        return None  # shouldn't happen - this message should already be parsed into records

    canonical_display = player_rows[0]["player"]

    this_row = next(
        (r for r in player_rows if r["date"] == played_date and r["final_score"] == score),
        None,
    )
    prior_rows = [r for r in player_rows if r is not this_row]

    if not prior_rows:
        return {
            "player": canonical_display,
            "score": score,
            "is_first_game": True,
            "is_new_high": False,
            "prior_max": None,
        }

    prior_max = max(r["final_score"] for r in prior_rows)
    return {
        "player": canonical_display,
        "score": score,
        "is_first_game": False,
        "is_new_high": score > prior_max,
        "prior_max": prior_max,
    }


def build_maptap_scores_csv(csv_in: str, csv_out: str) -> dict:
    """Parse csv_in (server_messages.csv) and write csv_out (date,player,final_score),
    dropping players who haven't logged enough games (MIN_GAMES) to show a trend.

    Returns a summary dict: {rows, players, date_min, date_max}.
    Raises FileNotFoundError if csv_in doesn't exist.
    """
    out_rows = parse_maptap_records(csv_in)

    # Drop players who haven't logged enough games to show a meaningful trend
    # (e.g. a single stray share) rather than cluttering the chart as lone dots.
    game_counts = {}
    for r in out_rows:
        game_counts[r["player"]] = game_counts.get(r["player"], 0) + 1
    out_rows = [r for r in out_rows if game_counts[r["player"]] >= MIN_GAMES]

    os.makedirs(os.path.dirname(csv_out), exist_ok=True)
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "player", "final_score"])
        w.writeheader()
        for r in out_rows:
            w.writerow({
                "date": r["date"].isoformat(),
                "player": r["player"],
                "final_score": r["final_score"],
            })

    players = sorted({r["player"] for r in out_rows})
    dates = [r["date"] for r in out_rows]
    return {
        "rows": len(out_rows),
        "players": players,
        "date_min": min(dates).isoformat() if dates else None,
        "date_max": max(dates).isoformat() if dates else None,
    }
