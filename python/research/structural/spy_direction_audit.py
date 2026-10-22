#!/usr/bin/env python3
"""
Pre-registered audit of the SPY next-day direction model: feature ablation and calibration.

Research only. Reads the feature matrix and the production logistic pickle (read-only),
writes nothing under models/, changes no production code.

PROTOCOL (fixed before running; nothing below is tuned on test folds)

Data
  outputs/features/markets/spy_features.csv, target next_dir_1d (1 if next-day SPY
  close-to-close return > 0). Drop any row dated today (provisional intraday row).
  Drop rows with NaN target. Feature universe = the feature list stored in the newest
  production pickle models/markets/spy/spy_logistic_next_dir_1d_*.pkl, minus any feature
  failing the production staleness rule (last non-NaN date more than 10 days before the
  last row). Remaining NaNs are imputed with the TRAINING-FOLD median only; a feature
  with no training data at all (median NaN) is filled with 0, which makes it a constant
  column in training and therefore gets a zero coefficient.

Folds
  Expanding-window walk-forward by calendar year, Y = 2019 .. 2026: train on rows dated
  before Jan 1 of Y, test on rows in year Y. The locked holdout (2024+) is reported
  separately by pooling the 2024, 2025, 2026 test folds.

Model family (identical for every feature set)
  StandardScaler + L2 LogisticRegression (lbfgs, max_iter 500, random_state 42).
  C chosen by inner 5-fold CV (KFold, no shuffle, neg log-loss) on the training fold
  only, from {0.01, 0.03, 0.1, 0.3, 1}. Inside the forward-selection loop only, the grid
  is reduced to {0.03, 0.1, 0.3} to bound compute (stated in the report); the frozen
  selected set is then re-evaluated with the full 5-value grid.

(A) Ablation
  1. Reference: full production feature list.
  2. Leave-one-group-out, groups defined explicitly in FEATURE_GROUPS below by data
     source. For each group refit without it and report the change in pooled OOS
     log-loss, AUC, accuracy, the per-year deltas, and the standard error of the yearly
     log-loss delta (sd / sqrt(8)). A group is called load-bearing only if removing it
     worsens log-loss (delta > 0) in at least 6 of the 8 years.
  3. Forward selection by walk-forward log-loss. Candidates pre-screened to the 40
     features with the largest absolute univariate Spearman correlation with the target
     on data before 2022. Selection uses ONLY the folds whose test year is before 2022
     (2019, 2020, 2021; all training data for those folds is also before 2022), pooled
     log-loss, greedy add, stop when the improvement is below 0.0005 or no candidate
     improves. The frozen set is then evaluated on the 2022-2026 folds and compared
     with the full set on the same folds.
  4. Stability: for the selected set, the coefficient sign in each yearly refit and the
     fraction of folds matching the majority sign.

(B) Calibration
  1. Pool walk-forward OOS predictions 2019-2026 for the full and the small model.
     Reliability table in 10 equal-count bins (mean predicted, observed frequency, n),
     Brier score with the Murphy decomposition (reliability, resolution, uncertainty)
     computed on the same bins, and expected calibration error (count-weighted mean
     absolute gap). Note: for the small model the 2019-2021 predictions are not
     strictly out of sample for the FEATURE CHOICE (selection used those folds); the
     2022-2026 rows are clean, and the tables are also written for 2022+ only.
  2. Threshold audit at 0.50, 0.51, 0.52, 0.53, 0.55, 0.57, 0.60: fraction of days
     traded, hit rate, mean next-day return on traded days in bps, for pooled OOS and
     2024+ only; UP calls are p >= t, DOWN calls are p <= 1 - t (hit = next day down,
     return reported as the raw SPY return on those days, so a working DOWN call shows
     a negative number).
  3. Informativeness: is observed frequency monotone in predicted probability (Spearman
     rank of bin index vs observed frequency), and does the top decile beat the bottom
     decile by more than 2 standard errors (two-proportion SE)?

Determinism: seed 42, no shuffling anywhere, KFold without shuffle.

AMENDMENT AFTER FIRST RUN (disclosed, not tuned on outcomes)
  The first registered run produced a 2025 fold log-loss of 1.19 with 112 of 250
  predictions outside [0.05, 0.95]. Inspecting train-vs-test z-scores (not outcomes)
  showed vix_move_ratio at |z| up to 63 on 209 of 250 days in 2025. The underlying
  outputs/markets/cache/MOVE.csv is not the ICE BofA MOVE index (which trades around
  80-140): its close decays monotonically from 605 (Mar 2021) to 8 (Oct 2026), so
  VIX / MOVE drifts from 0.03 to 3.9. This is a broken data source, not a signal.
  Protocol change: the registered "full" run is still reported exactly as specified
  (production list, nothing removed). A second reference "full_clean" = production list
  minus vix_move_ratio is added and is used as the base for the leave-one-group-out
  ablation, the forward-selection candidate pool, and the full-vs-small comparison,
  because the ablation against a reference that contains a known-broken column would
  only measure the broken column. Both references appear in every table.
"""
import os
import re
import sys
import time
import pickle
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import KFold
from sklearn.metrics import log_loss, roc_auc_score, accuracy_score, brier_score_loss

