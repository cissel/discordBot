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


def build_maptap_scores_csv(csv_in: str, csv_out: str) -> dict:
    """Parse csv_in (server_messages.csv) and write csv_out (date,player,final_score).

    Returns a summary dict: {rows, players, date_min, date_max}.
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

    out_rows = sorted(records.values(), key=lambda r: (r["date"], r["player"]))
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
