#!/usr/bin/env python3
"""
trainChopModel.py
=================
Dedicated chop-regime model: Logistic + GBM ensemble, trained exclusively on chop rows.

Motivation:
  - Universal SPY model (R36) WFCV folds span 2018-2023 — diverse regimes.
  - Live market (2024+) is 92% chop. Universal model optimizes cross-regime average,
    leaving chop-specific signal on the table.
  - Chop-specific features (fear_z21_x_chop, crash_x_chop, skew_chg_1d, AAII neutral)
    have strong consistent Spearman in chop but add noise to universal WFCV.

Architecture:
  - Filter training data to regime == 'chop' only.
  - Chop-specific feature set: RFE-52 core + chop-interaction features + SKEW + AAII.
  - 4-fold time-series WFCV on chop rows only (min_train=500; fold 1 ~319 rows skipped).
  - Logistic: Platt scaling calibration on held-out chop val rows.
  - GBM: 15-seed ensemble trained on chop rows only (same hyperparams as universal GBM).
  - Blend: 50/50 logistic_cal + gbm_chop_ensemble, reported separately.
  - Saves as spy_logistic_chop_dedicated_<date>.pkl (bundle includes gbm_chop_models key).

Usage:
  venv/bin/python3 python/trainChopModel.py
  venv/bin/python3 python/trainChopModel.py --notes "run43: chop GBM + blend"
"""

import os
import sys
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd

from sklearn.linear_model   import LogisticRegression
from sklearn.ensemble       import GradientBoostingClassifier
from sklearn.preprocessing  import StandardScaler
from sklearn.pipeline       import Pipeline
from sklearn.calibration    import CalibratedClassifierCV
from sklearn.metrics        import roc_auc_score, accuracy_score
from sklearn.feature_selection import RFECV
from scipy                  import stats

warnings.filterwarnings("ignore")

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy")
LOG_PATH  = os.path.expanduser("~/discordBot/models/meta/spy_experiment_log.csv")
os.makedirs(MODEL_DIR, exist_ok=True)

TODAY   = datetime.date.today().isoformat()
TX_COST = 0.0005   # 5bps one-way
ANNUAL  = 252

# ── Chop-specific feature set ─────────────────────────────────────────────────
# RFE-52 core (features that survived 3 RFE passes on universal model)
# + chop-specific interactions and SKEW/AAII signals
# consistent in WFCV-chop and val-chop Spearman analysis.
CHOP_FEATURES = [
    # ── RFE-52 core (kept from universal model) ───────────────────────────────
    "spy_ret_r5",
    "spy_consec_down",
    "spy_drawdown_252",
    "vix_level",
    "vix_z21",
    "vvix_chg_5d",
    "vix_term_z21",
    "gld_ret_1d",
    "gld_spy_corr_21",
    "gld_corr_x_era",
    "gld_corr_era_flag",
    "gld_corr63_x_era",
    "qqq_ret_1d",
    "xle_spy_rs_5d",
    "fedfunds_chg_21d",
    "fomc_window",
    "sector_risk_off_r5",
    "XLF_ret",
    "XLI_ret",
    "XLY_ret",
    "vwap_dev_open",
    "vwap_dev_r5",
    "vwap_dev_am",
    "vol_vwap_corr_5d",
    "open_drive_flag",
    "late_reversal_flag",
    "days_since_regime_change",
    "regime_transition_flag",
    "vix_rv_x_chop",          # kept; vix_rv_x_bear less relevant in chop
    "dix_chg_x_bear",
    "moon_phase_cos",
    "days_to_new_moon",
    "moon_full_flag",
    "moon_new_flag",
    "tavg_nyc",
    "prcp_flag_nyc",
    "sunshine_proxy_nyc",
    "tavg_chi",
    "temp_range_chi",
    "sunshine_proxy_chi",
    "wiki_sp500_views",
    "wiki_economy_views",
    "is_pre_holiday",
    "days_to_next_holiday",
    "mta_subway_riders",
    "earnings_season_flag",
    "days_to_earnings_season",
    "pres_approval_chg_21d",
    "cboe_pcr_equity",
    "trends_spy",
    "trends_crash",
    "trends_buy_stocks",
    "trends_volatility",
    "trends_fear_index",
    "trends_fear_z21",
    "gex_chg_5d",
    # ── Chop-specific additions ───────────────────────────────────────────────
    # Interactions that explicitly fire in chop regime:
    "fear_z21_x_chop",        # rho_wfcv=-0.102, rho_val=-0.123 — strongest chop signal
    "crash_x_chop",           # rho_wfcv=-0.061, rho_val=-0.092
    "vol_x_chop",             # rho_wfcv=-0.060, rho_val=-0.045
    # AAII sentiment — consistent in chop:
    "aaii_neutral",           # rho_wfcv=-0.049, rho_val=-0.073 — high neutral = indecision
    "aaii_bearish",           # rho_wfcv=+0.042, rho_val=+0.041 — contrarian: high bears = bullish
    # Macro calendar:
    "days_to_fomc",           # rho_wfcv=+0.041, rho_val=+0.075
    # VVIX:
    "vvix_z21",               # rho_wfcv=-0.054, rho_val=-0.069
    # Labor / macro sentiment:
    "ccsa_z52",               # rho_wfcv=+0.031, rho_val=+0.059
    "gld_z21",                # rho_wfcv=+0.066, rho_val=+0.033
    # SKEW — consistent in chop specifically:
    "skew_chg_1d",            # rho_wfcv=+0.081, rho_val=+0.099 — cleanest new feature
    "skew_chg_5d",            # rho_wfcv=+0.047, rho_val=+0.025
    "vix9d_z21",              # rho_wfcv=-0.036, rho_val=-0.069
]

