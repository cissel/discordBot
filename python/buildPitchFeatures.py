#!/usr/bin/env python3
"""
buildPitchFeatures.py
=====================
Aggregates pitch-level Statcast data into per-player rolling features
that get joined into batter_features.csv.

Run after fetchStatcastDaily.py, before trainFantasyModel.py.

For each batter × game_date row in batter_features.csv, computes:
  - Rolling 7/14-day aggregates from the pitch-level data
  - Features are computed using only pitches BEFORE the game_date (no leakage)

New feature columns added to batter_features.csv:

  Swing quality (last 14 days):
    bat_speed_r14         - avg bat speed on swings
    swing_length_r14      - avg swing length
    attack_angle_r14      - avg attack angle
    sweet_spot_pct_r14    - % pitches with launch angle 8-32 degrees

  Pitch mix faced (last 14 days):
    fastball_pct_r14      - % fastballs (FF, SI, FC) seen
    breaking_pct_r14      - % breaking balls (SL, ST, CU, KC) seen
    offspeed_pct_r14      - % offspeed (CH, FS) seen

  Zone discipline (last 14 days):
    chase_rate_r14        - swing% on pitches outside zone (zone 11-14)
    zone_contact_r14      - contact% on pitches inside zone
    whiff_rate_r14        - swing-and-miss / total swings
    first_pitch_strike_r14 - % PA where first pitch was a strike

  Quality of contact (last 14 days):
    xba_r14               - avg xBA on balls in play
    xwoba_r14_pitch       - avg xwOBA on balls in play (pitch-level, richer than game-log)
    hard_contact_r14      - % batted balls with EV >= 95mph
    barrel_rate_r14       - % batted balls that were barrels (LA 8-32, EV >= 98)

  Situational (last 14 days):
    times_thru_order_r14  - avg n_thruorder_pitcher when making contact
    count_leverage_r14    - avg delta_run_exp per PA

  Confirmed batting order (from boxscore, via day-of lineups):
    batting_order_r7      - already in features; this replaces BatOrder_r7 with
                            actual confirmed slot where available
"""

import os
import glob
import datetime
import numpy as np
import pandas as pd

STATCAST_DIR = os.path.expanduser("~/discordBot/outputs/sports/mlb/statcast")
FEAT_PATH    = os.path.expanduser("~/discordBot/outputs/features/sports/batter_features.csv")

# Pitch type families
FASTBALL_TYPES  = {"FF", "SI", "FC", "FT", "FA"}
BREAKING_TYPES  = {"SL", "ST", "CU", "KC", "CS", "SV", "KN"}
OFFSPEED_TYPES  = {"CH", "FS", "FO", "SC"}

# Zone numbers: 1-9 = strike zone, 11-14 = outside zone
IN_ZONE  = set(range(1, 10))
OUT_ZONE = {11, 12, 13, 14}

# Sweet spot: launch angle 8-32 degrees
SWEET_SPOT_LOW  = 8
SWEET_SPOT_HIGH = 32

# Hard contact: EV >= 95mph; Barrel: EV >= 98 AND LA 8-32
HARD_EV_THRESH   = 95.0
BARREL_EV_THRESH = 98.0


def load_statcast(season_year=None):
    """Load all monthly statcast CSVs, optionally filter by year."""
    pattern = os.path.join(STATCAST_DIR, "statcast_*.csv")
    files   = sorted(glob.glob(pattern))
    if not files:
        print(f"  [warn] no statcast files found in {STATCAST_DIR}")
        return pd.DataFrame()

    frames = []
    for f in files:
        if season_year and str(season_year) not in os.path.basename(f):
            continue
        df = pd.read_csv(f, low_memory=False)
        frames.append(df)
        print(f"  loaded {os.path.basename(f)}: {len(df):,} rows")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["game_date"] = pd.to_datetime(combined["game_date"])
    print(f"  total statcast rows: {len(combined):,}")
    return combined


def is_swing(description):
    """Return True if pitch resulted in a swing."""
    return str(description) in {
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip", "foul_bunt",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
        "missed_bunt",
    }


def is_contact(description):
    """Return True if pitch resulted in contact (ball in play or foul)."""
    return str(description) in {
        "foul", "foul_tip", "foul_bunt",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    }


def is_bip(description):
    """Ball in play (not foul)."""
    return str(description) in {
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    }


