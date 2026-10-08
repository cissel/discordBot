#!/usr/bin/env python3
"""
osrsLevelupsImport.py  --  merge RuneLite "level up" screenshot events
(pulled on James's laptop via a separate Hermes agent, OCR'd into a CSV)
into the canonical /osrs history data file (level_history.csv).

Source CSV columns: character, skill, level, date, time, datetime
  - one row per level-up notification (the level JUST reached, that day).
  - `time` values are corrupted OCR garbage (e.g. "21:-2:1-") - unusable,
    and not needed anyway since level_history.csv is date-granularity only.
  - no XP value exists in a level-up screenshot, so imported rows get xp
    set to the OFFICIAL OSRS xp-required-for-level threshold (computed via
    the standard Jagex formula, not fabricated/estimated). This is a lower
    bound / floor: true xp at the moment of a level-up screenshot is that
    threshold or very slightly above it (you cross the threshold exactly
    when the level-up fires). Close enough to plot meaningfully on the
    existing /osrs history XP chart; NOT exact captured xp like the daily
    cron snapshot gets from the live hiscores API. Caveat worth remembering
    if anyone eyeballs small single-day xp deltas in this range.

Identity resolution (per James, confirmed 2026-10-07): "cissel",
"severaljames", and "captdeadhead" are all the same person/account
(renamed over time) -> canonicalized to "captdeadhead".

Excluded by default (ask before changing):
  - "combat" skill rows: combat level is a derived stat from other skills,
    not one of the 24 trainable skills OSRS_SKILLS tracks; it was never
    part of level_history.csv's skill set (verified: not present in the
    existing file), so dropped here too for consistency.
  - "severaljames-Grid Master" / "severaljames-Raging Echoes League":
    these are OSRS Leagues (seasonal, temporary, fresh-start) character
    variants, not the persistent main account. Their levels reflect a
    completely different, short-lived character and would corrupt the
    long-run captdeadhead progression chart if merged in. Left out of
    level_history.csv; written instead to level_history_leagues_excluded.csv
    for inspection in case James wants a separate leagues chart later.

Merge rules (consistent with osrsHistoryMerge.py):
  1. Multiple level-ups in the same skill on the same calendar date -> keep
     the HIGHEST level reached that day (end-of-day state; level only
     increases).
  2. Never overwrite a (date, player, skill) combo that already exists in
     level_history.csv - existing rows (daily cron w/ real XP from the
     hiscores API, or prior backfill) are authoritative. New rows only
     fill genuinely missing dates.
  3. Backs up level_history.csv to level_history.csv.bak2 before writing.

Usage: python3 osrsLevelupsImport.py <path_to_levelups_csv> [--write]
  (dry run by default - prints what WOULD be added; --write actually merges)
"""
import sys
import shutil
from pathlib import Path

import pandas as pd

OUT_DIR = Path("~/discordBot/outputs/osrs").expanduser()
HISTORY = OUT_DIR / "level_history.csv"
BACKUP = OUT_DIR / "level_history.csv.bak2"
EXCLUDED_LEAGUES_OUT = OUT_DIR / "level_history_leagues_excluded.csv"

RENAMES = {
    "cissel": "captdeadhead",
    "severaljames": "captdeadhead",
}
DROP_SKILLS = {"combat"}
LEAGUE_SUFFIXES = ("-grid master", "-raging echoes league")


def xp_for_level(level: int) -> int:
    """Official OSRS xp-required-for-level table (Jagex formula), level 1-99.
    xp(1)=0; for lvl>=2: floor( sum_{n=1}^{lvl-1} floor(n + 300*2^(n/7)) / 4 )
    Returns the exact threshold xp at which that level is first reached.
    """
    if level <= 1:
        return 0
    total = 0
    for n in range(1, level):
        total += int(n + 300.0 * (2 ** (n / 7.0)))
    return total // 4


def canonicalize(raw_name: str):
    key = raw_name.strip().lower()
    if any(key.endswith(suf) for suf in LEAGUE_SUFFIXES):
        return None, "league_variant"
    if key in RENAMES:
        return RENAMES[key], None
    return raw_name.strip(), None


def main():
    if len(sys.argv) < 2:
        print("Usage: osrsLevelupsImport.py <levelups.csv> [--write]")
        sys.exit(1)
    src_path = Path(sys.argv[1]).expanduser()
    write_mode = "--write" in sys.argv[2:]

    raw = pd.read_csv(src_path)
    n_raw = len(raw)

    raw["skill"] = raw["skill"].str.strip().str.lower()
    raw = raw[~raw["skill"].isin(DROP_SKILLS)].copy()
    n_after_combat_drop = len(raw)

    canon = raw["character"].apply(canonicalize)
    raw["canon_player"] = canon.apply(lambda t: t[0])
    raw["drop_reason"] = canon.apply(lambda t: t[1])

    league_rows = raw[raw["canon_player"].isna()].copy()
    if not league_rows.empty:
        league_rows[["character", "skill", "level", "date"]].to_csv(
            EXCLUDED_LEAGUES_OUT, index=False
        )

    raw = raw[raw["canon_player"].notna()].copy()
    raw["player"] = raw["canon_player"]
    n_after_league_drop = len(raw)

    # rule 1: same (date, player, skill) -> keep max level that day
    raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
    grouped = (
        raw.groupby(["date", "player", "skill"], as_index=False)["level"]
        .max()
    )
    n_after_samedaydedup = len(grouped)

    grouped["rank"] = "unranked"
    grouped["xp"] = grouped["level"].apply(xp_for_level)

    cron = pd.read_csv(HISTORY)
    cron["date"] = cron["date"].astype(str)
    existing_keys = set(
        zip(cron["date"], cron["player"].str.lower(), cron["skill"].str.lower())
    )

    grouped["_key"] = list(
        zip(grouped["date"], grouped["player"].str.lower(), grouped["skill"].str.lower())
    )
    new_rows = grouped[~grouped["_key"].isin(existing_keys)].drop(columns=["_key"])
    n_new = len(new_rows)

    print(f"Raw rows in source CSV:                 {n_raw}")
    print(f"After dropping 'combat' rows:            {n_after_combat_drop}")
    print(f"After dropping league variant rows:      {n_after_league_drop}  "
          f"(excluded: {len(league_rows)} -> {EXCLUDED_LEAGUES_OUT if not league_rows.empty else 'n/a'})")
    print(f"After same-day same-skill dedup (max lvl): {n_after_samedaydedup}")
    print(f"New rows not already in level_history.csv: {n_new}")
    print()
    print("New rows by player:")
    print(new_rows["player"].value_counts().to_string())
    print()
    print("New rows by skill (top 10):")
    print(new_rows["skill"].value_counts().head(10).to_string())
    print()
    print(f"Date range of new rows: {new_rows['date'].min()} to {new_rows['date'].max()}")

    if not write_mode:
        print("\n[dry run - pass --write to actually merge into level_history.csv]")
        return

    final_new = new_rows[["date", "player", "skill", "level", "rank", "xp"]]
    combined = pd.concat([cron, final_new], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "player", "skill"]).reset_index(drop=True)
    combined["date"] = combined["date"].dt.strftime("%Y-%m-%d")

    shutil.copy2(HISTORY, BACKUP)
    combined.to_csv(HISTORY, index=False)
    print(f"\nBackup written: {BACKUP}")
    print(f"Merged history written: {HISTORY} ({len(combined)} total rows, +{n_new})")


if __name__ == "__main__":
    main()
