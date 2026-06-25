#!/usr/bin/env python3
"""
buildSportsFeatures.py
======================
Reads raw game logs and engineers a feature matrix for fantasy point prediction.
Outputs:
  outputs/features/sports/batter_features.csv
  outputs/features/sports/pitcher_features.csv

Each row represents one player on one date, with:
  - Rolling window stats (7 / 14 / 30 day)
  - Target variables: next_game_pts (daily model) and next7_pts (weekly model)
  - Contextual features: batting order trend, rest days, opponent difficulty

Run nightly via cron after game log data is refreshed.
"""

import os
import warnings
import pandas as pd
import numpy as np
import datetime

warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")

DATA_DIR    = os.path.expanduser("~/discordBot/outputs/sports/mlb/fantasy/playerData")
HIST_DIR    = os.path.join(DATA_DIR, "historical")
OUT_DIR     = os.path.expanduser("~/discordBot/outputs/features/sports")
os.makedirs(OUT_DIR, exist_ok=True)

CURRENT_SEASON = datetime.date.today().year

# ── rolling helper ────────────────────────────────────────────────────────────
def rolling_player(df, col, window, min_periods=1, suffix=None):
    """Compute rolling mean per player, shifted by 1 to avoid leakage."""
    suf = suffix or f"_r{window}"
    df = df.sort_values(["playerid", "game_date"])
    df[f"{col}{suf}"] = (
        df.groupby("playerid")[col]
          .transform(lambda x: x.shift(1).rolling(window, min_periods=min_periods).mean())
    )
    return df

def rolling_std_player(df, col, window, min_periods=2):
    """Rolling std per player, shifted."""
    df = df.sort_values(["playerid", "game_date"])
    df[f"{col}_std_r{window}"] = (
        df.groupby("playerid")[col]
          .transform(lambda x: x.shift(1).rolling(window, min_periods=min_periods).std())
    )
    return df

# ── opponent difficulty ───────────────────────────────────────────────────────
def build_opponent_difficulty(df, pts_col="fantasy_pts"):
    """
    For each opponent, compute the mean pts allowed to this player type
    over the prior 14 days (rolling, shifted). Proxy for matchup difficulty.
    """
    df = df.sort_values("game_date")
    opp_avg = (
        df.groupby(["opponent", "game_date"])[pts_col]
          .mean()
          .reset_index()
          .rename(columns={pts_col: "opp_pts_allowed"})
    )
    opp_avg = opp_avg.sort_values("game_date")
    opp_avg["opp_diff_r14"] = (
        opp_avg.groupby("opponent")["opp_pts_allowed"]
               .transform(lambda x: x.shift(1).rolling(14, min_periods=3).mean())
    )
    return df.merge(opp_avg[["opponent", "game_date", "opp_diff_r14"]],
                    on=["opponent", "game_date"], how="left")

# ── target builder ────────────────────────────────────────────────────────────
def build_targets(df, pts_col="fantasy_pts"):
    """
    next_game_pts : the very next game's pts (daily model target)
    next7_pts     : sum of pts over the next 7 calendar days (weekly model target)
    """
    df = df.sort_values(["playerid", "game_date"])
    df["game_date"] = pd.to_datetime(df["game_date"])

    # next_game_pts: shift(-1) within player
    df["next_game_pts"] = df.groupby("playerid")[pts_col].shift(-1)

    # next7_pts: for each row, sum pts in (game_date, game_date + 7 days]
    records = []
    for pid, grp in df.groupby("playerid"):
        grp = grp.sort_values("game_date").reset_index(drop=True)
        dates = grp["game_date"].values
        pts   = grp[pts_col].values
        totals = []
        for i, d in enumerate(dates):
            mask = (dates > d) & (dates <= d + np.timedelta64(7, "D"))
            totals.append(pts[mask].sum() if mask.any() else np.nan)
        grp["next7_pts"] = totals
        records.append(grp)
    df = pd.concat(records, ignore_index=True)
    return df

