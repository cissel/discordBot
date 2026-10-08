# osrsLevelHistory.py
# Daily snapshot of OSRS levels + XP (per-skill and total) for the crew,
# appended to a long-running history CSV for tracking progress over time.
#
# Reuses PLAYERS/SKILLS/fetch_player from osrsHiscores.py so the roster
# stays in one place.
#
# Usage: python3 osrsLevelHistory.py
#
# Writes/appends: outputs/osrs/level_history.csv
#   columns: date, player, skill, level, rank, xp
#
# Idempotent: if today's date is already fully logged, does nothing
# (safe to re-run / re-trigger the cron job manually).

import csv
import time
from datetime import date
from pathlib import Path

from osrsHiscores import PLAYERS, SKILLS, fetch_player

HISTORY = Path("~/discordBot/outputs/osrs/level_history.csv").expanduser()
HISTORY.parent.mkdir(parents=True, exist_ok=True)

FIELDNAMES = ["date", "player", "skill", "level", "rank", "xp"]


def already_logged_today(today_str):
    if not HISTORY.exists():
        return False
    with open(HISTORY, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date") == today_str:
                return True
    return False


def main():
    today_str = date.today().isoformat()

    if already_logged_today(today_str):
        print(f"[osrsLevelHistory] {today_str} already logged, skipping")
        return

    write_header = not HISTORY.exists()
    rows_written = 0

    with open(HISTORY, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()

        for rsn in PLAYERS:
            data = fetch_player(rsn)
            time.sleep(0.3)  # be polite to Jagex servers
            if not data:
                print(f"[osrsLevelHistory] {rsn}: no data, skipping")
                continue
            for skill in SKILLS:
                s = data.get(skill)
                if not s or s["level"] <= 0:
                    continue
                w.writerow({
                    "date":   today_str,
                    "player": rsn,
                    "skill":  skill,
                    "level":  s["level"],
                    "rank":   s["rank"] if s["rank"] else "unranked",
                    "xp":     s["xp"],
                })
                rows_written += 1

    print(f"[osrsLevelHistory] {today_str}: wrote {rows_written} rows for {len(PLAYERS)} players")


if __name__ == "__main__":
    main()