def compute_pitch_features_for_player(player_pitches, target_date, window_days=14):
    """
    Given all pitch rows for one batter (sorted by game_date),
    compute rolling features for the window ending before target_date.
    Returns a dict of feature values.
    """
    cutoff_start = target_date - pd.Timedelta(days=window_days)
    window = player_pitches[
        (player_pitches["game_date"] >= cutoff_start) &
        (player_pitches["game_date"] <  target_date)
    ]

    if len(window) < 5:  # not enough data
        return {}

    feats = {}

    # ── swing quality ─────────────────────────────────────────────────────
    swings = window[window["description"].apply(is_swing)]
    if len(swings) > 0 and "bat_speed" in swings.columns:
        bs = swings["bat_speed"].dropna()
        sl = swings["swing_length"].dropna() if "swing_length" in swings.columns else pd.Series(dtype=float)
        aa = swings["attack_angle"].dropna() if "attack_angle" in swings.columns else pd.Series(dtype=float)
        if len(bs) > 0: feats["bat_speed_r14"]     = bs.mean()
        if len(sl) > 0: feats["swing_length_r14"]  = sl.mean()
        if len(aa) > 0: feats["attack_angle_r14"]  = aa.mean()

    # Sweet spot % (on balls in play)
    bip = window[window["description"].apply(is_bip)]
    if len(bip) > 0 and "launch_angle" in bip.columns:
        la = bip["launch_angle"].dropna()
        if len(la) > 0:
            feats["sweet_spot_pct_r14"] = ((la >= SWEET_SPOT_LOW) & (la <= SWEET_SPOT_HIGH)).mean()

    # ── pitch mix faced ───────────────────────────────────────────────────
    n = len(window)
    if "pitch_type" in window.columns:
        pt = window["pitch_type"].fillna("XX")
        feats["fastball_pct_r14"]  = pt.isin(FASTBALL_TYPES).sum() / n
        feats["breaking_pct_r14"]  = pt.isin(BREAKING_TYPES).sum() / n
        feats["offspeed_pct_r14"]  = pt.isin(OFFSPEED_TYPES).sum() / n

    # ── zone discipline ───────────────────────────────────────────────────
    if "zone" in window.columns:
        zones = window["zone"].dropna()
        out_zone_mask = window["zone"].isin(OUT_ZONE)
        in_zone_mask  = window["zone"].isin(IN_ZONE)

        # Chase rate: swings on out-of-zone pitches / total out-of-zone pitches
        out_zone_pitches = window[out_zone_mask]
        if len(out_zone_pitches) > 0:
            chases = out_zone_pitches["description"].apply(is_swing).sum()
            feats["chase_rate_r14"] = chases / len(out_zone_pitches)

        # Zone contact: contact on in-zone pitches / swings on in-zone pitches
        in_zone_swings = window[in_zone_mask & window["description"].apply(is_swing)]
        if len(in_zone_swings) > 0:
            contacts = in_zone_swings["description"].apply(is_contact).sum()
            feats["zone_contact_r14"] = contacts / len(in_zone_swings)

    # Whiff rate: swinging strikes / total swings
    if len(swings) > 0:
        whiffs = swings["description"].isin({
            "swinging_strike", "swinging_strike_blocked"
        }).sum()
        feats["whiff_rate_r14"] = whiffs / len(swings)

    # First pitch strike rate (per PA)
    first_pitches = window[window["pitch_number"] == 1] if "pitch_number" in window.columns else pd.DataFrame()
    if len(first_pitches) > 0:
        fp_strikes = first_pitches["description"].isin({
            "called_strike", "swinging_strike", "foul", "foul_tip",
            "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
        }).sum()
        feats["first_pitch_strike_r14"] = fp_strikes / len(first_pitches)

    # ── quality of contact ────────────────────────────────────────────────
    if len(bip) > 0:
        xba  = bip["estimated_ba_using_speedangle"].dropna()   if "estimated_ba_using_speedangle"   in bip.columns else pd.Series(dtype=float)
        xwoba = bip["estimated_woba_using_speedangle"].dropna() if "estimated_woba_using_speedangle" in bip.columns else pd.Series(dtype=float)
        ev   = bip["launch_speed"].dropna()                     if "launch_speed"                    in bip.columns else pd.Series(dtype=float)
        la   = bip["launch_angle"].dropna()                     if "launch_angle"                    in bip.columns else pd.Series(dtype=float)

        if len(xba)   > 0: feats["xba_r14"]          = xba.mean()
        if len(xwoba) > 0: feats["xwoba_pitch_r14"]  = xwoba.mean()

        if len(ev) > 0:
            feats["hard_contact_r14"] = (ev >= HARD_EV_THRESH).mean()
            # Barrel: EV >= 98 AND LA in sweet spot
            ev_bip  = bip["launch_speed"].fillna(0)
            la_bip  = bip["launch_angle"].fillna(-999)
            barrels = ((ev_bip >= BARREL_EV_THRESH) &
                       (la_bip >= SWEET_SPOT_LOW) &
                       (la_bip <= SWEET_SPOT_HIGH))
            feats["barrel_rate_r14"] = barrels.mean()

    # ── situational ───────────────────────────────────────────────────────
    if "n_thruorder_pitcher" in window.columns:
        tto = window["n_thruorder_pitcher"].dropna()
        if len(tto) > 0:
            feats["times_thru_order_r14"] = tto.mean()

    if "delta_run_exp" in window.columns:
        # Sum delta_run_exp per PA (not per pitch) for count_leverage
        pas = window.groupby(["game_pk", "at_bat_number"])["delta_run_exp"].sum() \
              if "game_pk" in window.columns and "at_bat_number" in window.columns \
              else pd.Series(dtype=float)
        if len(pas) > 0:
            feats["count_leverage_r14"] = pas.mean()

    return feats