# ── multi-year loader ─────────────────────────────────────────────────────────
def load_game_logs(log_filename):
    """
    Load current season logs + any available historical logs, concatenate.
    Adds a 'season' column derived from game_date if not already present.
    """
    current_path = os.path.join(DATA_DIR, log_filename)
    frames = []

    # Current season
    if os.path.exists(current_path):
        df = pd.read_csv(current_path, parse_dates=["game_date"])
        if "season" not in df.columns:
            df["season"] = df["game_date"].dt.year
        frames.append(df)
        print(f"  Current season: {len(df)} rows")
    else:
        print(f"  [warn] {current_path} not found")

    # Historical seasons
    prefix = log_filename.replace(".csv", "")
    for fname in sorted(os.listdir(HIST_DIR)) if os.path.exists(HIST_DIR) else []:
        if fname.startswith(prefix + "_") and fname.endswith(".csv"):
            path = os.path.join(HIST_DIR, fname)
            df = pd.read_csv(path, parse_dates=["game_date"])
            if "season" not in df.columns:
                df["season"] = df["game_date"].dt.year
            frames.append(df)
            print(f"  Historical ({fname}): {len(df)} rows")

    if not frames:
        raise FileNotFoundError(f"No game log files found for {log_filename}")

    combined = pd.concat(frames, ignore_index=True)
    print(f"  Combined: {len(combined)} rows across {combined['season'].nunique()} season(s): "
          f"{sorted(combined['season'].unique())}")
    return combined

# ── park factor lookup ────────────────────────────────────────────────────────
def load_park_factors():
    """Load park factors CSV, return as dict: (team, season) -> pf_basic."""
    pf_path = os.path.join(DATA_DIR, "park_factors.csv")
    if not os.path.exists(pf_path):
        print("  [warn] park_factors.csv not found - run fetchParkFactors.py")
        return {}
    pf = pd.read_csv(pf_path)
    # Build lookup: (team_abbrev, season) -> pf_basic (5yr smoothed)
    lookup = {}
    for _, row in pf.iterrows():
        if pd.notna(row.get("team")) and pd.notna(row.get("pf_basic")):
            lookup[(str(row["team"]), int(row["season"]))] = float(row["pf_basic"])
    print(f"  Loaded park factors: {len(lookup)} (team, season) entries")
    return lookup

# ── pitcher quality lookup ────────────────────────────────────────────────
def load_pitcher_quality():
    """Load pitcher quality CSV; return per-field dicts keyed by mlbam_id (int)."""
    pq_path = os.path.join(DATA_DIR, "pitcher_quality.csv")
    if not os.path.exists(pq_path):
        print("  [warn] pitcher_quality.csv not found - run fetchPitcherQuality.py")
        return {}
    pq = pd.read_csv(pq_path)
    lookup = {}
    for _, row in pq.iterrows():
        mid = row.get("mlbam_id")
        if pd.notna(mid):
            lookup[int(mid)] = {
                "quality_score": float(row["quality_score"]) if pd.notna(row.get("quality_score")) else np.nan,
                "era":           float(row["era"])           if pd.notna(row.get("era"))           else np.nan,
                "whip":          float(row["whip"])          if pd.notna(row.get("whip"))          else np.nan,
                "k_per9":        float(row["k_per9"])        if pd.notna(row.get("k_per9"))        else np.nan,
                "xera":          float(row["xera"])          if pd.notna(row.get("xera"))          else np.nan,
            }
    print(f"  Loaded pitcher quality: {len(lookup)} pitchers by mlbam_id")
    return lookup


# ── handedness lookup ─────────────────────────────────────────────────────
def load_handedness():
    """Load player_handedness.csv -> dict: mlbam_id -> {bat_side, pitch_hand}."""
    path = os.path.join(DATA_DIR, "player_handedness.csv")
    if not os.path.exists(path):
        print("  [warn] player_handedness.csv not found - run fetchPlayerHandedness.py")
        return {}
    df = pd.read_csv(path)
    lookup = {}
    for _, row in df.iterrows():
        mid = row.get("mlbam_id")
        if pd.notna(mid):
            lookup[int(mid)] = {
                "bat_side":   str(row.get("bat_side",  "")).strip(),
                "pitch_hand": str(row.get("pitch_hand","")).strip(),
            }
    print(f"  Loaded handedness: {len(lookup)} players")
    return lookup


