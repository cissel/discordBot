#!/usr/bin/env python3
"""
buildNflAgingCurve.py

Builds position-specific career/aging curves for fantasy football using the
delta method (standard sabermetric technique, avoids survivorship bias from
naive cross-sectional age averages).

Two curves per position:
  1. RATE curve  -- per-game production skill trajectory
  2. VOLUME curve -- games-played / availability trajectory
Combined -> full season-value career curve, indexed to peak age = 100.

Why the delta method:
A naive average of fantasy_pts by age is biased -- only good players are still
in the league at age 33+, so the cross-sectional average makes it look like
players get BETTER with age (survivorship). The delta method instead looks at
each player's OWN year-over-year change between consecutive seasons they
actually played, weighted by sample confidence (games played). Chaining those
deltas together reconstructs the true population aging curve without the bias.

Input:  outputs/sports/nfl/fantasy/career_panel.csv (from r/fetchNflCareerData.R)
Output: outputs/sports/nfl/fantasy/aging_curves.csv
        outputs/sports/nfl/fantasy/aging_curve_summary.csv
"""
import os
import numpy as np
import pandas as pd
from scipy.interpolate import UnivariateSpline

OUT_DIR = os.path.expanduser("~/discordBot/outputs/sports/nfl/fantasy")
PANEL_PATH = os.path.join(OUT_DIR, "career_panel.csv")
POSITIONS = ["QB", "RB", "WR", "TE"]

# Peak-age anchor per position for indexing the curve to 100 -- informed by
# known NFL aging patterns (RB/WR peak earliest, QB latest) and refined after
# inspecting the raw delta curve (see printed peak_age in output).
ANCHOR_AGE_GUESS = {"QB": 28, "RB": 25, "WR": 27, "TE": 27}


def harmonic_weight(g1, g2):
    """Harmonic-mean-like weight from two season sample sizes.
    Punishes small samples in EITHER season (avoids a 1-game garbage season
    dominating the weighted delta)."""
    g1 = max(g1, 1)
    g2 = max(g2, 1)
    return 2 * g1 * g2 / (g1 + g2)


def build_delta_table(df, value_col):
    """For each player, pair up consecutive seasons (season diff == 1) and
    compute the delta in value_col, weighted by harmonic mean of games in
    both seasons."""
    df = df.sort_values(["player_id", "season"]).reset_index(drop=True)
    rows = []
    for pid, grp in df.groupby("player_id"):
        grp = grp.sort_values("season")
        seasons = grp["season"].values
        ages = grp["age"].values
        vals = grp[value_col].values
        games = grp["games"].values
        for i in range(len(grp) - 1):
            if seasons[i + 1] - seasons[i] != 1:
                continue  # skip gaps (injury year, out of league, etc.)
            if pd.isna(vals[i]) or pd.isna(vals[i + 1]):
                continue
            delta = vals[i + 1] - vals[i]
            w = harmonic_weight(games[i], games[i + 1])
            age_from = round(ages[i])
            rows.append({"age_from": age_from, "age_to": age_from + 1,
                         "delta": delta, "weight": w})
    return pd.DataFrame(rows)


def chain_curve(delta_tbl, age_min, age_max):
    """Weighted-average delta per age transition, then cumulative sum to
    reconstruct the full curve (relative units, arbitrary zero point)."""
    wmean = delta_tbl.groupby("age_from").apply(
        lambda g: np.average(g["delta"], weights=g["weight"]), include_groups=False
    ).reindex(range(age_min, age_max))
    n_obs = delta_tbl.groupby("age_from")["delta"].count().reindex(range(age_min, age_max))
    curve = wmean.fillna(0).cumsum()
    curve.iloc[0] = 0  # anchor start at 0 explicitly
    return curve, n_obs


def smooth_index(index_series, n_obs, age_min, age_max, floor_zero=True):
    """Weighted smoothing spline over the raw % index, weighted by sqrt(n_obs)
    (more observations at that age = more trusted). Falls back to raw values
    if too few points have support. This is the standard fix for delta-method
    noise at career tails where only a handful of elite survivors remain."""
    ages = np.array(index_series.index)
    vals = index_series.values.astype(float)
    weights = np.sqrt(n_obs.reindex(index_series.index).fillna(0).values + 1)
    mask = ~np.isnan(vals)
    if mask.sum() < 6:
        return index_series  # not enough points to smooth meaningfully
    try:
        # smoothing factor scaled to number of points; higher s = smoother
        spline = UnivariateSpline(ages[mask], vals[mask], w=weights[mask],
                                   k=3, s=len(ages[mask]) * 40)
        smoothed = spline(ages)
    except Exception:
        smoothed = vals
    if floor_zero:
        smoothed = np.clip(smoothed, 0, None)
    return pd.Series(smoothed, index=index_series.index)



