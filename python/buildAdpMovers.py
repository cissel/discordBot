#!/usr/bin/env python3
"""
buildAdpMovers.py

Phase 3 extension: track ADP drift across the daily snapshot history
(adp_history.csv, built by fetchNflAdpAndRoster.py + fetchEspnAdp.py since
2026-08-01) to see who's gaining or losing steam heading into the season.

Compares the EARLIEST available snapshot per source against the LATEST
(today's) snapshot for every player present in both, per source (Sleeper
and ESPN kept separate rather than blended - different ADP scales/pools,
see buildAdpDispersion.py for why they're not directly comparable row-for-
row). Positive adp_delta = ADP number went UP = player is being drafted
LATER now = losing steam. Negative = climbing draft boards = gaining steam.

Usage: venv/bin/python3 python/buildAdpMovers.py
Output: outputs/sports/nfl/fantasy/adp_movers_sleeper.csv
        outputs/sports/nfl/fantasy/adp_movers_espn.csv
"""
import os

import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
HISTORY_PATH = os.path.join(OUT_DIR, "adp_history.csv")

MIN_ADP_SAMPLES = 2  # need at least first + last snapshot to compute a delta


def build_movers(hist: pd.DataFrame, source: str) -> pd.DataFrame:
    sub = hist[hist["source"] == source].copy()
    sub["snapshot_date"] = pd.to_datetime(sub["snapshot_date"])

    dates = sorted(sub["snapshot_date"].unique())
    if len(dates) < MIN_ADP_SAMPLES:
        print(f"[{source}] Not enough snapshot dates ({len(dates)}) - skipping")
        return pd.DataFrame()

    first_date, last_date = dates[0], dates[-1]
    print(f"[{source}] Comparing {first_date.date()} -> {last_date.date()} "
          f"({len(dates)} snapshot days)")

    # keep a stable player key: player_id for sleeper (has one), full_name for espn
    key = "player_id" if source == "sleeper" and sub["player_id"].notna().any() else "full_name"

    cols = [key, "position", "team", "adp_overall"]
    if key != "full_name":
        cols.insert(1, "full_name")

    first_snap = sub[sub["snapshot_date"] == first_date][cols]
    last_snap = sub[sub["snapshot_date"] == last_date][cols]

    # dedupe on key (rare same-name collisions in the ESPN name-only key space -
    # keep the lowest ADP entry as a defensible tiebreak, not a real fix but
    # avoids many-to-many merge blowups for the handful of affected rows)
    first_snap = first_snap.sort_values("adp_overall").drop_duplicates(subset=[key], keep="first")
    last_snap = last_snap.sort_values("adp_overall").drop_duplicates(subset=[key], keep="first")

    merged = first_snap.merge(
        last_snap, on=key, how="inner", suffixes=("_first", "_last")
    )
    if key == "full_name":
        merged["full_name"] = merged[key]
    else:
        merged["full_name"] = merged["full_name_last"].fillna(merged["full_name_first"])
    merged["position"] = merged["position_last"].fillna(merged["position_first"])
    merged["team"] = merged["team_last"].fillna(merged["team_first"])
    merged["adp_delta"] = merged["adp_overall_last"] - merged["adp_overall_first"]
    merged["first_snapshot_date"] = first_date.date()
    merged["last_snapshot_date"] = last_date.date()

    out = merged[[
        "full_name", "position", "team",
        "adp_overall_first", "adp_overall_last", "adp_delta",
        "first_snapshot_date", "last_snapshot_date",
    ]].rename(columns={
        "adp_overall_first": "adp_first", "adp_overall_last": "adp_last",
    }).sort_values("adp_delta")

    return out


def main():
    hist = pd.read_csv(HISTORY_PATH)

    for source in ["sleeper", "espn"]:
        movers = build_movers(hist, source)
        if movers.empty:
            continue
        out_path = os.path.join(OUT_DIR, f"adp_movers_{source}.csv")
        movers.to_csv(out_path, index=False)
        print(f"Wrote: {out_path} ({len(movers)} rows)\n")

        print(f"=== {source.upper()}: Top 15 GAINING steam (ADP dropping / climbing draft boards) ===")
        print(movers.sort_values("adp_delta").head(15)
              [["full_name", "position", "team", "adp_first", "adp_last", "adp_delta"]]
              .to_string(index=False))

        print(f"\n=== {source.upper()}: Top 15 LOSING steam (ADP rising / falling draft boards) ===")
        print(movers.sort_values("adp_delta", ascending=False).head(15)
              [["full_name", "position", "team", "adp_first", "adp_last", "adp_delta"]]
              .to_string(index=False))
        print()


if __name__ == "__main__":
    main()