def build_pitch_features():
    print("[buildPitchFeatures] loading statcast data...")
    sc = load_statcast()
    if sc.empty:
        print("  no statcast data found — run fetchStatcastDaily.py --backfill first")
        return

    print("[buildPitchFeatures] loading batter features...")
    feats = pd.read_csv(FEAT_PATH, parse_dates=["game_date"])
    n_before = len(feats)

    # New columns — init to NaN
    new_cols = [
        "bat_speed_r14", "swing_length_r14", "attack_angle_r14", "sweet_spot_pct_r14",
        "fastball_pct_r14", "breaking_pct_r14", "offspeed_pct_r14",
        "chase_rate_r14", "zone_contact_r14", "whiff_rate_r14", "first_pitch_strike_r14",
        "xba_r14", "xwoba_pitch_r14", "hard_contact_r14", "barrel_rate_r14",
        "times_thru_order_r14", "count_leverage_r14",
    ]
    for col in new_cols:
        if col not in feats.columns:
            feats[col] = np.nan

    # Group statcast by batter ID for efficient lookup
    print("[buildPitchFeatures] indexing statcast by batter...")
    sc_by_batter = {batter_id: grp.sort_values("game_date")
                    for batter_id, grp in sc.groupby("batter")}
    print(f"  {len(sc_by_batter)} unique batters in statcast data")

    # Build name crosswalk: Statcast uses "Last, First" — convert to "First Last"
    # Also build reverse: "first last" -> mlbam_id
    name_to_mlbam = {}
    for mid, grp in sc_by_batter.items():
        names = grp["player_name"].dropna().unique()
        for n in names:
            n = str(n).strip()
            # Store original
            name_to_mlbam[n.lower()] = mid
            # Convert "Last, First" -> "First Last"
            if "," in n:
                parts = [p.strip() for p in n.split(",", 1)]
                if len(parts) == 2:
                    converted = f"{parts[1]} {parts[0]}"
                    name_to_mlbam[converted.lower()] = mid

    print(f"  built name->mlbam crosswalk: {len(name_to_mlbam)} entries")

    # Process each unique (player, game_date) combination
    # Only process dates that are within the statcast date range
    sc_min_date = sc["game_date"].min()
    sc_max_date = sc["game_date"].max()

    # Filter feature rows to statcast coverage window
    eligible = feats[
        (feats["game_date"] >= sc_min_date) &
        (feats["game_date"] <= sc_max_date + pd.Timedelta(days=1))
    ].index

    print(f"  eligible feature rows (within statcast window): {len(eligible):,}")
    print(f"  statcast window: {sc_min_date.date()} to {sc_max_date.date()}")

    patched = 0
    skipped = 0

    # Process in chunks by player to avoid recomputing name lookups
    player_groups = feats.loc[eligible].groupby("player_name")
    total_players = feats.loc[eligible]["player_name"].nunique()

    for i, (player_name, player_rows) in enumerate(player_groups):
        name_key = str(player_name).strip().lower()
        mlbam_id = name_to_mlbam.get(name_key)

        if mlbam_id is None or mlbam_id not in sc_by_batter:
            skipped += 1
            continue

        player_sc = sc_by_batter[mlbam_id]

        for idx, row in player_rows.iterrows():
            target_date = row["game_date"]
            pitch_feats = compute_pitch_features_for_player(player_sc, target_date, window_days=14)

            if pitch_feats:
                for col, val in pitch_feats.items():
                    feats.at[idx, col] = val
                patched += 1

        if (i + 1) % 50 == 0:
            print(f"  progress: {i+1}/{total_players} players, {patched:,} rows patched")

    feats.to_csv(FEAT_PATH, index=False)
    print(f"\n[buildPitchFeatures] done.")
    print(f"  rows patched with pitch features: {patched:,} / {len(eligible):,}")
    print(f"  players skipped (no statcast match): {skipped}")
    print(f"  feature columns added: {new_cols}")
    print(f"  wrote {len(feats):,} rows -> {FEAT_PATH}")


if __name__ == "__main__":
    build_pitch_features()