# ── day-of lineup lookup ──────────────────────────────────────────────────
def load_day_of_lineups():
    """
    Load day_of_lineups.csv -> dict: (game_date_str, mlbam_id) -> batting_order int.
    Also returns SP lookup: (game_date_str, team_name) -> probable_sp_id.
    """
    path = os.path.join(DATA_DIR, "day_of_lineups.csv")
    if not os.path.exists(path):
        return {}, {}
    df = pd.read_csv(path)
    if df.empty or "batting_order" not in df.columns:
        return {}, {}
    order_lookup = {}
    sp_lookup    = {}
    for _, row in df.iterrows():
        gdate = str(row.get("game_date", ""))[:10]
        mid   = row.get("mlbam_id")
        if pd.notna(mid):
            order_lookup[(gdate, int(mid))] = int(row["batting_order"])
        # SP lookup: opponent team -> which SP they face
        sp_id = row.get("probable_sp_id")
        opp   = str(row.get("opponent", ""))
        if pd.notna(sp_id) and opp:
            sp_lookup[(gdate, opp)] = int(sp_id)
    print(f"  Loaded day-of lineups: {len(order_lookup)} batter slots, {len(sp_lookup)} SP assignments")
    return order_lookup, sp_lookup

# ══════════════════════════════════════════════════════════════════════════════
# BATTER FEATURES
# ══════════════════════════════════════════════════════════════════════════════
def build_batter_features():
    print("[buildSportsFeatures] loading batter game logs...")
    df = load_game_logs("batter_game_logs.csv")
    df = df.sort_values(["playerid", "game_date"]).reset_index(drop=True)

    print(f"  {len(df)} rows, {df['player_name'].nunique()} players, "
          f"{df['season'].nunique()} season(s)")

    # ── park factors ─────────────────────────────────────────────────────────
    print("[buildSportsFeatures] joining park factors...")
    park_lookup = load_park_factors()
    if park_lookup:
        df["park_factor"] = df.apply(
            lambda r: park_lookup.get((str(r["team"]), int(r["season"])),
                      park_lookup.get((str(r["team"]), CURRENT_SEASON), 100.0)),
            axis=1
        )
        # Normalize: 100 = neutral, express as deviation from neutral
        df["park_factor_dev"] = df["park_factor"] - 100.0
    else:
        df["park_factor"]     = 100.0
        df["park_factor_dev"] = 0.0

    # ── SP matchup features ───────────────────────────────────────────────────
    # Uses day_of_lineups.csv (written by noon ET cron) to identify the opposing SP
    # for each game-date row, then joins pitcher_quality for that SP.
    # For historical rows (no day-of lineup), falls back to NaN (imputed at train time).
    print("[buildSportsFeatures] loading pitcher quality + handedness + day-of lineups...")
    pq_lookup       = load_pitcher_quality()
    hand_lookup     = load_handedness()
    order_lookup, sp_lookup = load_day_of_lineups()

    def _opp_clean(opp):
        return str(opp).lstrip("@").strip() if pd.notna(opp) else ""

    # For each row: look up the probable SP via (game_date, opponent_team_name)
    # Opponent in batter logs is an abbreviation (e.g. "NYY") — day_of_lineups stores full names.
    # We'll try abbreviation first, then fall back gracefully.
    def _get_sp_features(row):
        gdate = str(row["game_date"])[:10]
        opp   = _opp_clean(row.get("opponent", ""))
        sp_id = sp_lookup.get((gdate, opp))
        if sp_id and sp_id in pq_lookup:
            pq = pq_lookup[sp_id]
            return pd.Series({
                "sp_era":    pq["era"],
                "sp_xera":   pq["xera"],
                "sp_k9":     pq["k_per9"],
                "sp_whip":   pq["whip"],
                "sp_quality": pq["quality_score"],
                "sp_mlbam_id": sp_id,
            })
        return pd.Series({
            "sp_era": np.nan, "sp_xera": np.nan, "sp_k9": np.nan,
            "sp_whip": np.nan, "sp_quality": np.nan, "sp_mlbam_id": np.nan,
        })

    sp_features = df.apply(_get_sp_features, axis=1)
    df = pd.concat([df, sp_features], axis=1)
    sp_hit_rate = df["sp_era"].notna().mean()
    print(f"  SP matchup feature hit rate: {sp_hit_rate*100:.1f}% of rows")

    # ── platoon split: batter hand vs SP hand ─────────────────────────────────
    # Encode: 1 = platoon advantage (L batter vs R pitcher or vice versa), 0 = same side, NaN = unknown
    def _platoon(row):
        bat_id = row.get("playerid")
        sp_id  = row.get("sp_mlbam_id")
        if pd.isna(sp_id):
            return np.nan
        batter_info = hand_lookup.get(int(bat_id), {}) if pd.notna(bat_id) else {}
        sp_info     = pq_lookup.get(int(sp_id), {})
        bat_side    = batter_info.get("bat_side", "")
        # pitcher hand from handedness file
        sp_hand_info = hand_lookup.get(int(sp_id), {})
        sp_hand      = sp_hand_info.get("pitch_hand", "")
        if bat_side in ("L","R") and sp_hand in ("L","R"):
            return 1 if bat_side != sp_hand else 0  # 1 = platoon advantage
        return np.nan

    # Note: playerid in batter logs is Fangraphs ID, not mlbam — platoon lookup
    # requires mlbam_id cross-reference. We'll use the SP's pitch_hand only
    # and encode batter's hand via the day-of lineup mlbam_id where available.
    # For historical rows without day-of lineups, platoon = NaN (model imputes median=0.5).
    # When day-of lineup is available, batter mlbam_id is in order_lookup keys.
    def _get_batter_mlbam(row):
        """Try to find batter's mlbam_id from day-of lineup via name match."""
        gdate = str(row["game_date"])[:10]
        # We stored (gdate, mlbam_id) in order_lookup — reverse map isn't cheap,
        # so for historical data we skip. For today's predictions use predictFantasy.py.
        return np.nan

    df["platoon_adv"] = np.nan  # populated in predictFantasy.py with real-time lineup data

    # ── rolling fantasy pts ───────────────────────────────────────────────────
    for w in [7, 14, 30]:
        df = rolling_player(df, "fantasy_pts", w)
    df = rolling_std_player(df, "fantasy_pts", 7)
    df = rolling_std_player(df, "fantasy_pts", 14)

    # Sharpe-like: mean / std over last 14
    df["sharpe_r14"] = df["fantasy_pts_r14"] / df["fantasy_pts_std_r14"].replace(0, np.nan)

    # ── rolling Statcast features ─────────────────────────────────────────────
    statcast_cols = ["wOBA", "xwOBA", "ISO", "BABIP", "Hard_pct",
                     "Barrel_pct", "EV", "SwStr_pct", "BB_pct", "K_pct"]
    for col in statcast_cols:
        if col in df.columns:
            for w in [7, 14]:
                df = rolling_player(df, col, w)

    # ── batting order trend ───────────────────────────────────────────────────
    if "BatOrder" in df.columns:
        df["BatOrder"] = pd.to_numeric(df["BatOrder"], errors="coerce")
        df = rolling_player(df, "BatOrder", 7)
        df["bat_order_trend"] = df["BatOrder_r7"] - df.groupby("playerid")["BatOrder"].transform(
            lambda x: x.shift(1).rolling(30, min_periods=5).mean()
        )

    # ── rest days ─────────────────────────────────────────────────────────────
    df["prev_game_date"] = df.groupby("playerid")["game_date"].shift(1)
    df["rest_days"] = (df["game_date"] - df["prev_game_date"]).dt.days.clip(upper=7)
    df.drop(columns=["prev_game_date"], inplace=True)

    # ── home/away flag ────────────────────────────────────────────────────────
    # opponent column has '@' prefix for away games (e.g. '@NYY' = playing at NYY)
    df["is_home"] = (~df["opponent"].astype(str).str.startswith("@")).astype(int)

    # ── month (schedule density / fatigue proxy) ──────────────────────────────
    df["month"] = df["game_date"].dt.month  # 3=Mar, 4=Apr ... 9=Sep
    # Mid-season dummy: Jul-Aug tend to have highest fatigue
    df["is_midsummer"] = df["month"].isin([7, 8]).astype(int)

    # ── streak features ───────────────────────────────────────────────────────
    df = df.sort_values(["playerid", "game_date"])

    # Games since last HR (per player, within-season) - capped at 20
    def games_since_event(series, event_col):
        """Count games since last non-zero event, within player."""
        out = []
        count = 20  # start high (assume no recent event)
        for val in series:
            if val > 0:
                count = 0
            else:
                count = min(count + 1, 20)
            out.append(count)
        return out

    hr_since = []
    sb_since = []
    for pid, grp in df.groupby(["playerid", "season"]):
        hr_since.extend(games_since_event(grp["HR"].shift(1).fillna(0), "HR"))
        sb_since.extend(games_since_event(grp["SB"].shift(1).fillna(0), "SB"))
    df["games_since_hr"] = hr_since
    df["games_since_sb"] = sb_since

    # Rolling multi-hit game rate (proxy for contact consistency)
    df["multi_hit"] = (df["H"] >= 2).astype(float)
    df = rolling_player(df, "multi_hit", 14)

    # ── opponent difficulty ───────────────────────────────────────────────────
    df = build_opponent_difficulty(df)


    # ── season-to-date pts per game ───────────────────────────────────────────
    # Compute within-season (reset each year)
    df = df.sort_values(["playerid", "season", "game_date"])
    df["cum_pts"]    = df.groupby(["playerid", "season"])["fantasy_pts"].transform(
        lambda x: x.shift(1).expanding().sum())
    df["cum_games"]  = df.groupby(["playerid", "season"])["fantasy_pts"].transform(
        lambda x: x.shift(1).expanding().count())
    df["season_ppg"] = df["cum_pts"] / df["cum_games"].replace(0, np.nan)
    df.drop(columns=["cum_pts", "cum_games"], inplace=True)

    # ── hot/cold flag ─────────────────────────────────────────────────────────
    df["hot_flag"]  = (df["fantasy_pts_r7"] > df["season_ppg"] * 1.25).astype(int)
    df["cold_flag"] = (df["fantasy_pts_r7"] < df["season_ppg"] * 0.75).astype(int)

    # ── targets ───────────────────────────────────────────────────────────────
    # Only build targets within each season (no cross-season leakage)
    season_frames = []
    for s, grp in df.groupby("season"):
        season_frames.append(build_targets(grp.copy()))
    df = pd.concat(season_frames, ignore_index=True)

    # ── position dummies ──────────────────────────────────────────────────────
    pos_dummies = pd.get_dummies(df["fantasy_position"], prefix="pos")
    df = pd.concat([df, pos_dummies], axis=1)

    out_path = os.path.join(OUT_DIR, "batter_features.csv")
    df.to_csv(out_path, index=False)
    total_rows = len(df)
    seasons    = sorted(df["season"].unique())
    print(f"  wrote {total_rows} rows ({seasons}) -> {out_path}")
    return df

