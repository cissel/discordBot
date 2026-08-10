"""wordleScores.py

Parses Wordle results out of the server message log (outputs/metrics/server_messages.csv)
and writes a tidy (date, player, score, source) CSV for plotting.

Two source patterns are recognized:
  1. Self-reported share messages: "Wordle 1,836 2/6" (posted by the player themself)
  2. The Wordle bot's daily recap: "**Your group is on a N day streak!** ... yesterday's
     results:\n👑 X/6: <@id> <@id2>\nY/6: @PlainMention ..."
     -- these recap the PREVIOUS calendar day's results (America/New_York).

Player identity from the recap can be either a raw Discord mention <@id> (resolved via
the guild member cache/fetch) or a plain "@Name" text mention with no ID. Names are
normalized (case/whitespace/punctuation-insensitive) so the same player reported both
ways collapses into a single series; self-reports take priority over recap rows when
both exist for the same player/date.
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

SELF_REPORT_RE   = re.compile(r"Wordle\s+[\d,]+\s+([1-6]|X)/6", re.IGNORECASE)
RECAP_LINE_RE    = re.compile(r"^(?:\U0001F451\s*)?([1-6]|X)/6:\s*(.*)$")
ID_MENTION_RE    = re.compile(r"<@!?(\d+)>")
PLAIN_MENTION_RE = re.compile(r"@[^@]+")

SCORE_VALUE = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "X": 7}


def _ny_date(iso_ts: str):
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY_TZ).date()


def _norm_key(name: str) -> str:
    name = re.sub(r"[^\w\s]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", name).strip().lower()


def _clean_display(name: str) -> str:
    """Strip leading/trailing emoji/symbol decoration (e.g. '🧢 Zooksy 🧢' -> 'Zooksy')
    so plot legends don't show tofu boxes for missing emoji glyphs in the R font."""
    core = re.sub(r"^[^\w]+|[^\w]+$", "", name, flags=re.UNICODE)
    return core if core else name


async def build_wordle_scores_csv(guild, csv_in: str, csv_out: str) -> dict:
    """Parse csv_in (server_messages.csv) and write csv_out (date,player,score,source).

    Returns a summary dict: {rows, players, date_min, date_max}.
    Raises FileNotFoundError if csv_in doesn't exist.
    """
    if not os.path.exists(csv_in):
        raise FileNotFoundError(csv_in)

    with open(csv_in, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # ── resolve every <@id> mention seen in bot recap rows to a display name ──
    ids_seen = set()
    for row in rows:
        if row.get("user_name") != "Wordle":
            continue
        for m in ID_MENTION_RE.finditer(row.get("message", "") or ""):
            ids_seen.add(m.group(1))

    id_to_name = {}
    for uid in ids_seen:
        name = None
        if guild is not None:
            member = guild.get_member(int(uid))
            if member is None:
                try:
                    member = await guild.fetch_member(int(uid))
                except Exception:
                    member = None
            if member is not None:
                name = member.display_name
        id_to_name[uid] = name or f"user_{uid}"

    canon = {}
    for name in id_to_name.values():
        canon.setdefault(_norm_key(name), name)

    def canon_name(raw: str) -> str:
        raw = _clean_display(raw)
        key = _norm_key(raw)
        if key in canon:
            return canon[key]
        canon[key] = raw
        return raw

    records = {}  # (date, norm_key) -> row dict

    def _add(date, player_raw, score, source):
        player = canon_name(player_raw)
        key = (date, _norm_key(player))
        existing = records.get(key)
        if existing is not None and existing["source"] == "self":
            return
        records[key] = {"date": date, "player": player, "score": score, "source": source}

    # ── pass 1: self-reported share messages ──
    for row in rows:
        if row.get("user_name") == "Wordle":
            continue
        msg = row.get("message", "") or ""
        m = SELF_REPORT_RE.search(msg)
        if not m:
            continue
        try:
            date = _ny_date(row["datetime"])
        except Exception:
            continue
        player = row.get("user_display_name") or row.get("user_name") or "unknown"
        _add(date, player, m.group(1).upper(), "self")

    # ── pass 2: bot daily recap messages (results are for the PRIOR day) ──
    for row in rows:
        if row.get("user_name") != "Wordle":
            continue
        msg = row.get("message", "") or ""
        if "yesterday" not in msg.lower():
            continue
        try:
            msg_date = _ny_date(row["datetime"])
        except Exception:
            continue
        result_date = msg_date - timedelta(days=1)

        for line in msg.split("\\n"):
            line = line.strip()
            lm = RECAP_LINE_RE.match(line)
            if not lm:
                continue
            score = lm.group(1).upper()
            rest = lm.group(2)

            for h in ID_MENTION_RE.finditer(rest):
                _add(result_date, id_to_name.get(h.group(1), f"user_{h.group(1)}"), score, "recap")

            rest_wo_ids = ID_MENTION_RE.sub("", rest)
            for pm in PLAIN_MENTION_RE.finditer(rest_wo_ids):
                name = pm.group(0)[1:].strip()
                if name:
                    _add(result_date, name, score, "recap")

    out_rows = sorted(records.values(), key=lambda r: (r["date"], r["player"]))
    os.makedirs(os.path.dirname(csv_out), exist_ok=True)
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "player", "score", "score_value"])
        w.writeheader()
        for r in out_rows:
            w.writerow({
                "date": r["date"].isoformat(),
                "player": r["player"],
                "score": r["score"],
                "score_value": SCORE_VALUE.get(r["score"], ""),
            })

    players = sorted({r["player"] for r in out_rows})
    dates = [r["date"] for r in out_rows]
    return {
        "rows": len(out_rows),
        "players": players,
        "date_min": min(dates).isoformat() if dates else None,
        "date_max": max(dates).isoformat() if dates else None,
    }