SPARSE_FEATURES = [
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    "vwap_dev_open", "vwap_dev_r5", "vwap_dev_am", "vol_vwap_corr_5d",
    "open_drive_flag", "late_reversal_flag",
    "premarket_ret", "premarket_vol_ratio",
    "vix_z252", "vvix_z21", "vvix_chg_5d", "vix_term_slope", "vix_term_z21",
    "vix_spike_flag", "vix_ma20_ratio", "vwap_dev_close",
    "gld_corr_x_vol", "gld_spy_corr_63", "gld_corr63_x_era",
    "moon_phase", "moon_phase_sin", "moon_phase_cos",
    "days_to_full_moon", "days_to_new_moon", "moon_full_flag", "moon_new_flag",
    "tavg_nyc", "temp_range_nyc", "prcp_flag_nyc", "snow_flag_nyc", "sunshine_proxy_nyc",
    "tavg_chi", "temp_range_chi", "prcp_flag_chi", "sunshine_proxy_chi",
    "wiki_stock_market_views", "wiki_sp500_views", "wiki_economy_views",
    "wiki_total_views", "wiki_views_z21",
    "is_pre_holiday", "is_post_holiday", "days_to_next_holiday", "holiday_week",
    "mta_subway_riders", "mta_bus_riders", "mta_total_riders", "mta_riders_z21",
    "daylight_hours_nyc", "daylight_chg_7d", "daylight_z365",
    "earnings_season_flag", "days_to_earnings_season",
    "pres_approval", "pres_approval_chg_21d",
    "cboe_pcr_equity",
    "trends_spy", "trends_crash", "trends_recession", "trends_buy_stocks",
    "trends_volatility", "trends_fear_index", "trends_fear_z21",
    "trends_bankruptcy", "trends_unemployment", "trends_payday_loan",
    "trends_margin_call", "trends_how_invest", "trends_distress_index",
    "trends_risk_appetite_index",
    "aaii_bullish", "aaii_bearish", "aaii_neutral", "aaii_bull_bear_spread", "aaii_bull_z8",
    "cfg_value", "cfg_z21", "cfg_extreme_fear", "cfg_greed",
    "icsa_z52", "icsa_chg_4w", "ccsa_z52", "umcsent_z12", "umcsent_chg_3m",
    "rate_chg_63d", "rate_chg_252d", "rate_shock_flag", "rate_easing_flag",
    "rate_speed_z63", "rate_shock_x_bear", "rate_speed_x_bear",
    "ret_skew_21", "ret_skew_63", "ret_kurt_21", "ret_skew_z21",
    "drawdown_63d", "regime_age_z",
    "fear_z21_x_bear", "fear_z21_x_bull", "fear_z21_x_chop",
    "crash_x_bear", "recession_x_bear", "vol_x_bear", "buy_x_bull",
    "vol_x_chop", "crash_x_chop", "distress_x_bear", "distress_x_chop",
    "aaii_spread_x_bear", "aaii_spread_x_bull",
    "cfg_z21_x_bear", "cfg_z21_x_bull",
    "icsa_z52_x_bear", "icsa_z52_x_chop",
    "fear_z21_x_rate_shock", "recession_x_rate_shock", "icsa_x_rate_shock",
    "cboe_skew", "skew_z21", "skew_z63", "skew_chg_5d", "skew_chg_1d",
    "skew_high_flag", "vix9d", "vix9d_z21", "vix9d_vix30_ratio",
    "gex_b", "gex_z21", "gex_z63", "gex_chg_1d", "gex_chg_5d",
    "gld_z21", "vvix_z21", "days_to_fomc", "ccsa",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def prep_chop(df_raw, features):
    """Filter to chop rows, impute sparse, dropna on core."""
    df = df_raw[df_raw["regime"] == "chop"].copy()
    avail = [f for f in features if f in df.columns]
    for col in SPARSE_FEATURES:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)
    core = [f for f in avail if f not in SPARSE_FEATURES]
    df = df.dropna(subset=core + ["next_dir_1d"]).sort_values("date").reset_index(drop=True)
    return df, avail