# ══════════════════════════════════════════════════════════════════════════════
# PITCHER FEATURES
# ══════════════════════════════════════════════════════════════════════════════
def build_pitcher_features():
    print("[buildSportsFeatures] loading pitcher game logs...")
    df = load_game_logs("pitcher_game_logs.csv")
    df = df.sort_values(["playerid", "game_date"]).reset_index(drop=True)

    print(f"  {len(df)} rows, {df['player_name'].nunique()} players, "
          f"{df['season'].nunique()} season(s)")

    # ── park factors (home/away matters for pitchers too) ─────────────────────
    park_lookup = load_park_factors()
    if park_lookup and "home_away" in df.columns:
        # Pitchers: use the opponent park when away, own park when home
        df["park_factor"] = df.apply(
            lambda r: park_lookup.get((str(r["opponent"]).lstrip("@"), int(r["season"])),
                      park_lookup.get((str(r["team"]), int(r["season"])), 100.0))
            if str(r.get("home_away", "")).upper() == "A"
            else park_lookup.get((str(r["team"]), int(r["season"])), 100.0),
            axis=1
        )
        df["park_factor_dev"] = df["park_factor"] - 100.0
    else:
        df["park_factor"]     = 100.0
        df["park_factor_dev"] = 0.0

    # ── rolling fantasy pts ───────────────────────────────────────────────────
    for w in [3, 5, 10]:   # smaller windows — pitchers start every 5 days
        df = rolling_player(df, "fantasy_pts", w)
    df = rolling_std_player(df, "fantasy_pts", 5)
    df = rolling_std_player(df, "fantasy_pts", 10)

    df["sharpe_r5"] = df["fantasy_pts_r5"] / df["fantasy_pts_std_r5"].replace(0, np.nan)

    # ── rolling box score features ────────────────────────────────────────────
    for col in ["IP", "K", "ER", "BB", "H"]:
        if col in df.columns:
            for w in [3, 5]:
                df = rolling_player(df, col, w)

    # ── ERA / WHIP rolling ────────────────────────────────────────────────────
    for col in ["ERA", "WHIP"]:
        if col in df.columns:
            df = rolling_player(df, col, 5)

    # ── rest days (starts ~every 5) ───────────────────────────────────────────
    df["prev_game_date"] = df.groupby("playerid")["game_date"].shift(1)
    df["rest_days"] = (df["game_date"] - df["prev_game_date"]).dt.days.clip(upper=10)
    df.drop(columns=["prev_game_date"], inplace=True)

    # ── opponent difficulty ───────────────────────────────────────────────────
    df = build_opponent_difficulty(df)

    # ── opponent wOBA (rolling quality of batters the pitcher faces) ──────────
    # Load batter game logs to get team-level wOBA by date
    try:
        batter_df = load_game_logs("batter_game_logs.csv")
        batter_df = batter_df.sort_values("game_date")
        # team-level mean wOBA per game date (as offensive team)
        team_woba = (
            batter_df.groupby(["team", "game_date"])["wOBA"]
            .mean()
            .reset_index()
            .rename(columns={"wOBA": "team_woba"})
        )
        team_woba = team_woba.sort_values("game_date")
        # rolling 14-day mean wOBA per team (shifted to avoid leakage)
        team_woba["opp_woba_r14"] = (
            team_woba.groupby("team")["team_woba"]
            .transform(lambda x: x.shift(1).rolling(14, min_periods=3).mean())
        )
        # pitcher's opponent = the batting team they faced
        # opponent column in pitcher_game_logs has the team abbreviation (with @ prefix stripped)
        df["opp_team"] = df["opponent"].str.lstrip("@")
        df = df.merge(
            team_woba[["team", "game_date", "opp_woba_r14"]].rename(columns={"team": "opp_team"}),
            on=["opp_team", "game_date"], how="left"
        )
        df.drop(columns=["opp_team"], inplace=True)
        hit_rate = df["opp_woba_r14"].notna().mean()
        print(f"  opp_woba_r14: {hit_rate:.1%} rows populated")
    except Exception as e:
        print(f"  opp_woba_r14: skipped ({e})")
        df["opp_woba_r14"] = np.nan
    df = df.sort_values(["playerid", "season", "game_date"])
    df["cum_pts"]    = df.groupby(["playerid", "season"])["fantasy_pts"].transform(
        lambda x: x.shift(1).expanding().sum())
    df["cum_games"]  = df.groupby(["playerid", "season"])["fantasy_pts"].transform(
        lambda x: x.shift(1).expanding().count())
    df["season_ppg"] = df["cum_pts"] / df["cum_games"].replace(0, np.nan)
    df.drop(columns=["cum_pts", "cum_games"], inplace=True)

    # ── targets (within-season only) ─────────────────────────────────────────
    season_frames = []
    for s, grp in df.groupby("season"):
        season_frames.append(build_targets(grp.copy()))
    df = pd.concat(season_frames, ignore_index=True)

    # ── position dummies ──────────────────────────────────────────────────────
    pos_dummies = pd.get_dummies(df["fantasy_position"], prefix="pos")
    df = pd.concat([df, pos_dummies], axis=1)

    out_path = os.path.join(OUT_DIR, "pitcher_features.csv")
    df.to_csv(out_path, index=False)
    print(f"  wrote {len(df)} rows ({sorted(df['season'].unique())}) -> {out_path}")
    return df

if __name__ == "__main__":
    build_batter_features()
    build_pitcher_features()
    print("[buildSportsFeatures] done.")
