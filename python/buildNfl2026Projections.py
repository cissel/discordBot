#!/usr/bin/env python3
"""
buildNfl2026Projections.py

Phase 3, step 1: generate 2026 season fantasy point projections for every
player with current ADP, using the Phase 2 trained models (Ridge/GBM/LightGBM
per position) and each player's real 2025 season usage as the lag1 features.

Handles the name-matching between Sleeper (ADP source) and nflverse (features
source) - different name conventions require suffix stripping (Jr./Sr./III)
and accent normalization.

Rookies (no 2025 usage in nflverse - literally didn't play in the NFL last
year) cannot get a model prediction under our leakage-safe design (no lag1
features exist). They are flagged separately rather than silently dropped or
given a fabricated projection - James can layer rookie ADP-implied value on
top manually, or that's a natural Phase 3.5 extension (draft-capital based
rookie prior) if he wants it.

Usage: venv/bin/python3 python/buildNfl2026Projections.py
Output: outputs/sports/nfl/fantasy/projections_2026.csv
"""
import glob
import os
import re
import unicodedata

import joblib
import numpy as np
import pandas as pd

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
MODEL_DIR = os.path.expanduser("~/discordBot/models/sports/nfl")

TEAM_MAP = {"LAR": "LA"}  # Sleeper -> nflverse team code differences
SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?\s*$", re.IGNORECASE)