def make_pipeline(C=0.1):
    return Pipeline([
        ("sc", StandardScaler()),
        ("m",  LogisticRegression(C=C, max_iter=2000, random_state=42,
                                  class_weight="balanced")),
    ])


def dir_accuracy(y_true, y_pred_prob, thresh=0.5):
    return float(accuracy_score(y_true, (y_pred_prob >= thresh).astype(int)))


N_GBM_SEEDS = 15


def train_chop_gbm_ensemble(X_tr, y_tr, X_va, n_seeds=N_GBM_SEEDS):
    """Train a 15-seed GBM ensemble on chop rows. Returns (models, val_probs_avg)."""
    seeds = list(range(0, n_seeds * 7, 7))
    models = []
    val_probs = np.zeros(len(X_va))
    for seed in seeds:
        m = Pipeline([
            ("sc", StandardScaler()),
            ("m", GradientBoostingClassifier(
                n_estimators=300, learning_rate=0.03,
                max_depth=2, min_samples_leaf=50,
                subsample=0.7, random_state=seed)),
        ])
        m.fit(X_tr, y_tr)
        val_probs += m.predict_proba(X_va)[:, 1]
        models.append(m)
    val_probs /= n_seeds
    return models, val_probs


# ── chop-only WFCV ────────────────────────────────────────────────────────────

