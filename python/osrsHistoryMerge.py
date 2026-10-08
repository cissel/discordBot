#!/usr/bin/env python3
"""
osrsHistoryMerge.py  --  clean + merge the chat-derived backfill data
(level_history_backfill.csv, produced by osrsHistoryBackfill.py) into the
canonical daily-cron history file (level_history.csv) used by /osrs history.

Cleaning rules applied (see inline comments for rationale):
  1. Drop level == 0 rows - OSRS accounts can't have total level 0 (min is 32);
     a 0 here means "unranked / not found on hiscores API at lookup time",
     not a real data point. Fabricating a "level 0" day would wreck any chart.
  2. Canonicalize RSN case variants (pexci/Pexci, swampdog/Swampdog, etc.) to
     the capitalization used in OSRS_PLAYERS (commands.py / osrsHiscores.py).
  3. Apply the one CONFIRMED rename found by cross-checking: "cissel" ->
     "captdeadhead". Verified by exact level+xp match across all 25 skills
     on the last cissel-labeled snapshot (2026-04-08) vs the first
     captdeadhead-labeled snapshot the very next day (2026-04-09) - same
     account, renamed RSN, not a coincidence.
  4. DROP unmapped names that aren't in the current 9-player roster and have
     no confirmed crosswalk: "The Bog Body", "Zezima", "Omar", "Hale",
     "Delpius", "zooksy". These look like one-off lookups of other people's
     accounts (friends, jokes, hiscore curiosity) mixed into the same
     channels, not crew members. Not merged in - see backfill CSV if you
     want to inspect them raw.
  5. When multiple chat snapshots land on the same calendar date for the
     same (player, skill), keep the LATEST one that day (end-of-day state,
     consistent with how the daily cron snapshot represents "today").
  6. Never overwrite a date already covered by the authoritative daily cron
     log (>= first cron date). Chat-derived data only fills gaps BEFORE or
     BETWEEN cron coverage.

Writes a backup of the original level_history.csv (level_history.csv.bak)
before overwriting it with the merged, date-sorted result.
"""
import shutil
from pathlib import Path

import pandas as pd

OUT_DIR   = Path("~/discordBot/outputs/osrs").expanduser()
CRON_CSV  = OUT_DIR / "level_history.csv"
RAW_CSV   = OUT_DIR / "level_history_backfill.csv"
CLEAN_CSV = OUT_DIR / "level_history_backfill_clean.csv"
BACKUP    = OUT_DIR / "level_history.csv.bak"

CANONICAL_PLAYERS = [
    "captdeadhead", "SubieVapeski", "Pexci", "swampdog",
    "Squidlies", "Wmwhite", "TrimIsLife", "Fart Johnsun",
    "carpetweed",
]
CANON_LOOKUP = {p.lower(): p for p in CANONICAL_PLAYERS}

RENAMES = {
    "cissel": "captdeadhead",   # confirmed exact 25-skill match, 2026-04-08 -> 04-09
}

DROPPED_UNMAPPED = {"the bog body", "zezima", "omar", "hale", "delpius", "zooksy"}


def canonicalize_player(raw_name: str):
    key = raw_name.strip().lower()
    if key in RENAMES:
        key = RENAMES[key].lower()
    if key in DROPPED_UNMAPPED:
        return None
    return CANON_LOOKUP.get(key)  # None if truly unrecognized


def main():
    raw = pd.read_csv(RAW_CSV)
    cron = pd.read_csv(CRON_CSV)
    cron["date"] = cron["date"].astype(str)

    n_raw = len(raw)

    # 1. drop level==0 (non-data / unranked placeholder)
    raw = raw[raw["level"] > 0].copy()
    n_after_zero = len(raw)

    # 2+3+4. canonicalize / rename / drop unmapped
    raw["canon_player"] = raw["player"].apply(canonicalize_player)
    dropped_unmapped_rows = raw[raw["canon_player"].isna()]["player"].unique().tolist()
    raw = raw[raw["canon_player"].notna()].copy()
    raw["player"] = raw["canon_player"]
    raw = raw.drop(columns=["canon_player"])
    n_after_map = len(raw)

    # 5. same (date, player, skill) on the same day -> keep latest msg
    raw = raw.sort_values("msg_created_utc")
    raw = raw.drop_duplicates(subset=["date", "player", "skill"], keep="last")
    n_after_dedup = len(raw)

    # 6. never overwrite dates already in the cron log
    cron_dates = set(cron["date"].unique())
    raw_before_cron_filter = len(raw)
    raw = raw[~raw["date"].isin(cron_dates)].copy()
    n_after_cron_filter = len(raw)

    # final shape matching cron schema
    raw["rank"] = raw["rank"].fillna("unranked")
    raw["xp"] = pd.to_numeric(raw["xp"], errors="coerce")
    merged_backfill = raw[["date", "player", "skill", "level", "rank", "xp"]].copy()
    merged_backfill.to_csv(CLEAN_CSV, index=False)

    # merge into canonical file (cron rows untouched/authoritative, backfill fills gaps)
    combined = pd.concat([cron, merged_backfill], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "player", "skill"]).reset_index(drop=True)
    combined["date"] = combined["date"].dt.strftime("%Y-%m-%d")

    shutil.copy2(CRON_CSV, BACKUP)
    combined.to_csv(CRON_CSV, index=False)

    print(f"Raw backfill rows:                  {n_raw}")
    print(f"After dropping level==0:            {n_after_zero}")
    print(f"After player canonicalize/drop:     {n_after_map}  (dropped unmapped: {sorted(dropped_unmapped_rows)})")
    print(f"After same-day dedup (keep latest):  {n_after_dedup}")
    print(f"After dropping cron-covered dates:   {n_after_cron_filter}  (of {raw_before_cron_filter})")
    print(f"Clean backfill written:             {CLEAN_CSV}  ({len(merged_backfill)} rows)")
    print(f"Backup of original cron CSV:        {BACKUP}")
    print(f"Merged history written:             {CRON_CSV}  ({len(combined)} total rows)")
    print()
    print("Dates added to history (previously missing):")
    new_dates = sorted(set(merged_backfill["date"].unique()))
    for d in new_dates:
        n = (merged_backfill["date"] == d).sum()
        players_that_day = sorted(merged_backfill[merged_backfill["date"] == d]["player"].unique())
        print(f"  {d}: {n} rows - players: {', '.join(players_that_day)}")


if __name__ == "__main__":
    main()