warnings.filterwarnings("ignore")
np.random.seed(42)
SEED = 42

ROOT = "/home/jhcv/discordBot"
FEAT_PATH = os.path.join(ROOT, "outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.join(ROOT, "models/markets/spy")
OUT = os.path.join(ROOT, "outputs/research/structural")
os.makedirs(OUT, exist_ok=True)

TARGET = "next_dir_1d"
RET = "next_ret_1d"
YEARS = list(range(2019, 2027))
SELECT_YEARS = [y for y in YEARS if y < 2022]
EVAL_YEARS = [y for y in YEARS if y >= 2022]
HOLDOUT_START = 2024
C_GRID_FULL = [0.01, 0.03, 0.1, 0.3, 1.0]
C_GRID_SEL = [0.03, 0.1, 0.3]
STALE_MAX_LAG_DAYS = 10
PRESCREEN_N = 40
FS_MIN_GAIN = 0.0005
THRESHOLDS = [0.50, 0.51, 0.52, 0.53, 0.55, 0.57, 0.60]
MIN_LOADBEARING_YEARS = 6
# Disclosed post-hoc data-integrity exclusion (see docstring amendment). Broken source,
# not a performance-based removal.
INTEGRITY_EXCLUSIONS = ["vix_move_ratio"]

# Explicit group definitions by data source (names from the production list).
FEATURE_GROUPS = {
    "price_tech": ["spy_ret_r5", "spy_consec_down", "spy_drawdown_252",
                   "days_since_regime_change", "regime_transition_flag"],
    "vix_term": ["vix_level", "vix_z21", "vvix_chg_5d", "vix_term_z21",
                 "vix_rv_x_bear", "vix_rv_x_chop", "vix_move_ratio"],
    "cross_asset": ["gld_ret_1d", "gld_spy_corr_21", "gld_corr_x_era", "gld_corr_era_flag",
                    "gld_corr63_x_era", "qqq_ret_1d", "xle_spy_rs_5d", "sector_risk_off_r5",
                    "XLF_ret", "XLI_ret", "XLY_ret", "copper_gold_ratio", "hy_ig_spread"],
    "macro_fred": ["fedfunds_chg_21d", "fomc_window", "net_liq_chg_21d", "net_liq_chg_63d",
                   "t5yie_chg_5d", "breakeven_slope"],
    "dix_gex": ["dix_chg_x_bear"],
    "intraday": ["vwap_dev_open", "vwap_dev_r5", "vwap_dev_am", "open_drive_flag",
                 "late_reversal_flag", "rv_1min_full_ann", "bpv_1min_ann", "jump_ratio",
                 "jump_ratio_r5", "session_only_ret", "bucket1_ret", "bucket6_ret",
                 "or_15m_range", "or_15m_breakout_up", "or_15m_breakout_down",
                 "vol_entropy_30m", "signed_vol_imbalance", "vwap_dev_close_full",
                 "session_runup", "session_drawdown", "bpv_vix_spread"],
    "moon": ["moon_phase_cos", "days_to_new_moon", "moon_full_flag", "moon_new_flag"],
    "weather": ["tavg_nyc", "prcp_flag_nyc", "sunshine_proxy_nyc", "tavg_chi",
                "temp_range_chi", "sunshine_proxy_chi"],
    "breadth": ["pct_above_ma50", "pct_above_ma200", "pct_above_ma50_chg5d", "advance_pct",
                "ad_line_10d_chg", "new_high_pct_63d", "new_low_pct_63d", "nh_nl_spread_63d",
                "cross_sectional_dispersion", "breadth_thrust_flag"],
}

T0 = time.time()
N_FITS = 0


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# ------------------------------------------------------------------ data
def load_production_features():
    pat = re.compile(r"^spy_logistic_next_dir_1d_(\d{4}-\d{2}-\d{2})\.pkl$")
    cands = sorted([f for f in os.listdir(MODEL_DIR) if pat.match(f)])
    if not cands:
        sys.exit("no production logistic pickle found")
    with open(os.path.join(MODEL_DIR, cands[-1]), "rb") as f:
        b = pickle.load(f)
    return list(b["features"]), cands[-1]


def load_data():
    df = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date")
    today = pd.Timestamp.today().normalize()
    n0 = len(df)
    df = df[df["date"] < today]
    df = df.dropna(subset=[TARGET]).reset_index(drop=True)
    df[TARGET] = df[TARGET].astype(int)
    log(f"rows {n0} -> {len(df)} after dropping today / NaN target; "
        f"{df['date'].min().date()} to {df['date'].max().date()}")
    return df


def stale_filter(df, feats):
    last = df["date"].max()
    keep, dropped = [], []
    for f in feats:
        if f not in df.columns:
            dropped.append((f, "missing"))
            continue
        valid = df.loc[df[f].notna(), "date"]
        if valid.empty or (last - valid.max()).days > STALE_MAX_LAG_DAYS:
            dropped.append((f, str(valid.max().date()) if not valid.empty else "never"))
        else:
            keep.append(f)
    return keep, dropped


# ------------------------------------------------------------------ model
def fit_predict(tr, te, feats, c_grid):
    """Impute with training medians, choose C by inner 5-fold CV, return test probs and model."""
    global N_FITS
    Xtr = tr[feats].copy()
    med = Xtr.median()
    med = med.fillna(0.0)
    Xtr = Xtr.fillna(med).values
    Xte = te[feats].fillna(med).values
    ytr = tr[TARGET].values
    best_c, best_ll = None, np.inf
    if len(c_grid) == 1:
        best_c = c_grid[0]
    else:
        kf = KFold(n_splits=5, shuffle=False)
        for c in c_grid:
            lls = []
            for itr, iva in kf.split(Xtr):
                m = Pipeline([("sc", StandardScaler()),
                              ("m", LogisticRegression(C=c, max_iter=500, random_state=SEED))])
                m.fit(Xtr[itr], ytr[itr])
                N_FITS += 1
                p = np.clip(m.predict_proba(Xtr[iva])[:, 1], 1e-6, 1 - 1e-6)
                lls.append(log_loss(ytr[iva], p, labels=[0, 1]))
            ll = float(np.mean(lls))
            if ll < best_ll:
                best_ll, best_c = ll, c
    m = Pipeline([("sc", StandardScaler()),
                  ("m", LogisticRegression(C=best_c, max_iter=500, random_state=SEED))])
    m.fit(Xtr, ytr)
    N_FITS += 1
    p = m.predict_proba(Xte)[:, 1]
    return p, m, best_c


def base_rate_predict(tr, te):
    p = float(tr[TARGET].mean())
    return np.full(len(te), p)


def walk_forward(df, feats, years, c_grid, label):
    """Returns a DataFrame of OOS predictions (date, year, y, ret, p) and per-fold models."""
    out, models = [], {}
    for y in years:
        tr = df[df["date"] < pd.Timestamp(f"{y}-01-01")]
        te = df[df["date"].dt.year == y]
        if len(te) == 0:
            continue
        if len(feats) == 0:
            p = base_rate_predict(tr, te)
            m, c = None, np.nan
        else:
            p, m, c = fit_predict(tr, te, feats, c_grid)
        models[y] = (m, c)
        out.append(pd.DataFrame({"date": te["date"].values, "year": y, "y": te[TARGET].values,
                                 "ret": te[RET].values, "p": p, "model": label}))
    return pd.concat(out, ignore_index=True), models


def metrics(y, p, ret=None):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    d = {"n": int(len(y)),
         "logloss": float(log_loss(y, p, labels=[0, 1])),
         "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 and np.std(p) > 0 else 0.5,
         "acc": float(accuracy_score(y, (p >= 0.5).astype(int))),
         "brier": float(brier_score_loss(y, p)),
         "base_rate": float(np.mean(y))}
    return d


def yearly_metrics(pred):
    rows = []
    for y, g in pred.groupby("year"):
        d = metrics(g["y"].values, g["p"].values)
        d["year"] = y
        rows.append(d)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ calibration helpers
def reliability(pred, nbins=10):
    p, y = pred["p"].values, pred["y"].values
    order = np.argsort(p, kind="mergesort")
    bins = np.array_split(order, nbins)
    rows = []
    N = len(p)
    obar = y.mean()
    rel = res = 0.0
    for i, idx in enumerate(bins):
        pb, ob, n = p[idx].mean(), y[idx].mean(), len(idx)
        rel += n / N * (pb - ob) ** 2
        res += n / N * (ob - obar) ** 2
        rows.append({"bin": i + 1, "p_lo": p[idx].min(), "p_hi": p[idx].max(),
                     "mean_pred": pb, "obs_freq": ob, "n": n,
                     "se_obs": np.sqrt(ob * (1 - ob) / n) if n > 0 else np.nan})
    tab = pd.DataFrame(rows)
    unc = obar * (1 - obar)
    ece = float(np.sum(tab["n"] / N * np.abs(tab["mean_pred"] - tab["obs_freq"])))
    brier = float(np.mean((p - y) ** 2))
    rho, _ = stats.spearmanr(tab["bin"], tab["obs_freq"])
    top, bot = tab.iloc[-1], tab.iloc[0]
    diff = top["obs_freq"] - bot["obs_freq"]
    se = np.sqrt(top["obs_freq"] * (1 - top["obs_freq"]) / top["n"]
                 + bot["obs_freq"] * (1 - bot["obs_freq"]) / bot["n"])
    summ = {"brier": brier, "reliability": rel, "resolution": res, "uncertainty": unc,
            "ece": ece, "bin_monotone_spearman": float(rho),
            "top_minus_bottom": float(diff), "top_minus_bottom_se": float(se),
            "top_minus_bottom_z": float(diff / se) if se > 0 else np.nan,
            "n": int(N), "base_rate": float(obar)}
    return tab, summ


def threshold_table(pred, label, period):
    rows = []
    y, p, r = pred["y"].values, pred["p"].values, pred["ret"].values
    N = len(y)
    for t in THRESHOLDS:
        up = p >= t
        dn = p <= 1 - t
        for side, mask, hit in (("UP", up, y == 1), ("DOWN", dn, y == 0)):
            n = int(mask.sum())
            rows.append({"model": label, "period": period, "threshold": t, "side": side,
                         "frac_days": n / N, "n": n,
                         "hit_rate": float(hit[mask].mean()) if n else np.nan,
                         "hit_se": float(np.sqrt(hit[mask].mean() * (1 - hit[mask].mean()) / n)) if n else np.nan,
                         "mean_ret_bps": float(np.nanmean(r[mask]) * 1e4) if n else np.nan,
                         "ret_se_bps": float(np.nanstd(r[mask], ddof=1) / np.sqrt(n) * 1e4) if n > 1 else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main
def main():
    df = load_data()
    prod_feats, prod_file = load_production_features()
    log(f"production list from {prod_file}: {len(prod_feats)} features")
    feats, dropped = stale_filter(df, prod_feats)
    log(f"stale filter dropped {len(dropped)}: {dropped}")
    grouped = {f for g in FEATURE_GROUPS.values() for f in g}
    ungrouped = [f for f in feats if f not in grouped]
    if ungrouped:
        FEATURE_GROUPS["other"] = ungrouped
        log(f"ungrouped features placed in 'other': {ungrouped}")
    groups = {g: [f for f in fl if f in feats] for g, fl in FEATURE_GROUPS.items()}
    groups = {g: fl for g, fl in groups.items() if fl}

    # ---- (A1) full reference (registered) and full_clean (data-integrity exclusion)
    log("A1: full model walk-forward (registered production list)")
    pred_full_reg, models_full_reg = walk_forward(df, feats, YEARS, C_GRID_FULL, "full")
    yr_full_reg = yearly_metrics(pred_full_reg)
    log(f"   full pooled: {metrics(pred_full_reg['y'].values, pred_full_reg['p'].values)}")
    log(f"   C by year: { {y: c for y, (m, c) in models_full_reg.items()} }")
    feats_reg = list(feats)
    feats = [f for f in feats if f not in INTEGRITY_EXCLUSIONS]
    groups = {g: [f for f in fl if f in feats] for g, fl in groups.items()}
    groups = {g: fl for g, fl in groups.items() if fl}
    log(f"A1b: full_clean = production list minus {INTEGRITY_EXCLUSIONS} ({len(feats)} features)")
    pred_full, models_full = walk_forward(df, feats, YEARS, C_GRID_FULL, "full_clean")
    yr_full = yearly_metrics(pred_full)
    log(f"   full_clean pooled: {metrics(pred_full['y'].values, pred_full['p'].values)}")
    log(f"   C by year: { {y: c for y, (m, c) in models_full.items()} }")

    # ---- (A2) leave-one-group-out
    log("A2: leave-one-group-out")
    abl_rows = []
    for g, fl in groups.items():
        rest = [f for f in feats if f not in fl]
        pred_g, _ = walk_forward(df, rest, YEARS, C_GRID_FULL, f"minus_{g}")
        yr_g = yearly_metrics(pred_g)
        d_ll = yr_g["logloss"].values - yr_full["logloss"].values   # >0 means removal hurt
        pooled_g = metrics(pred_g["y"].values, pred_g["p"].values)
        pooled_f = metrics(pred_full["y"].values, pred_full["p"].values)
        row = {"group": g, "n_features": len(fl),
               "delta_logloss": pooled_g["logloss"] - pooled_f["logloss"],
               "delta_logloss_se": float(np.std(d_ll, ddof=1) / np.sqrt(len(d_ll))),
               "delta_auc": pooled_g["auc"] - pooled_f["auc"],
               "delta_acc": pooled_g["acc"] - pooled_f["acc"],
               "years_removal_hurt": int((d_ll > 0).sum()), "n_years": len(d_ll),
               "load_bearing": bool((d_ll > 0).sum() >= MIN_LOADBEARING_YEARS),
               "features": ";".join(fl)}
        for y, v in zip(yr_full["year"], d_ll):
            row[f"d_ll_{y}"] = v
        abl_rows.append(row)
        log(f"   minus {g:12s} dLL={row['delta_logloss']:+.4f} (se {row['delta_logloss_se']:.4f}) "
            f"dAUC={row['delta_auc']:+.3f} hurt {row['years_removal_hurt']}/8")
    ablation = pd.DataFrame(abl_rows).sort_values("delta_logloss", ascending=False)
    ablation.to_csv(os.path.join(OUT, "direction_audit_ablation.csv"), index=False)

    # ---- (A3) forward selection
    log("A3: forward selection (pre-2022 folds only)")
    pre = df[df["date"] < pd.Timestamp("2022-01-01")]
    sp = {}
    for f in feats:
        m = pre[[f, TARGET]].dropna()
        if len(m) < 100 or m[f].nunique() < 2:
            sp[f] = 0.0
            continue
        rho, _ = stats.spearmanr(m[f], m[TARGET])
        sp[f] = 0.0 if np.isnan(rho) else abs(rho)
    screened = sorted(sp, key=lambda f: -sp[f])[:PRESCREEN_N]
    log(f"   prescreen top-{PRESCREEN_N} |Spearman| range {sp[screened[-1]]:.4f} .. {sp[screened[0]]:.4f}")

    def pooled_ll(fl, years, c_grid):
        pr, _ = walk_forward(df, fl, years, c_grid, "fs")
        return float(log_loss(pr["y"].values, np.clip(pr["p"].values, 1e-6, 1 - 1e-6), labels=[0, 1]))

    selected, fs_rows = [], []
    current = pooled_ll([], SELECT_YEARS, C_GRID_SEL)
    fs_rows.append({"step": 0, "added": "(intercept only)", "pooled_logloss_pre2022": current,
                    "gain": np.nan, "abs_spearman": np.nan, "accepted": True})
    log(f"   step 0 intercept-only pre-2022 pooled LL {current:.5f}")
    step = 0
    while True:
        step += 1
        best_f, best_ll = None, np.inf
        for f in screened:
            if f in selected:
                continue
            ll = pooled_ll(selected + [f], SELECT_YEARS, C_GRID_SEL)
            if ll < best_ll:
                best_ll, best_f = ll, f
        gain = current - best_ll
        accepted = gain >= FS_MIN_GAIN
        fs_rows.append({"step": step, "added": best_f, "pooled_logloss_pre2022": best_ll,
                        "gain": gain, "abs_spearman": sp[best_f], "accepted": accepted})
        log(f"   step {step} best add {best_f} LL {best_ll:.5f} gain {gain:+.5f} "
            f"{'ACCEPT' if accepted else 'STOP'} (fits so far {N_FITS})")
        if not accepted:
            break
        selected.append(best_f)
        current = best_ll
        if len(selected) >= 15:
            log("   cap of 15 features reached")
            break
    log(f"   selected ({len(selected)}): {selected}")

    # Small model: selected set. If nothing selected, fall back to the single best
    # first-step feature so the calibration/threshold tables still have a small model,
    # and say so loudly.
    small_fallback = False
    if len(selected) == 0:
        small_fallback = True
        selected = [fs_rows[1]["added"]]
        log(f"   NOTHING selected; small model falls back to top first-step feature {selected}")

    pred_small, models_small = walk_forward(df, selected, YEARS, C_GRID_FULL, "small")
    yr_small = yearly_metrics(pred_small)
    pred_base = pred_full.copy()
    pred_base["model"] = "always_up"
    # always-UP baseline probability = training base rate for each fold
    pb, _ = walk_forward(df, [], YEARS, [1.0], "always_up")
    pred_base["p"] = pb["p"].values
    yr_base = yearly_metrics(pred_base)

    def sub(pred, years):
        return pred[pred["year"].isin(years)]

    fs = pd.DataFrame(fs_rows)
    for lab, pr in (("full", pred_full_reg), ("full_clean", pred_full), ("small", pred_small), ("always_up", pred_base)):
        m22 = metrics(sub(pr, EVAL_YEARS)["y"].values, sub(pr, EVAL_YEARS)["p"].values)
        for k, v in m22.items():
            fs[f"{lab}_2022plus_{k}"] = v
    fs.to_csv(os.path.join(OUT, "direction_audit_forward_selection.csv"), index=False)

    # ---- (A4) stability of coefficient signs
    stab_rows = []
    for f in selected:
        signs = []
        for y in YEARS:
            m, c = models_small[y]
            if m is None:
                continue
            coef = m.named_steps["m"].coef_[0][selected.index(f)]
            signs.append(np.sign(coef))
        signs = np.array(signs)
        maj = 1.0 if (signs > 0).sum() >= (signs < 0).sum() else -1.0
        stab_rows.append({"feature": f, "majority_sign": "+" if maj > 0 else "-",
                          "frac_sign_match": float((signs == maj).mean()),
                          "signs_by_year": "".join("+" if s > 0 else ("-" if s < 0 else "0") for s in signs),
                          "mean_coef_last_fold": float(models_small[YEARS[-1]][0].named_steps["m"].coef_[0][selected.index(f)])})
    stability = pd.DataFrame(stab_rows)
    stability.to_csv(os.path.join(OUT, "direction_audit_small_stability.csv"), index=False)

    # ---- (B1) reliability
    log("B1: reliability")
    rel_tabs, rel_summ = [], {}
    for lab, pr in (("full", pred_full_reg), ("full_clean", pred_full), ("small", pred_small)):
        for period, years in (("2019-2026", YEARS), ("2022-2026", EVAL_YEARS), ("2024+", [y for y in YEARS if y >= HOLDOUT_START])):
            tab, summ = reliability(sub(pr, years))
            tab["model"], tab["period"] = lab, period
            rel_tabs.append(tab)
            rel_summ[(lab, period)] = summ
    reliab = pd.concat(rel_tabs, ignore_index=True)
    reliab.to_csv(os.path.join(OUT, "direction_audit_reliability.csv"), index=False)

    # ---- (B2) thresholds
    log("B2: thresholds")
    thr = []
    for lab, pr in (("full", pred_full_reg), ("full_clean", pred_full), ("small", pred_small)):
        thr.append(threshold_table(pr, lab, "2019-2026"))
        thr.append(threshold_table(sub(pr, [y for y in YEARS if y >= HOLDOUT_START]), lab, "2024+"))
    thr = pd.concat(thr, ignore_index=True)
    thr.to_csv(os.path.join(OUT, "direction_audit_thresholds.csv"), index=False)

    # ---- per-year table for the R chart
    yr = pd.concat([yr_full_reg.assign(model="full"), yr_full.assign(model="full_clean"),
                    yr_small.assign(model="small"), yr_base.assign(model="always_up")], ignore_index=True)
    yr.to_csv(os.path.join(OUT, "direction_audit_yearly.csv"), index=False)
    pd.concat([pred_full_reg, pred_full, pred_small], ignore_index=True).to_csv(
        os.path.join(OUT, "direction_audit_oos_predictions.csv"), index=False)

    # ---- summary
    def mline(lab, pr, years):
        m = metrics(sub(pr, years)["y"].values, sub(pr, years)["p"].values)
        return (f"| {lab} | {m['n']} | {m['logloss']:.4f} | {m['auc']:.3f} | {m['acc']:.3f} | "
                f"{m['brier']:.4f} | {m['base_rate']:.3f} |")

    hold = [y for y in YEARS if y >= HOLDOUT_START]
    L = []
    L.append("SPY next-day direction audit: ablation and calibration")
    L.append("")
    L.append(f"Generated by python/research/structural/spy_direction_audit.py. Rows {len(df)}, "
             f"{df['date'].min().date()} to {df['date'].max().date()}. Production list {prod_file} "
             f"({len(prod_feats)} features, {len(dropped)} dropped by the 10-day staleness rule). "
             f"full = registered production list ({len(feats_reg)}); full_clean = minus the disclosed "
             f"data-integrity exclusion {INTEGRITY_EXCLUSIONS} ({len(feats)}), used as the ablation and "
             f"selection reference. "
             f"Total logistic fits {N_FITS}, wall time {time.time() - T0:.0f}s. Inside the forward-selection "
             f"loop the C grid was {C_GRID_SEL}; everywhere else {C_GRID_FULL}.")
    L.append("")
    L.append("Pooled walk-forward metrics (always_up = training-fold base rate as the probability)")
    L.append("")
    L.append("| model | n | logloss | AUC | acc | Brier | base rate |")
    L.append("|---|---|---|---|---|---|---|")
    for period, years in (("2019-2026", YEARS), ("2022-2026", EVAL_YEARS), ("2024+", hold)):
        L.append(f"| period {period} | | | | | | |")
        L.append(mline("full", pred_full_reg, years))
        L.append(mline("full_clean", pred_full, years))
        L.append(mline("small", pred_small, years))
        L.append(mline("always_up", pred_base, years))
    L.append("")
    L.append("Per-year accuracy (full registered / full_clean / small / always-UP)")
    L.append("")
    for y in YEARS:
        a = yr_full.set_index("year").loc[y]; b = yr_small.set_index("year").loc[y]; c = yr_base.set_index("year").loc[y]
        r = yr_full_reg.set_index("year").loc[y]
        L.append(f"- {y}: full {r['acc']:.3f} (LL {r['logloss']:.4f}) | full_clean {a['acc']:.3f} (LL {a['logloss']:.4f}, AUC {a['auc']:.3f}) | "
                 f"small {b['acc']:.3f} (LL {b['logloss']:.4f}, AUC {b['auc']:.3f}) | always-UP {c['acc']:.3f}")
    L.append("")
    L.append("Leave-one-group-out vs full_clean (delta = metric without group minus full_clean; positive logloss delta means the group helped)")
    L.append("")
    L.append("| group | k | dLogLoss | se | dAUC | dAcc | years hurt / 8 | load-bearing |")
    L.append("|---|---|---|---|---|---|---|---|")
    for _, r in ablation.iterrows():
        L.append(f"| {r['group']} | {r['n_features']} | {r['delta_logloss']:+.4f} | {r['delta_logloss_se']:.4f} | "
                 f"{r['delta_auc']:+.3f} | {r['delta_acc']:+.3f} | {r['years_removal_hurt']} | {r['load_bearing']} |")
    L.append("")
    L.append("Forward selection (pre-2022 folds, pooled log-loss, stop at gain < 0.0005)")
    L.append("")
    for _, r in fs.iterrows():
        g = "" if pd.isna(r["gain"]) else f" gain {r['gain']:+.5f}"
        L.append(f"- step {int(r['step'])}: {r['added']} LL {r['pooled_logloss_pre2022']:.5f}{g} "
                 f"{'accepted' if r['accepted'] else 'rejected (stop)'}")
    L.append("")
    L.append(f"Selected small set ({len(selected)}){' [FALLBACK: nothing passed the gain rule, top first-step feature used]' if small_fallback else ''}: {selected}")
    L.append("")
    L.append("Coefficient sign stability across the 8 yearly refits of the small model")
    L.append("")
    for _, r in stability.iterrows():
        L.append(f"- {r['feature']}: majority {r['majority_sign']}, match {r['frac_sign_match']:.2f}, by year {r['signs_by_year']}, "
                 f"last-fold coef {r['mean_coef_last_fold']:+.3f}")
    L.append("")
    L.append("Reliability (10 equal-count bins)")
    L.append("")
    for (lab, period), s in rel_summ.items():
        L.append(f"- {lab} {period}: Brier {s['brier']:.4f} = REL {s['reliability']:.5f} - RES {s['resolution']:.5f} + UNC {s['uncertainty']:.4f}; "
                 f"ECE {s['ece']:.4f}; bin monotonicity Spearman {s['bin_monotone_spearman']:+.2f}; "
                 f"top minus bottom decile {s['top_minus_bottom']:+.3f} (se {s['top_minus_bottom_se']:.3f}, z {s['top_minus_bottom_z']:+.2f}); n {s['n']}")
    L.append("")
    for lab in ("full", "full_clean", "small"):
        t = reliab[(reliab["model"] == lab) & (reliab["period"] == "2019-2026")]
        L.append(f"{lab} model, 2019-2026, bins: " + "; ".join(
            f"[{r['mean_pred']:.3f} -> {r['obs_freq']:.3f}, n={int(r['n'])}]" for _, r in t.iterrows()))
    L.append("")
    L.append("Threshold audit (UP: p >= t; DOWN: p <= 1 - t; return = raw next-day SPY return in bps)")
    L.append("")
    L.append("| model | period | t | side | frac days | n | hit rate | mean ret bps | se bps |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in thr.iterrows():
        L.append(f"| {r['model']} | {r['period']} | {r['threshold']:.2f} | {r['side']} | {r['frac_days']:.3f} | {int(r['n'])} | "
                 f"{'' if pd.isna(r['hit_rate']) else f'{r['hit_rate']:.3f}'} | "
                 f"{'' if pd.isna(r['mean_ret_bps']) else f'{r['mean_ret_bps']:+.1f}'} | "
                 f"{'' if pd.isna(r['ret_se_bps']) else f'{r['ret_se_bps']:.1f}'} |")
    L.append("")
    with open(os.path.join(OUT, "direction_audit_summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    log("done; summary written")


if __name__ == "__main__":
    main()