def chop_wfcv(df, features, n_folds=5, fold_size=252, min_train_rows=500):
    """
    Walk-forward CV on chop rows only.
    Each fold: train on all chop rows before fold start, val on chop rows in fold window.
    fold_size is the calendar window (trading days) per fold, not chop-row count.

    min_train_rows: skip folds with fewer than this many training rows. Default 500 —
    fold 1 typically has ~319 rows (pre-2019 chop history is thin) and produces
    unreliable estimates that drag down the mean. Folds 2-5 have 617-1373 rows each.
    """
    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = fold_size * n_folds
    if n < total_window + fold_size:
        print(f"  [WFCV] insufficient data: {n} dates, need {total_window + fold_size}")
        return None

    train_start_idx = n - total_window - fold_size
    fold_accs, fold_aucs = [], []
    fold_details = []

    for i in range(n_folds):
        fold_start = dates[train_start_idx + fold_size + i * fold_size]
        fold_end   = dates[min(train_start_idx + fold_size + (i + 1) * fold_size - 1, n - 1)]

        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()

        if len(tr) < min_train_rows or len(va) < 20:
            print(f"  fold {i+1}: skipped (tr={len(tr)} < min_train={min_train_rows} or va={len(va)} < 20)")
            continue

        avail = [f for f in features if f in tr.columns]
        X_tr, y_tr = tr[avail].values, tr["next_dir_1d"].values
        X_va, y_va = va[avail].values, va["next_dir_1d"].values
        val_rets    = va["SPY_ret"].values if "SPY_ret" in va.columns else np.zeros(len(va))

        m = make_pipeline()
        m.fit(X_tr, y_tr)
        p = m.predict_proba(X_va)[:, 1]

        acc = dir_accuracy(y_va, p)
        auc = float(roc_auc_score(y_va, p)) if len(np.unique(y_va)) > 1 else float("nan")
        fold_accs.append(acc)
        fold_aucs.append(auc)

        # Simple threshold backtest on chop rows
        pos    = (p > 0.51).astype(float)   # chop-lower threshold
        dr     = pos * val_rets - np.abs(np.diff(np.append(0, pos))) * TX_COST
        ar     = dr.mean() * ANNUAL
        av     = dr.std() * np.sqrt(ANNUAL)
        sharpe = ar / av if av > 0 else 0.0
        mdd    = float(((np.maximum.accumulate(np.cumprod(1 + dr)) -
                         np.cumprod(1 + dr)) /
                        np.maximum.accumulate(np.cumprod(1 + dr))).max())

        bh_dr  = val_rets
        bh_ar  = bh_dr.mean() * ANNUAL
        bh_av  = bh_dr.std() * np.sqrt(ANNUAL)
        bh_sh  = bh_ar / bh_av if bh_av > 0 else 0.0

        detail = {
            "fold": i + 1,
            "fold_start": str(fold_start.date()),
            "fold_end":   str(fold_end.date()),
            "tr_chop_rows": len(tr),
            "va_chop_rows": len(va),
            "acc": round(acc, 4),
            "auc": round(auc, 4),
            "bt_sharpe": round(sharpe, 3),
            "bt_mdd":    round(mdd, 3),
            "bh_sharpe": round(bh_sh, 3),
            "n_signals": int(pos.sum()),
        }
        fold_details.append(detail)
        print(f"  fold {i+1} ({detail['fold_start']} - {detail['fold_end']})  "
              f"chop_tr={len(tr):>4} chop_va={len(va):>3}  "
              f"acc={acc:.4f}  auc={auc:.4f}  "
              f"bt@0.51: Sharpe={sharpe:.3f}  BH={bh_sh:.3f}  n={int(pos.sum())}")

    if not fold_accs:
        return None

    return {
        "wfcv_dir_acc_mean": round(float(np.mean(fold_accs)), 4),
        "wfcv_dir_acc_std":  round(float(np.std(fold_accs)),  4),
        "wfcv_auc_mean":     round(float(np.nanmean(fold_aucs)), 4),
        "wfcv_auc_std":      round(float(np.nanstd(fold_aucs)),  4),
        "fold_details":      fold_details,
    }


# ── RFE on chop rows ──────────────────────────────────────────────────────────