def main():
    panel = pd.read_csv(PANEL_PATH)
    panel = panel[panel["position"].isin(POSITIONS)].copy()
    panel["age"] = panel["age"].astype(float)

    all_curves = []
    summary_rows = []

    for pos in POSITIONS:
        sub = panel[panel["position"] == pos].copy()
        # drop extreme low-sample seasons (garbage-time cameo, IR return for 1 game)
        # from CONTRIBUTING to deltas' games weight, but keep them in the panel
        age_min, age_max = 20, 40

        # --- RATE curve (pts_per_game skill trajectory) ---
        rate_deltas = build_delta_table(sub, "pts_per_game")
        rate_curve, rate_n = chain_curve(rate_deltas, age_min, age_max)

        # --- VOLUME curve (games played / availability trajectory) ---
        vol_deltas = build_delta_table(sub, "games")
        vol_curve, vol_n = chain_curve(vol_deltas, age_min, age_max)

        # peak age = age with max value on the rate curve (restricted to ages
        # with reasonable sample support, n_obs >= 15)
        supported = rate_n[rate_n >= 15].index
        if len(supported) > 0:
            peak_age = rate_curve.loc[supported].idxmax()
        else:
            peak_age = ANCHOR_AGE_GUESS[pos]

        # index rate curve to peak = 100 (relative production vs peak-age self)
        peak_val = rate_curve.loc[peak_age]
        rate_indexed = 100 + (rate_curve - peak_val)  # additive delta units -> convert to index
        # convert additive cumsum (pts/game units) into a % index around peak
        # use peak absolute average pts_per_game at peak age as denominator
        peak_abs_ppg = sub[sub["age"].round() == peak_age]["pts_per_game"].mean()
        if pd.isna(peak_abs_ppg) or peak_abs_ppg == 0:
            peak_abs_ppg = sub["pts_per_game"].mean()
        rate_pct_index = 100 * (1 + (rate_curve - peak_val) / peak_abs_ppg)

        # volume curve indexed similarly (games played relative to peak-age games)
        peak_games_abs = sub[sub["age"].round() == peak_age]["games"].mean()
        if pd.isna(peak_games_abs) or peak_games_abs == 0:
            peak_games_abs = sub["games"].mean()
        vol_peak_val = vol_curve.loc[peak_age] if peak_age in vol_curve.index else 0
        vol_pct_index = 100 * (1 + (vol_curve - vol_peak_val) / peak_games_abs)

        # smooth both indices with a weighted spline to fix tail noise
        # (sparse elite-survivor samples at 35+ produce jagged/negative raw values)
        rate_pct_index = smooth_index(rate_pct_index, rate_n, age_min, age_max)
        vol_pct_index = smooth_index(vol_pct_index, vol_n, age_min, age_max)

        # combined season-value curve = rate_index * volume_index / 100
        # (both are % of peak; product captures "how much of peak-season total
        #  fantasy value does a player at this age produce")
        combined_index = (rate_pct_index * vol_pct_index) / 100

        for age in range(age_min, age_max):
            all_curves.append({
                "position": pos,
                "age": age,
                "rate_index": rate_pct_index.get(age, np.nan),
                "volume_index": vol_pct_index.get(age, np.nan),
                "combined_index": combined_index.get(age, np.nan),
                "n_rate_obs": rate_n.get(age, 0),
                "n_vol_obs": vol_n.get(age, 0),
            })

        summary_rows.append({
            "position": pos,
            "peak_age": peak_age,
            "peak_abs_ppg": round(peak_abs_ppg, 2),
            "peak_abs_games": round(peak_games_abs, 2),
            "n_players": sub["player_id"].nunique(),
            "n_player_seasons": len(sub),
        })

    curves_df = pd.DataFrame(all_curves)
    summary_df = pd.DataFrame(summary_rows)

    curves_path = os.path.join(OUT_DIR, "aging_curves.csv")
    summary_path = os.path.join(OUT_DIR, "aging_curve_summary.csv")
    curves_df.to_csv(curves_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    print("=== Peak ages & anchors ===")
    print(summary_df.to_string(index=False))
    print(f"\nWrote {curves_path}")
    print(f"Wrote {summary_path}")

    # quick sanity print: combined index at a few key ages per position
    print("\n=== Combined season-value index (100 = peak-age level) ===")
    for pos in POSITIONS:
        row = curves_df[curves_df["position"] == pos].set_index("age")["combined_index"]
        ages_to_show = [22, 24, 26, 28, 30, 32, 34, 36]
        vals = [f"{a}:{row.get(a, np.nan):.0f}" for a in ages_to_show if a in row.index]
        print(f"{pos:3s}  " + "  ".join(vals))


if __name__ == "__main__":
    main()