def norm_name(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_latest_model(position, model_type=None):
    """Load the best (or specified) model for a position from the pkl bundles.

    KNOWN LATENT RISK (flagged 2026-08, discovered during the injury-bias
    fix): this globs ALL pkl files matching nfl_{position}_* across every
    training date and picks by val_spearman alone - it does NOT check that
    the feature schema matches what buildNfl2026Projections.py currently
    builds. If a stale model from before a feature-set change happens to
    score marginally better on val_spearman, this will silently select it
    and predict() will crash (or worse, silently mis-predict if the stale
    schema happens to be a subset) the next time features change. Mitigated
    for now by deleting stale pre-fix pkls (see nfl_experiment_log.csv
    2026-07-30 entries), but if this recurs, add a feature_cols equality
    check against the CURRENT FEATURE_COLS list before considering a
    candidate, not just take the max-spearman file blindly.
    """
    pattern = os.path.join(MODEL_DIR, f"nfl_{position.lower()}_*.pkl")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    bundles = [joblib.load(f) for f in files]
    if model_type:
        bundles = [b for b in bundles if b["model_type"] == model_type]
    # pick best by val_spearman
    best = max(bundles, key=lambda b: b["metrics"]["val_spearman"])
    return best


def main():
    adp = pd.read_csv(os.path.join(OUT_DIR, "adp_2026.csv"))
    adp["team_nflverse"] = adp["team"].map(lambda t: TEAM_MAP.get(t, t))
    adp["norm_name"] = adp["full_name"].map(norm_name)

    career = pd.read_csv(os.path.join(OUT_DIR, "career_panel.csv"))
    phase1_raw = pd.read_csv(os.path.join(OUT_DIR, "nfl_phase1_features.csv"))
    aging = pd.read_csv(os.path.join(OUT_DIR, "aging_curves.csv"))
    coach_sig = pd.read_csv(os.path.join(OUT_DIR, "coach_scheme_signature.csv"))
    staff = pd.read_csv(os.path.join(OUT_DIR, "coaching_staff.csv"))

    # Collapse mid-season-trade duplicates same as training panel build
    phase1 = (
        phase1_raw.assign(_snaps=phase1_raw["games_with_snaps"].fillna(0))
        .sort_values("_snaps", ascending=False)
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .drop(columns=["_snaps"])
    )

    # 2025 usage = lag1 features for 2026 predictions
    usage_2025 = phase1[phase1["season"] == 2025][
        ["player_id", "player_display_name", "position", "team",
         "targets", "receptions", "rec_yards", "air_yards", "rush_attempts",
         "target_share", "air_yards_share", "games_with_snaps", "snap_share_avg"]
    ].copy()
    usage_2025["norm_name"] = usage_2025["player_display_name"].map(norm_name)

    output_2025 = career[career["season"] == 2025][
        ["player_id", "player_display_name", "position", "room40_pts", "pts_per_game", "games", "age"]
    ].copy()
    output_2025["norm_name"] = output_2025["player_display_name"].map(norm_name)
    output_2025["age_2026"] = output_2025["age"] + 1  # age as of Sept 1 2026
    static_info = career[career["season"] == 2025][
        ["player_id", "draft_number", "years_exp"]
    ].drop_duplicates(subset=["player_id"])

    # Merge ADP with 2025 usage (by normalized name - player_id namespaces differ
    # between Sleeper and nflverse, name matching is the reliable bridge here)
    merged = adp.merge(
        usage_2025.add_prefix("lag1_").rename(columns={"lag1_norm_name": "norm_name"}),
        on="norm_name", how="left", suffixes=("", "_usage")
    )
    merged = merged.merge(
        output_2025[["norm_name", "room40_pts", "pts_per_game", "games", "player_id", "age_2026"]]
        .rename(columns={"room40_pts": "lag1_room40_pts", "pts_per_game": "lag1_pts_per_game",
                          "games": "lag1_games", "player_id": "nflverse_player_id"}),
        on="norm_name", how="left"
    )
    merged = merged.merge(static_info, left_on="nflverse_player_id", right_on="player_id",
                           how="left", suffixes=("", "_static"))

    # 2026 coaching situation - join on team (using nflverse team code)
    coach_2026 = coach_sig[coach_sig["season"] == 2025][
        ["team", "coach_proe_signature", "coach_pace_signature", "coach_rz_signature",
         "coach_n_prior_seasons"]
    ].copy()
    # NOTE: coach_scheme_signature.csv only has data through 2025 season (built from
    # 2018-2025 PBP). For 2026 team scheme, use each team's 2025 signature as the
    # best available prior (2026 PBP doesn't exist yet - season hasn't started).
    staff_2026_note = staff[staff["season"] == 2025][["team", "new_oc", "confidence"]]
    coach_2026 = coach_2026.merge(staff_2026_note, on="team", how="left")

    merged = merged.merge(coach_2026, left_on="team_nflverse", right_on="team",
                           how="left", suffixes=("", "_coach"))

    # aging curve for age_2026
    merged["age_round"] = merged["age_2026"].round()
    aging_lookup = aging.rename(columns={"age": "age_round"})
    merged = merged.merge(aging_lookup[["position", "age_round", "combined_index"]],
                           left_on=["position", "age_round"], right_on=["position", "age_round"],
                           how="left")

    # fill static defaults
    merged["draft_number"] = merged["draft_number"].fillna(300)
    merged["new_oc"] = merged["new_oc"].fillna(0).astype(int)
    merged["scheme_uncertainty_flag"] = 0  # not tracked forward into 2026, default 0

    # team_changed: compare each player's 2025 actual team (lag1_team, from
    # usage_2025 above) against their 2026 ADP-listed team (team_nflverse).
    # Mirrors the training panel's team_changed feature (buildNflTrainingPanel.R)
    # exactly - LA/LAR is the same franchise under different team codes across
    # sources, not a real move, so it's excluded via TEAM_MAP normalization
    # already applied to both sides via team_nflverse.
    merged["team_changed"] = (
        merged["lag1_team"].notna()
        & (merged["lag1_team"] != merged["team_nflverse"])
    ).astype(int)
    merged["team_changed_x_target_share"] = merged["team_changed"] * merged["lag1_target_share"]

    FEATURE_COLS = [
        "age_2026", "draft_number", "years_exp",
        "lag1_targets", "lag1_receptions", "lag1_rec_yards", "lag1_air_yards",
        "lag1_rush_attempts", "lag1_target_share", "lag1_air_yards_share",
        "lag1_games_with_snaps", "lag1_snap_share_avg",
        "lag1_pts_per_game", "lag1_games",
        "combined_index",
        "new_oc", "scheme_uncertainty_flag",
        "team_changed", "team_changed_x_target_share",
        "coach_proe_signature", "coach_pace_signature", "coach_rz_signature",
        "coach_n_prior_seasons",
    ]
    RENAME_FOR_MODEL = {"age_2026": "age"}  # model was trained with column name "age"

    has_usage = merged["lag1_room40_pts"].notna()
    print(f"Players with 2025 usage (predictable): {has_usage.sum()} / {len(merged)}")
    print(f"Rookies/no-2025-data (not predictable under this design): {(~has_usage).sum()}")

    predictions = pd.Series(np.nan, index=merged.index)
    model_used = pd.Series("", index=merged.index)

    for position in ["QB", "RB", "WR", "TE"]:
        bundle = load_latest_model(position)
        if bundle is None:
            print(f"No model found for {position}, skipping")
            continue
        pos_mask = (merged["position"] == position) & has_usage
        if pos_mask.sum() == 0:
            continue
        X = merged.loc[pos_mask, FEATURE_COLS].rename(columns=RENAME_FOR_MODEL)
        # median-impute sparse coach features (matches training-time convention)
        for c in ["coach_proe_signature", "coach_pace_signature", "coach_rz_signature", "coach_n_prior_seasons"]:
            X[c] = X[c].fillna(X[c].median() if X[c].notna().any() else 0)
        # median-impute snap-share features too (defense in depth): these
        # come from a separate source table than targets (see
        # mergeNflPhase1Features.R name-normalization fix, 2026-07-29) and a
        # player can legitimately have real target-share data with no snap
        # data attached (e.g. a genuine upstream source gap, not just the
        # name-mismatch bug that fix addressed). Treating these as
        # hard-required in the old core_ok dropna() silently dropped real
        # rostered players from the entire VORP board with no warning -
        # median-impute instead, consistent with how coach features are
        # already handled above.
        for c in ["lag1_games_with_snaps", "lag1_snap_share_avg"]:
            X[c] = X[c].fillna(X[c].median() if X[c].notna().any() else 0)
        # dropna for core - if still missing after merge, skip that row
        core_ok = X.drop(columns=["coach_proe_signature", "coach_pace_signature",
                                    "coach_rz_signature", "coach_n_prior_seasons"]).notna().all(axis=1)
        idx = X[core_ok].index
        if len(idx) == 0:
            continue
        preds = bundle["model"].predict(X.loc[idx])
        predictions.loc[idx] = preds
        model_used.loc[idx] = f"{position}_{bundle['model_type']}"
        print(f"{position}: {len(idx)} players scored using {bundle['model_type']} "
              f"(val_spearman={bundle['metrics']['val_spearman']:.3f})")

    merged["projected_room40_pts"] = predictions
    merged["model_used"] = model_used

    out_cols = [
        "full_name", "position", "team", "adp_overall", "adp_position",
        "sleeper_proj_pts_week1", "lag1_room40_pts", "lag1_pts_per_game", "lag1_games",
        "lag1_target_share", "lag1_snap_share_avg", "projected_room40_pts", "model_used",
        "new_oc", "coach_proe_signature", "team_changed",
    ]
    out = merged[out_cols].sort_values("adp_overall")
    out_path = os.path.join(OUT_DIR, "projections_2026.csv")
    out.to_csv(out_path, index=False)

    print(f"\nWrote: {out_path}")
    print(f"Total rows: {len(out)}")
    print(f"Rows with a projection: {out['projected_room40_pts'].notna().sum()}")
    print("\n=== Top 10 by projected points ===")
    print(out.sort_values("projected_room40_pts", ascending=False).head(10)
          [["full_name", "position", "team", "adp_overall", "projected_room40_pts"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