def run_rfe(df, features):
    avail = [f for f in features if f in df.columns]
    split = max(len(df) - 252, int(len(df) * 0.8))
    tr    = df.iloc[:split]
    X     = tr[avail].values
    y     = tr["next_dir_1d"].values
    sc    = StandardScaler()
    Xs    = sc.fit_transform(X)
    est   = LogisticRegression(C=0.1, max_iter=2000, random_state=42)
    rfecv = RFECV(estimator=est, cv=3, scoring="roc_auc",
                  min_features_to_select=35, n_jobs=-1)
    rfecv.fit(Xs, y)
    selected = [avail[i] for i, s in enumerate(rfecv.support_) if s]
    print(f"  [RFE] {len(selected)} features selected from {len(avail)}")
    return selected


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", type=str, default="chop model")
    parser.add_argument("--skip-rfe", action="store_true")
    parser.add_argument("--min-train-rows", type=int, default=500,
                        help="Min chop rows in train fold to include in WFCV (default 500; fold 1 ~319 rows is skipped)")
    args = parser.parse_args()

    print("[trainChopModel] loading features...")
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df_raw = df_raw.sort_values("date").reset_index(drop=True)
    print(f"  {len(df_raw)} total rows, "
          f"{(df_raw.regime=='chop').sum()} chop rows "
          f"({(df_raw.regime=='chop').mean():.1%})")

    df, avail = prep_chop(df_raw, CHOP_FEATURES)
    print(f"  chop after dropna: {len(df)} rows, {len(avail)} features available")
    print(f"  date range: {df.date.min().date()} to {df.date.max().date()}")
    print()

    # ── RFE: find best chop-specific feature subset ───────────────────────────
    features = avail
    if not args.skip_rfe:
        print("=" * 56)
        print("  RFE on chop rows (RFECV, LogisticRegression, cv=3)")
        print("=" * 56)
        features = run_rfe(df, avail)
        print(f"  Selected: {features}")
        print()

    # ── Chop WFCV ─────────────────────────────────────────────────────────────
    print("=" * 56)
    print("  Chop-only walk-forward CV (5 folds x 252 calendar days)")
    print("=" * 56)
    wfcv = chop_wfcv(df, features, min_train_rows=args.min_train_rows)
    if wfcv:
        print(f"\n  WFCV summary:  "
              f"dir_acc={wfcv['wfcv_dir_acc_mean']:.4f} +/-{wfcv['wfcv_dir_acc_std']:.4f}  "
              f"auc={wfcv['wfcv_auc_mean']:.4f} +/-{wfcv['wfcv_auc_std']:.4f}")
    print()

    # ── Final model: train on all chop rows except last 252 (holdout) ─────────
    VAL_DAYS = 252
    split    = max(len(df) - VAL_DAYS, int(len(df) * 0.8))
    tr_df    = df.iloc[:split].copy()
    va_df    = df.iloc[split:].copy()

    avail_f = [f for f in features if f in df.columns]
    X_tr, y_tr = tr_df[avail_f].values, tr_df["next_dir_1d"].values
    X_va, y_va = va_df[avail_f].values, va_df["next_dir_1d"].values
    val_rets    = va_df["SPY_ret"].values if "SPY_ret" in va_df.columns else np.zeros(len(va_df))

    print("=" * 56)
    print(f"  Final model: train={len(tr_df)} chop rows, val={len(va_df)} chop rows")
    print("=" * 56)

    # Train base model
    m = make_pipeline()
    m.fit(X_tr, y_tr)
    p_raw = m.predict_proba(X_va)[:, 1]

    acc_raw = dir_accuracy(y_va, p_raw)
    auc_raw = float(roc_auc_score(y_va, p_raw)) if len(np.unique(y_va)) > 1 else float("nan")
    sp_raw  = float(stats.spearmanr(y_va, p_raw).statistic)
    print(f"  Base: dir_acc={acc_raw:.4f}  AUC={auc_raw:.4f}  Spearman={sp_raw:.4f}")

    # Platt calibration: fit base on train, calibrate on val
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.base import clone
    m_base = clone(m)
    m_base.fit(X_tr, y_tr)
    # Use val set for calibration (sigmoid fit on held-out chop rows)
    p_raw_tr = m_base.predict_proba(X_va)[:, 1]
    from scipy.special import expit
    from scipy.optimize import minimize_scalar
    # Simple isotonic / Platt: fit a + b*logit(p) on val
    from sklearn.linear_model import LogisticRegression as LR
    logit_p = np.log(np.clip(p_raw_tr, 1e-6, 1-1e-6) / (1 - np.clip(p_raw_tr, 1e-6, 1-1e-6)))
    platt = LR(C=1e6, max_iter=500)
    platt.fit(logit_p.reshape(-1, 1), y_va)
    p_cal = platt.predict_proba(logit_p.reshape(-1, 1))[:, 1]

    acc_cal = dir_accuracy(y_va, p_cal)
    auc_cal = float(roc_auc_score(y_va, p_cal)) if len(np.unique(y_va)) > 1 else float("nan")
    print(f"  Calibrated: dir_acc={acc_cal:.4f}  AUC={auc_cal:.4f}")

    # Val backtest at chop threshold
    for thresh, label in [(0.51, "t=0.51"), (0.53, "t=0.53")]:
        pos   = (p_cal > thresh).astype(float)
        dr    = pos * val_rets - np.abs(np.diff(np.append(0, pos))) * TX_COST
        ar    = dr.mean() * ANNUAL
        av    = dr.std() * np.sqrt(ANNUAL)
        sh    = ar / av if av > 0 else 0.0
        cum   = np.cumprod(1 + dr)
        mdd   = float(((np.maximum.accumulate(cum) - cum) /
                       np.maximum.accumulate(cum)).max())
        n_sig = int(pos.sum())
        print(f"  {label}: Sharpe={sh:.3f}  ann_ret={ar*100:.1f}%  "
              f"MaxDD={mdd*100:.1f}%  n_signals={n_sig}")

    # Feature importances (top 10 by |coef|)
    inner  = m.named_steps["m"]
    coefs  = np.abs(inner.coef_.ravel())
    top_idx = np.argsort(coefs)[::-1][:10]
    top5   = "; ".join([f"{avail_f[i]}={coefs[i]:.3f}" for i in top_idx[:5]])
    print(f"  Top features: {top5}")
    print()

    # ── GBM chop ensemble ─────────────────────────────────────────────────────
    print("=" * 56)
    print(f"  GBM chop ensemble ({N_GBM_SEEDS} seeds, chop rows only)")
    print("=" * 56)
    gbm_chop_models, p_gbm = train_chop_gbm_ensemble(X_tr, y_tr, X_va)
    acc_gbm = dir_accuracy(y_va, p_gbm)
    auc_gbm = float(roc_auc_score(y_va, p_gbm)) if len(np.unique(y_va)) > 1 else float("nan")
    print(f"  GBM: dir_acc={acc_gbm:.4f}  AUC={auc_gbm:.4f}")

    # GBM backtest
    for thresh, label in [(0.51, "t=0.51"), (0.53, "t=0.53")]:
        pos   = (p_gbm > thresh).astype(float)
        dr    = pos * val_rets - np.abs(np.diff(np.append(0, pos))) * TX_COST
        ar    = dr.mean() * ANNUAL
        av    = dr.std() * np.sqrt(ANNUAL)
        sh    = ar / av if av > 0 else 0.0
        n_sig = int(pos.sum())
        print(f"  GBM {label}: Sharpe={sh:.3f}  n_signals={n_sig}")

    # 50/50 blend: calibrated logistic + GBM chop
    p_blend = 0.5 * p_cal + 0.5 * p_gbm
    acc_blend = dir_accuracy(y_va, p_blend)
    auc_blend = float(roc_auc_score(y_va, p_blend)) if len(np.unique(y_va)) > 1 else float("nan")
    print()
    print(f"  Blend (50/50 log_cal + gbm_chop): dir_acc={acc_blend:.4f}  AUC={auc_blend:.4f}")
    for thresh, label in [(0.51, "t=0.51"), (0.53, "t=0.53")]:
        pos   = (p_blend > thresh).astype(float)
        dr    = pos * val_rets - np.abs(np.diff(np.append(0, pos))) * TX_COST
        ar    = dr.mean() * ANNUAL
        av    = dr.std() * np.sqrt(ANNUAL)
        sh    = ar / av if av > 0 else 0.0
        cum   = np.cumprod(1 + dr)
        mdd   = float(((np.maximum.accumulate(cum) - cum) /
                       np.maximum.accumulate(cum)).max())
        n_sig = int(pos.sum())
        print(f"  Blend {label}: Sharpe={sh:.3f}  ann_ret={ar*100:.1f}%  "
              f"MaxDD={mdd*100:.1f}%  n_signals={n_sig}")
    print()

    # ── Compare vs existing regime_chop logistic ─────────────────────────────
    import glob
    chop_pats = sorted(glob.glob(os.path.join(MODEL_DIR, "spy_logistic_regime_chop_calibrated_*.pkl")))
    if not chop_pats:
        chop_pats = sorted(glob.glob(os.path.join(MODEL_DIR, "spy_logistic_regime_chop_*.pkl")))
    if chop_pats:
        with open(chop_pats[-1], "rb") as fh:
            old_bundle = pickle.load(fh)
        old_model  = old_bundle["model"]
        old_feats  = old_bundle.get("features", [])
        old_flip   = old_bundle.get("flip_probs", False)
        avail_old  = [f for f in old_feats if f in va_df.columns]
        X_va_old   = va_df[avail_old].copy()
        for col in X_va_old.columns:
            if X_va_old[col].isna().any():
                med = df[col].median() if col in df.columns else 0.0
                X_va_old[col] = X_va_old[col].fillna(med if pd.notna(med) else 0.0)
        try:
            p_old = old_model.predict_proba(X_va_old.values)[:, 1]
            if old_flip:
                p_old = 1.0 - p_old
            acc_old = dir_accuracy(y_va, p_old)
            auc_old = float(roc_auc_score(y_va, p_old)) if len(np.unique(y_va)) > 1 else float("nan")
            print(f"  Existing regime_chop model:  dir_acc={acc_old:.4f}  AUC={auc_old:.4f}")
            print(f"  New dedicated chop model:    dir_acc={acc_cal:.4f}  AUC={auc_cal:.4f}")
            delta_acc = acc_cal - acc_old
            delta_auc = auc_cal - auc_old
            print(f"  Delta: acc={delta_acc:+.4f}  auc={delta_auc:+.4f}")
        except Exception as e:
            print(f"  [warn] could not run old chop model: {e}")
    print()

    # ── Save ──────────────────────────────────────────────────────────────────
    bundle = {
        "model":            m_base,           # base logistic pipeline (StandardScaler + LR)
        "platt":            platt,            # Platt calibration: fit logit(p) -> calibrated prob
        "gbm_chop_models":  gbm_chop_models,  # 15-seed GBM chop ensemble
        "features":         avail_f,
        "trained_on":       TODAY,
        "regime":           "chop",
        "model_type":       "logistic_chop_dedicated",
        "n_train":          len(tr_df),
        "n_val":            len(va_df),
        "val_dir_acc":      round(acc_cal, 4),
        "val_auc":          round(auc_cal, 4),
        "val_blend_acc":    round(acc_blend, 4),
        "val_blend_auc":    round(auc_blend, 4),
        "wfcv":             wfcv,
        "notes":            args.notes,
        "flip_probs":       False,
        "drop_from_blend":  False,
    }
    out_path = os.path.join(MODEL_DIR, f"spy_logistic_chop_dedicated_{TODAY}.pkl")
    with open(out_path, "wb") as fh:
        pickle.dump(bundle, fh)
    print(f"  saved -> {out_path}")

    # ── Log to experiment CSV ──────────────────────────────────────────────────
    log_row = {
        "model_id":          f"spy_logistic_chop_dedicated_{TODAY}",
        "target":            "next_dir_1d",
        "model_type":        "logistic_chop_dedicated",
        "train_date":        TODAY,
        "train_rows":        len(tr_df),
        "val_rows":          len(va_df),
        "val_window_days":   VAL_DAYS,
        "val_dir_acc":       round(acc_cal, 4),
        "val_auc":           round(auc_cal, 4),
        "val_spearman":      round(sp_raw, 4),
        "wfcv_dir_acc_mean": wfcv["wfcv_dir_acc_mean"] if wfcv else None,
        "wfcv_dir_acc_std":  wfcv["wfcv_dir_acc_std"]  if wfcv else None,
        "wfcv_auc_mean":     wfcv["wfcv_auc_mean"]     if wfcv else None,
        "notes":             args.notes,
        "top5_features":     top5,
    }
    log_df = pd.read_csv(LOG_PATH) if os.path.exists(LOG_PATH) else pd.DataFrame()
    log_df = pd.concat([log_df, pd.DataFrame([log_row])], ignore_index=True)
    log_df.to_csv(LOG_PATH, index=False)
    print(f"  logged -> {LOG_PATH}")
    print()
    print("[trainChopModel] done.")


if __name__ == "__main__":
    main()
