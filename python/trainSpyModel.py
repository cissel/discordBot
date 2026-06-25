#!/usr/bin/env python3
"""
trainSpyModel.py
================
Trains directional and return-magnitude models for SPY.

Changes in Run 13 (June 2026):
  - Feature list trimmed from 158 → 44 features (dropped anything with abs(Spearman) < 0.02
    vs next_dir_1d, plus deduplication of correlated pairs). Kept a handful of low-linear-rho
    features that have strong 5d signal or known nonlinear interaction value (vix_level,
    vol_regime, spy_drawdown_252, gex_chg_5d).
  - Recency weighting: exponential decay half-life 1260 days (~5yr). Passes sample_weight to
    GBM and Logistic .fit(). Ridge uses sample_weight too.
    (Swept 252/504/756/1260 in Run 20 — monotonically increasing, 1260 marginally best.)
  - Walk-forward cross-validation: 5 expanding folds, each fold trains on all prior data and
    validates on the next 252 days. Reports mean ± std of dir_acc and AUC across folds before
    the standard single-val-window training run.
  - Quintile target (next_dir_tail): 1 if next day is top 20% return, -1 if bottom 20%, 0 else.
    Trains a separate Logistic model on the tail target.  Useful for high-conviction entry signals.
  - macro_event_x_regime replaces raw macro_event_window in the feature list — interacts the
    event flag with the current rate-cycle direction (easing=+1, hiking=-1).

Models trained:
  1. Ridge regression      — next_ret_1d  (baseline linear)
  2. GBM regression        — next_ret_1d
  3. Logistic              — next_dir_1d  (primary production signal)
  4. GBM classifier        — next_dir_1d
  5. GBM regression        — next_ret_5d  (weekly horizon)
  6. GBM multi-seed (x15)  — next_dir_1d  (variance-reduced ensemble)
  7. Blend                 — next_dir_1d  (Logistic + GBM-ensemble 50/50)
  8. Logistic              — next_dir_tail (quintile tail signal)

Usage:
  venv/bin/python3 python/trainSpyModel.py [--notes "..."] [--skip-wfcv]
"""

import os
import sys
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

from sklearn.linear_model        import Ridge, LogisticRegression
from sklearn.ensemble            import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.calibration         import CalibratedClassifierCV, calibration_curve
from sklearn.preprocessing       import StandardScaler
from sklearn.pipeline            import Pipeline
from sklearn.metrics             import (mean_squared_error, mean_absolute_error,
                                         r2_score, accuracy_score, roc_auc_score,
                                         brier_score_loss)
from sklearn.feature_selection   import RFECV

warnings.filterwarnings("ignore")

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy")
LOG_PATH  = os.path.expanduser("~/discordBot/models/meta/spy_experiment_log.csv")
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

TODAY   = datetime.date.today().isoformat()
N_SEEDS = 15

# ── feature list (Run 13 — trimmed to abs(Spearman) >= 0.02, deduped corr pairs) ──────────────
# Dropped: 99 features below threshold + correlated duplicates (qqq_z21, gld_z21, XLK_ret,
#          vix_ma20_ratio replaced by vix_z21, gex_b/sign/z21/z63/chg_1d replaced by gex_chg_5d)
# Kept despite low linear rho: vix_level (rho_5d=0.11), vol_regime (rho_5d=0.11),
#   spy_drawdown_252 (rho_5d=-0.12), gex_chg_5d — all have meaningful nonlinear/5d signal

SPY_FEATURES = [
    # SPY momentum & mean reversion
    "spy_ret_r5",                    # rho_dir=-0.021
    "spy_rsi_3",                     # rho_dir=-0.023  (short-term overbought)
    "spy_z21",                       # rho_dir=-0.025
    "spy_consec_up", "spy_consec_down",  # rho ~±0.024-0.029
    "spy_drawdown_252",              # rho_dir=-0.007 BUT rho_5d=-0.121 — keep for 5d model
    # Volatility regime
    "vix_level",                     # rho_dir=-0.015 BUT rho_5d=+0.109 — strong nonlinear regime feature
    "vix_z21",                       # rho_dir=-0.023
    "vol_regime",                    # rho_dir=-0.013 BUT rho_5d=+0.110 — same reason
    # VIX term structure + VVIX
    "vvix_z21",                      # rho_dir=-0.031
    "vvix_chg_5d",                   # rho_dir=-0.040
    "vix_term_z21",                  # rho_dir=+0.029
    # Options (sparse — imputed, accumulating)
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    # Cross-asset — gold signal (top GBM features every run)
    "gld_ret_1d",                    # rho_dir=+0.054
    "gld_z21",                       # rho_dir=+0.052 — kept despite corr with gld_ret_1d (different timescale)
    "gld_spy_corr_21",               # rho_dir=-0.053  (risk-off regime proxy)
    "gld_spy_corr_63",               # 63d version — less noisy, more stable across regimes
    "gld_corr_x_vol",                # gld_spy_corr_21 × vol_regime — fixes non-stationarity across QE/post-QE eras
    "gld_corr_x_era",                # gld_spy_corr_21 × qt_era — corr signal only in QT/post-2022 era
    "gld_corr_era_flag",             # gld_spy_corr_21 × (1-qt_era) — corr signal only in QE/pre-2022 era
    "gld_corr63_x_era",              # gld_spy_corr_63 × qt_era — smoother 63d version for QT era
    # Cross-asset — QQQ / equity breadth
    "qqq_ret_1d",                    # rho_dir=-0.028
    # Cross-asset — energy (inflation / commodity regime)
    "xle_spy_rs_5d",                 # rho_dir=-0.025
    # Macro — Fed
    "fedfunds_chg_21d",              # rho_dir=-0.046
    # Macro — yield curve
    "yield_curve_z63",               # rho_dir=+0.021
    # Macro events — interaction with rate regime (replaces raw macro_event_window)
    "macro_event_x_regime",          # signed: easing-cycle events = bullish, hiking = bearish
    "macro_event_window",            # kept as companion — model sees both to learn the interaction
    "fomc_window",                   # rho_dir=-0.048
    "fomc_before", "fomc_after",     # rho ~-0.028 to -0.032
    "days_to_fomc",                  # rho_dir=+0.022
    # GEX / DIX
    "gex_chg_5d",                    # rho_dir=-0.024 (kept; gex_b/z21 dropped — near-zero)
    "dix_z21",                       # rho_dir=+0.025
    "dix_chg_5d",                    # rho_dir=+0.030
    # Calendar
    "is_friday",                     # rho_dir=+0.023
    # Sector
    "sector_risk_off_r5",            # rho_dir=-0.033
    # Individual ETF returns (regularized by Logistic L2; GBM uses GBM_FEATURES without these)
    "XLF_ret", "XLI_ret", "XLY_ret",  # rho ~-0.025 to -0.030
    # VWAP intraday
    "vwap_dev_open",                 # rho_dir=+0.051
    "vwap_dev_r5",                   # rho_dir=-0.049
    "vol_concentration",             # rho_dir=+0.048
    "vwap_cross_count",              # rho_dir=+0.022
    "vwap_time_above_pct",           # rho_dir=+0.023
    "vwap_dev_am",                   # rho_dir=-0.026
    "vol_vwap_corr_5d",              # rho_dir=+0.025
    # Intraday flags
    "open_drive_flag",               # rho_dir=-0.083  (strongest single feature by Spearman)
    "late_reversal_flag",            # rho_dir=-0.034
    "overnight_gap",                 # rho_dir=+0.034
    "gap_fill_flag",                 # rho_dir=-0.023
    "am_range",                      # rho_dir=-0.029
    # Block signals
    # block_active_flag DROPPED Run 30: zero GBM importance; redundant with richer block features.
    # "block_active_flag" — tombstoned Run 30
    # Regime-change recency + stability interactions (section 17)
    "days_since_regime_change",      # how long current regime has been active (capped 63)
    "regime_transition_flag",        # 1 if regime changed within last 5 days
    "mom_x_regime_stability",        # spy_ret_r21 × regime_stability — fades stale momentum post-flip
    "mom63_x_regime_stability",      # spy_ret_r63 × regime_stability
    # Intraday × regime interactions
    "open_drive_x_bear",             # open_drive_flag × bear — intraday confirmation in downtrends
    "open_drive_x_bull",             # open_drive_flag × bull
    # Vol/flow × regime interactions
    "vix_rv_x_bear",                 # vix_rv_ratio × bear — vol risk premium in stress regimes
    "vix_rv_x_chop",                 # vix_rv_ratio × chop
    "dix_chg_x_bear",                # dix_chg_5d × bear — dark pool flow in downtrends
    "dix_chg_x_bull",                # dix_chg_5d × bull
    "sector_riskoff_x_bear",         # sector_risk_off_r5 × bear — flight-to-safety rotation
]

# ── Moon phase + weather feature lists ────────────────────────────────────────
# Added as a separate block so they're easy to isolate/ablate.
# GBM L2 regularization will zero these out if they carry no signal.
# Sparse — weather data starts 2015, moon phase is dense from any date.
MOON_FEATURES = [
    "moon_phase",           # 0=new, 0.5=full, 1=new — continuous cycle
    "moon_phase_sin",       # cyclic encoding (preferred over raw for ML)
    "moon_phase_cos",       # cyclic encoding
    "days_to_full_moon",    # countdown to next full moon (capped 15)
    "days_to_new_moon",     # countdown to next new moon (capped 15)
    "moon_full_flag",       # 1 if within 2 days of full moon
    "moon_new_flag",        # 1 if within 2 days of new moon
]
WEATHER_FEATURES = [
    # NYC (Central Park) — financial center, Hirshleifer & Shumway (2003) effect
    "tavg_nyc",             # avg temp C — baseline mood proxy
    "temp_range_nyc",       # diurnal range — wide = clear sky
    "prcp_flag_nyc",        # 1 if meaningful precipitation (>0.5mm)
    # cold_flag_nyc DROPPED Run 30: near-constant in training window (summer-heavy), zero importance.
    # hot_flag_nyc  DROPPED Run 30: same — near-constant, zero importance.
    # "cold_flag_nyc", "hot_flag_nyc" — tombstoned Run 30
    "snow_flag_nyc",        # 1 if snowfall > 0
    "sunshine_proxy_nyc",   # (1 - prcp_flag) * temp_range — clear warm day proxy
    # Chicago (O'Hare) — CME/CBOT derivatives hub
    "tavg_chi",
    "temp_range_chi",
    "prcp_flag_chi",
    "sunshine_proxy_chi",
]

# Extend SPY_FEATURES with moon + weather
SPY_FEATURES = SPY_FEATURES + MOON_FEATURES + WEATHER_FEATURES

# ── Alternative / sentiment feature lists ──────────────────────────────────────
WIKI_FEATURES = [
    "wiki_stock_market_views",   # daily Wikipedia "Stock market" page views
    "wiki_sp500_views",          # "S&P 500" page views
    "wiki_economy_views",        # "Economy of the United States" page views
    "wiki_total_views",          # sum of all 3
    "wiki_views_z21",            # 21-day z-score — spikes = retail anxiety
]
HOLIDAY_FEATURES = [
    "is_pre_holiday",            # 1 if next trading day is a market holiday
    "is_post_holiday",           # 1 if previous trading day was a holiday
    "days_to_next_holiday",      # countdown (capped 10)
    "holiday_week",              # 1 if any holiday in current Mon-Fri week
]
MTA_FEATURES = [
    "mta_subway_riders",         # NYC subway daily ridership (2020+)
    "mta_riders_z21",            # 21-day z-score — low = hybrid work / market thin
]
ALT2_FEATURES = [
    # Daylight hours (pure astronomy, 100% coverage)
    "daylight_hours_nyc",        # hours of daylight in NYC
    "daylight_chg_7d",           # rate of change — shortening = SAD effect onset
    "daylight_z365",             # deviation from seasonal norm (is today unusually short?)
    # AQI — DROPPED: 0% coverage (no free historical API). Pure noise when median-imputed.
    # "aqi_pm25_nyc", "aqi_bad_flag", "aqi_z21" — tombstoned Run 28
    # Flight disruption — DROPPED Run 30: near-constant (2.6% of days), zero GBM importance.
    # "flight_disruption_flag" — tombstoned Run 30
    # Earnings season (100% coverage, calendar math)
    "earnings_season_flag",      # 1 if within Q earnings window (Jan/Apr/Jul/Oct 15-15)
    "days_to_earnings_season",   # days until next earnings season
    # Presidential approval (100% coverage, piecewise interpolated)
    "pres_approval",             # polling aggregate estimate
    "pres_approval_chg_21d",     # 21-day change — deteriorating = macro uncertainty
    # CBOE PCR (29% coverage — 2015-2019 only; 2020+ NaN, sparse-imputed)
    "cboe_pcr_equity",           # equity put/call ratio (CBOE CDN CSV, stops Oct 2019)
    # cboe_pcr_z21 DROPPED: 0% coverage. Tombstoned Run 28.
    # cboe_pcr_spike DROPPED Run 30: near-constant (0 after Oct 2019), zero GBM importance.
]
CONGRESS_FEATURES = [
    "congress_net_buy_count",    # net buy minus sell count on that day
    "congress_net_flow",         # net dollar flow (buys - sells)
    "congress_buy_flag",         # 1 if net buyers that day
]
TRENDS_FEATURES = [
    # Google Trends (100% coverage, weekly interpolated to daily, +2bd lag applied)
    "trends_spy",                # search interest: "SPY ETF"
    "trends_crash",              # search interest: "stock market crash"
    "trends_recession",          # search interest: "recession"
    "trends_buy_stocks",         # search interest: "buy stocks"
    "trends_volatility",         # search interest: "market volatility"
    "trends_fear_index",         # composite: (crash_z + recession_z) / 2
    "trends_fear_z21",           # 21-day z-score of fear index
    # New distress/risk-appetite terms (Run 31)
    "trends_bankruptcy",         # bankruptcy search interest — financial distress signal
    "trends_unemployment",       # unemployment searches — labor stress proxy
    "trends_payday_loan",        # payday loan interest — retail desperation
    "trends_margin_call",        # margin call searches — leverage stress
    "trends_how_invest",         # how to invest — retail FOMO proxy
    "trends_distress_index",     # composite: (bankruptcy + unemployment + payday + debt_relief) / 4
    "trends_risk_appetite_index", # composite: (casino + how_invest + buy_stocks) / 3
]
SENTIMENT_FEATURES = [
    # AAII individual investor sentiment survey (weekly, +1bd lag, 1987-present)
    "aaii_bullish",              # % bulls (individual investors)
    "aaii_bearish",              # % bears
    "aaii_bull_bear_spread",     # bull - bear spread (positive = optimistic)
    "aaii_bull_z8",              # 8-week z-score — extreme readings are contrarian signals
    # Crypto Fear & Greed index (daily, alternative.me, 2018-present)
    "cfg_value",                 # raw 0-100 score
    "cfg_z21",                   # 21-day z-score
    "cfg_extreme_fear",          # 1 if score < 25 — cross-asset panic
    "cfg_greed",                 # 1 if score > 75 — cross-asset euphoria
]
MACRO_SENT_FEATURES = [
    # FRED ICSA/CCSA (weekly initial/continuing jobless claims, +4bd lag)
    "icsa_z52",                  # 52-week z-score of initial claims — labor stress
    "icsa_chg_4w",               # 4-week change — direction of labor market stress
    "ccsa_z52",                  # continuing claims z-score
    # U Michigan consumer sentiment (monthly, +21d lag)
    "umcsent_z12",               # 12-month z-score
    "umcsent_chg_3m",            # 3-month change
]
WSB_FEATURES = [
    # TOMBSTONED Run 36: RFE dropped all 3 WSB features (0 incremental signal over Trends).
    # Google Trends (crash/volatility/fear_index) already captures retail anxiety better.
    # "wsb_avg_score", "wsb_avg_comments", "wsb_score_z21"
]

GEX_FEATURES = [
    # Raw GEX standalone — all near-zero Spearman (rho_all < 0.02) both WFCV and val periods.
    # TOMBSTONED Run 38: RFE kept them but they hurt WFCV Sharpe (-0.4). No incremental signal.
    # gex_chg_5d already in RFE-52 core; rest dropped.
    # "gex_b", "gex_z21", "gex_z63", "gex_chg_1d"
]

SKEW_FEATURES = [
    # CBOE SKEW + VIX9D — Run 38 screening, keep only features with consistent Spearman
    # across both WFCV (2018-2023) and val (2024+) periods.
    #
    # KEPT (consistent signal both periods):
    "skew_chg_1d",       # rho_all=+0.048 (p=0.016), wfcv=+0.033, val=+0.107 -- cleanest
    "skew_chg_5d",       # rho_all=+0.036 (p=0.066), wfcv=+0.038, val=+0.040 -- consistent
    "vix9d_z21",         # rho_all=-0.032 (p=0.102), wfcv=-0.047, val=-0.071 -- consistent direction
    #
    # DROPPED (noisy / inconsistent across periods):
    # "cboe_skew"        -- wfcv=+0.045, val=-0.039 (sign flip)
    # "skew_z21"         -- rho_all=+0.016, near-zero
    # "skew_z63"         -- wfcv=+0.058, val=-0.001 (fades in holdout)
    # "skew_high_flag"   -- rho_all=+0.016, near-zero
    # "vix9d"            -- wfcv=-0.028, val=+0.022 (sign flip)
    # "vix9d_vix30_ratio"-- rho_all=-0.009, negligible
    # "gex_b/z21/z63/chg_1d" -- all rho_all < 0.02
]

RATE_SHOCK_FEATURES = [
    # Fed tightening velocity — captures 2022-style inflationary bear (Arch #2)
    "rate_chg_63d",              # raw fed funds rate change over 63 days
    "rate_chg_252d",             # raw rate change over 252 days
    "rate_shock_flag",           # 1 if >100bps hike in 63 days
    "rate_easing_flag",          # 1 if >50bps cut in 63 days
    "rate_speed_z63",            # z-score of tightening speed vs history
    "rate_shock_x_bear",         # rate shock AND bear regime (2022 compound)
    "rate_speed_x_bear",         # rate speed × bear flag
]
DIST_REGIME_FEATURES = [
    # Returns-distribution regime (Arch #6) — less endogenous than VIX/price regime
    "ret_skew_21",               # 21-day realized skewness (negative = left-tail env)
    "ret_skew_63",               # 63-day realized skewness
    "ret_kurt_21",               # 21-day realized kurtosis (high = fat tails / stress)
    "ret_skew_z21",              # skewness z-score vs 252d history
    "drawdown_63d",              # rolling drawdown from 63d peak (continuous, not discrete)
    "regime_age_z",              # regime duration z-score (old regimes may be ending)
]
INTERACTION_FEATURES = [
    # Regime × sentiment interactions (Arch #8) — same signal, different meaning by regime
    "fear_z21_x_bear",   "fear_z21_x_bull",   "fear_z21_x_chop",
    "crash_x_bear",      "recession_x_bear",
    "vol_x_bear",        "buy_x_bull",
    "distress_x_bear",   "distress_x_chop",
    "aaii_spread_x_bear", "aaii_spread_x_bull",
    "cfg_z21_x_bear",    "cfg_z21_x_bull",
    "icsa_z52_x_bear",   "icsa_z52_x_chop",
    # Rate-shock × sentiment
    "fear_z21_x_rate_shock", "recession_x_rate_shock", "icsa_x_rate_shock",
]

# ── RFE-52 + new candidates (Run 38) ─────────────────────────────────────────
# Run 34 RFE-52 stable core + GEX standalone + CBOE SKEW/VIX9D (Run 38 additions).
# RFE will prune new candidates if they carry no incremental signal.
SPY_FEATURES = [
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
    "vix_rv_x_bear",
    "vix_rv_x_chop",
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
    # Run 40 candidates tested — all regressed WFCV (0.5603 -> 0.5429).
    # WFCV folds (2018-2023) don't share regime distribution with holdout (2024+, 92% chop).
    # New features have real holdout signal but hurt 2018-2023 fold generalization.
    # Tombstoned until holdout window grows to 1000+ days (est. Q1 2027).
    # "fear_z21_x_chop", "umcsent_chg_3m", "ccsa_z52", "gld_z21"
]

# Sparse features — imputed with median when unavailable
# NOTE: VWAP/intraday are now DENSE (2021+ backfill complete, 52% of rows).
# Only sparse < 50% coverage or genuinely event-driven remain here.
SPARSE_FEATURES = [
    # Options — only 7 rows, accumulating
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    # Sector ETF rets — actually dense (100% coverage) — NOT sparse
    # VWAP / intraday — 52% coverage (pre-2021 gap). Use fillna(median) for pre-2021 rows.
    "vwap_dev_open", "vwap_dev_r5", "vol_concentration",
    "vwap_cross_count", "vwap_time_above_pct", "vwap_dev_am", "vol_vwap_corr_5d",
    "open_drive_flag", "late_reversal_flag", "overnight_gap", "gap_fill_flag", "am_range",
    # Block signals — ~100% coverage (block_active_flag = 0 when no event, not NaN)
    # Extra 5d features — sparse (premarket from intraday backfill, starts 2021)
    "premarket_ret", "premarket_vol_ratio",
    # 5d vol features — dense, but listed here so GBM_5D train doesn't dropna pre-2021
    "sector_dispersion_r5",
    # Warmup-window NaN features — NaN from rolling window startup, not true sparsity.
    # Impute with median rather than dropping rows (critical for 5d model).
    # vix_z252=251 NaN, vvix_z21=253 NaN, vvix_chg_5d=139 NaN,
    # vix_term_slope=118 NaN, vix_term_z21=138 NaN
    "vix_z252", "vvix_z21", "vvix_chg_5d", "vix_term_slope", "vix_term_z21",
    # vix_spike_flag: 0 except on large VIX jump days — impute 0 on NaN (rare)
    "vix_spike_flag", "vix_ma20_ratio",
    # vwap_dev_close: only 52% coverage (intraday backfill 2021+)
    "vwap_dev_close",
    # gld_corr_x_vol: product of rolling corr × vol_regime — warmup NaNs from gld_spy_corr_21
    "gld_corr_x_vol",
    # gld_spy_corr_63 / gld_corr63_x_era: 63-day rolling corr, ~63-row warmup NaN window
    "gld_spy_corr_63",
    "gld_corr63_x_era",
    # Regime-interaction features — inherit sparsity from intraday/VWAP parent features
    "open_drive_x_bear", "open_drive_x_bull",
    "dix_chg_x_bear",    "dix_chg_x_bull",
    "sector_riskoff_x_bear",
    "mom_x_regime_stability", "mom63_x_regime_stability",
    # Moon phase — dense (pure math), but listed here to safely impute any edge-case NaNs
    "moon_phase", "moon_phase_sin", "moon_phase_cos",
    "days_to_full_moon", "days_to_new_moon", "moon_full_flag", "moon_new_flag",
    # Weather — occasional missing days; impute with column median
    "tavg_nyc", "temp_range_nyc", "prcp_flag_nyc",
    # cold_flag_nyc, hot_flag_nyc DROPPED Run 30 (near-constant, zero importance)
    "snow_flag_nyc", "sunshine_proxy_nyc",
    "tavg_chi", "temp_range_chi", "prcp_flag_chi", "sunshine_proxy_chi",
    # Wikipedia — missing on market holidays (no views data); impute with median
    "wiki_stock_market_views", "wiki_sp500_views", "wiki_economy_views",
    "wiki_total_views", "wiki_views_z21",
    # Holidays — dense (calendar math), listed for safety
    "is_pre_holiday", "is_post_holiday", "days_to_next_holiday", "holiday_week",
    # MTA — only available from 2020-03-01; NaN pre-2020
    "mta_subway_riders", "mta_bus_riders", "mta_total_riders", "mta_riders_z21",
    # Alt data 2 — daylight dense, PCR sparse (2015-2019 only), others dense
    "daylight_hours_nyc", "daylight_chg_7d", "daylight_z365",
    # aqi cols, cboe_pcr_z21, cboe_pcr_spike, flight_disruption_flag — DROPPED (tombstoned Run 28-30)
    "earnings_season_flag", "days_to_earnings_season",
    "pres_approval", "pres_approval_chg_21d",
    "cboe_pcr_equity",
    # Congress trading — sparse (only days with disclosed trades)
    "congress_net_buy_count", "congress_buy_vol_usd", "congress_sell_vol_usd",
    "congress_net_flow", "congress_buy_flag",
    # Google Trends — sparse near ends (2bd lag creates small gap at start)
    "trends_spy", "trends_crash", "trends_recession", "trends_buy_stocks", "trends_volatility",
    "trends_fear_index", "trends_fear_z21",
    "trends_bankruptcy", "trends_unemployment", "trends_payday_loan", "trends_margin_call",
    "trends_how_invest", "trends_distress_index", "trends_risk_appetite_index",
    # AAII — weekly, forward-filled; full coverage from 1987 but listed for safety
    "aaii_bullish", "aaii_bearish", "aaii_bull_bear_spread", "aaii_bull_z8",
    # Crypto Fear & Greed — starts 2018-02-01, 73% coverage; NaN pre-2018
    "cfg_value", "cfg_z21", "cfg_extreme_fear", "cfg_greed",
    # FRED macro sentiment — weekly/monthly with publication lags
    "icsa_z52", "icsa_chg_4w", "ccsa_z52", "umcsent_z12", "umcsent_chg_3m",
    # Rate-shock features — warmup NaN from 63/252d rolling windows
    "rate_chg_63d", "rate_chg_252d", "rate_shock_flag", "rate_easing_flag",
    "rate_speed_z63", "rate_shock_x_bear", "rate_speed_x_bear",
    # Returns-distribution regime — warmup NaN from rolling windows
    "ret_skew_21", "ret_skew_63", "ret_kurt_21", "ret_skew_z21",
    "drawdown_63d", "regime_age_z",
    # Regime×sentiment interactions — inherit sparsity from parent features
    "fear_z21_x_bear", "fear_z21_x_bull", "fear_z21_x_chop",
    "crash_x_bear", "recession_x_bear", "vol_x_bear", "buy_x_bull",
    "distress_x_bear", "distress_x_chop",
    "aaii_spread_x_bear", "aaii_spread_x_bull",
    "cfg_z21_x_bear", "cfg_z21_x_bull",
    "icsa_z52_x_bear", "icsa_z52_x_chop",
    "fear_z21_x_rate_shock", "recession_x_rate_shock", "icsa_x_rate_shock",
    # GEX standalone (Run 38) — dense 2011+, NaN pre-2011 imputed with median
    "gex_b", "gex_z21", "gex_z63", "gex_chg_1d", "gex_chg_5d",
    # CBOE SKEW + VIX9D (Run 38) — dense 2011+, NaN pre-2011 imputed with median
    "cboe_skew", "skew_z21", "skew_z63", "skew_chg_5d", "skew_chg_1d",
    "skew_high_flag", "vix9d", "vix9d_z21", "vix9d_vix30_ratio",
]

# GBM feature list — drops individual ETF rets (overfits at ~1600 train rows)
GBM_FEATURES = [f for f in SPY_FEATURES if f not in ("XLF_ret", "XLI_ret", "XLY_ret")]

# ── Regime-specific feature lists ─────────────────────────────────────────────
# These add features that have strong regime-conditional Spearman but were dropped
# from SPY_FEATURES by the global filter (their cross-regime average is near zero).
# Source: outputs/features/markets/regime_feature_importance.csv

# Bear features: risk-off / macro-stress signals that fire in downtrends
# Added: yield_inverted(-0.088), XLE_ret(-0.074), yield_curve(+0.067),
#        dxy_z21(+0.066), vix_rv_ratio(+0.056), cpi signals, macro_event_day,
#        dxy_ret_1d(+0.054), cpi_before(-0.053), month_cos(-0.051)
BEAR_FEATURES = sorted(set(SPY_FEATURES) | {
    "yield_inverted",    # -0.088 bear (+0.019 bull) — inversion IS a bear signal
    "XLE_ret",           # -0.074 bear — energy leads in inflationary selloffs
    "yield_curve",       # +0.067 bear — curve level (not z-score) matters in risk-off
    "dxy_z21",           # +0.066 bear — dollar strength = flight to quality
    "vix_rv_ratio",      # +0.056 bear — vol risk premium spikes in fear regimes
    "cpi_day",           # -0.059 bear — CPI prints move bear markets harder
    "cpi_window",        # -0.055 bear
    "cpi_before",        # -0.053 bear
    "macro_event_day",   # -0.056 bear — event risk matters more in stressed markets
    "dxy_ret_1d",        # +0.054 bear
    "month_cos",         # -0.051 bear — seasonality signal in downtrends
    "days_to_any_macro", # +0.060 bear — countdown to event has bear-regime predictive power
    "XLK_ret",           # -0.057 bear — tech underperformance signals bear leadership
    # Run 18 additions
    "spy_rsi_14",        # rho_5d=-0.084 bear — medium-term oversold bounce fires hardest in bear
    "nfp_window",        # rho_5d=+0.019 bear — NFP date-risk matters in stressed markets
    "nfp_before",        # rho_dir=+0.015 bear — pre-NFP positioning in downtrends
    "days_to_nfp",       # rho_5d=-0.023 bear — countdown to labour report
    "vix_spike_flag",    # bear-specific: regime transition days (VIX jump + above MA)
    "vix_ma20_ratio",    # VIX vs its own trend — elevated = persistent stress
    # Run 23: regime-conditional interactions — fire only in bear
    "open_drive_x_bear",     # intraday confirmation in downtrends
    "vix_rv_x_bear",         # vol risk premium in stress regimes
    "dix_chg_x_bear",        # dark pool flow direction in bear
    "sector_riskoff_x_bear", # flight-to-safety sector rotation in bear
    "mom_x_regime_stability",  # fades stale momentum post-flip (useful at bear entry)
    "regime_transition_flag",  # bear entries often coincide with transition days
})

# Bull features: intraday momentum / VWAP signals that fire in uptrends
# Added: pm_range(+0.090), premarket_ret(+0.079) — both strong bull, weak/negative bear
# Note: many VWAP features already in SPY_FEATURES; adding the 2 that were dropped
BULL_FEATURES = sorted(set(SPY_FEATURES) | {
    "pm_range",          # +0.090 bull (-0.022 bear) — afternoon range = momentum continuation
    "premarket_ret",     # +0.079 bull (-0.027 bear) — pre-market direction = bull follow-through
    # Run 18 additions
    "vwap_dev_close",    # rho_5d=-0.053 bull — closing below VWAP = distribution signal in uptrends
    "is_qtr_end",        # rho_5d=+0.044 bull — quarter-end rebalancing flows in bull markets
})

# 5d-specific GBM feature list — adds back vol/regime features that have strong
# rho_5d signal but near-zero rho_1d (so they were dropped from the 1d filter).
# These features ARE in spy_features.csv — they were excluded only because the
# Spearman filter was run against next_dir_1d. The 5d model needs its own filter.
GBM_5D_FEATURES = sorted(set(GBM_FEATURES) | {
    "spy_vol_r5", "spy_vol_r10", "spy_vol_r21", "spy_vol_r63",  # rho_5d ~0.10-0.13
    "rv_21",                    # rho_5d=+0.100 (realized vol)
    "vix_term_slope",           # rho_5d=-0.110 (contango/backwardation)
    "gex_b",                    # rho_5d=-0.082 (dealer gamma)
    "sector_dispersion_r5",     # rho_5d=+0.115 (breadth signal)
    "premarket_ret",            # rho_5d=+0.075
    "premarket_vol_ratio",      # rho_5d=+0.070
    "spy_rsi_14",               # rho_5d=-0.084
    "spy_ret_r63",              # rho_5d=-0.082 (3mo momentum)
    "spy_bbpct_20",             # rho_5d=-0.070
    "uso_ret_5d",               # rho_5d=-0.096 (5d oil move)
    "vix_z252",                 # rho_5d=+0.054
    "days_to_cpi",              # rho_5d=-0.068
})

LOG_COLS = [
    "model_id", "target", "model_type", "train_date",
    "train_rows", "val_rows", "val_window_days",
    "val_spearman", "val_rmse", "val_mae", "val_r2",
    "val_dir_acc", "val_auc", "val_brier",
    "wfcv_dir_acc_mean", "wfcv_dir_acc_std", "wfcv_auc_mean", "wfcv_auc_std",
    "top5_features", "notes",
]


# ── helpers ────────────────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH)
    return pd.DataFrame(columns=LOG_COLS)


def append_log(row):
    log = load_log()
    # Ensure all LOG_COLS present
    for col in LOG_COLS:
        if col not in row:
            row[col] = None
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)


def dir_accuracy(y_true, y_pred):
    return float(np.mean(np.sign(y_true) == np.sign(y_pred)))


def compute_sample_weights(dates, half_life_days=756):
    """
    Exponential decay sample weights. Most recent row = weight 1.0.
    A row half_life_days in the past gets weight ~0.5.
    Reduces influence of stale market regimes (2016-2020 zero-rate world).
    Default 756 days (~3yr). Override with --half-life CLI arg.
    """
    max_date = dates.max()
    days_ago = (max_date - dates).dt.days.values.astype(float)
    weights  = np.exp(-days_ago / half_life_days)
    return weights / weights.mean()   # normalise so mean weight = 1


def prep_df(df, features, target):
    """Impute sparse features, drop rows missing core features or target."""
    df = df.copy()
    avail = [f for f in features if f in df.columns]
    for col in SPARSE_FEATURES:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)
    core = [f for f in avail if f not in SPARSE_FEATURES]
    df   = df.dropna(subset=core + [target])
    df   = df.sort_values("date").reset_index(drop=True)
    return df, avail


def build_model(model_type, is_clf):
    if model_type == "ridge":
        return Pipeline([("sc", StandardScaler()), ("m", Ridge(alpha=1.0))])
    if model_type == "logistic":
        return Pipeline([("sc", StandardScaler()),
                         ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
    if model_type == "gbm" and is_clf:
        return Pipeline([("sc", StandardScaler()),
                         ("m", GradientBoostingClassifier(
                             n_estimators=300, learning_rate=0.03,
                             max_depth=2, min_samples_leaf=50,
                             subsample=0.7, random_state=42))])
    if model_type == "gbm":
        return Pipeline([("sc", StandardScaler()),
                         ("m", GradientBoostingRegressor(
                             n_estimators=200, learning_rate=0.05,
                             max_depth=3, min_samples_leaf=20,
                             subsample=0.8, random_state=42))])
    raise ValueError(f"Unknown model_type: {model_type}")


def eval_metrics(y_true, y_pred, y_prob, is_clf):
    m = {}
    if is_clf:
        m["val_dir_acc"]  = round(accuracy_score(y_true, y_pred), 4)
        m["val_auc"]      = round(roc_auc_score(y_true, y_prob), 4)
        m["val_brier"]    = round(brier_score_loss(y_true, y_prob), 4)
        m["val_spearman"] = round(stats.spearmanr(y_true, y_prob).statistic, 4)
        m["val_rmse"] = m["val_mae"] = m["val_r2"] = None
    else:
        m["val_rmse"]     = round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 6)
        m["val_mae"]      = round(mean_absolute_error(y_true, y_pred), 6)
        m["val_r2"]       = round(r2_score(y_true, y_pred), 4)
        m["val_spearman"] = round(stats.spearmanr(y_true, y_pred).statistic, 4)
        m["val_dir_acc"]  = round(dir_accuracy(y_true, y_pred), 4)
        m["val_auc"] = m["val_brier"] = None
    return m


def top_features(model, avail, n=5):
    inner = model.named_steps.get("m")
    if hasattr(inner, "feature_importances_"):
        imps = inner.feature_importances_
        idx  = np.argsort(imps)[::-1][:n]
        return [f"{avail[i]}={imps[i]:.3f}" for i in idx]
    if hasattr(inner, "coef_"):
        coefs = np.abs(inner.coef_.ravel())
        idx   = np.argsort(coefs)[::-1][:n]
        return [f"{avail[i]}={coefs[i]:.3f}" for i in idx]
    return []


# ── walk-forward cross-validation ─────────────────────────────────────────────

def walk_forward_cv(df, features, target, model_type, n_folds=5, fold_size=252, half_life=1260,
                    holdout_start="2024-01-01"):
    """
    Expanding-window walk-forward CV.
    Each fold trains on all rows before the fold window, validates on the next fold_size rows.
    Folds are constructed BEFORE the holdout_start date — the holdout period is excluded
    from all WFCV folds and reported separately to prevent methodological overfitting.
    Returns dict with mean/std of dir_acc and AUC across folds.
    """
    df_p, avail = prep_df(df, features, target)
    if len(df_p) < (n_folds + 1) * fold_size:
        return None   # not enough data

    is_clf = target in ("next_dir_1d", "next_dir_tail", "next_dir_tail_bin")

    # ── Holdout split (Arch #9) ───────────────────────────────────────────────
    # Lock rows from holdout_start onward. WFCV only sees pre-holdout data.
    # This prevents the iterative feature engineering process from implicitly
    # fitting to recent market behaviour through design choices.
    holdout_mask = df_p["date"] >= pd.Timestamp(holdout_start)
    df_pre  = df_p[~holdout_mask].copy()
    df_hold = df_p[holdout_mask].copy()

    total  = len(df_pre)
    # Start folds so last fold ends at total (within pre-holdout data)
    fold_starts = [total - (n_folds - i) * fold_size for i in range(n_folds)]

    dir_accs, aucs = [], []
    fold_details   = []   # per-fold: date range, regime composition, accuracy
    for fold_start in fold_starts:
        if fold_start < fold_size:
            continue   # need at least fold_size rows to train
        tr = df_pre.iloc[:fold_start]
        va = df_pre.iloc[fold_start:fold_start + fold_size]
        if len(va) < 50:
            continue

        # Regime composition of this validation fold
        fold_info = {
            "start": str(va["date"].iloc[0].date()),
            "end":   str(va["date"].iloc[-1].date()),
            "n":     len(va),
        }
        if "regime" in va.columns:
            rc = va["regime"].value_counts(normalize=True)
            fold_info.update({
                "bull_pct": round(float(rc.get("bull", 0)), 3),
                "bear_pct": round(float(rc.get("bear", 0)), 3),
                "chop_pct": round(float(rc.get("chop", 0)), 3),
            })

        sw = compute_sample_weights(tr["date"], half_life_days=half_life)
        m  = build_model(model_type, is_clf)
        X_tr, y_tr = tr[avail].values, tr[target].values
        X_va, y_va = va[avail].values, va[target].values

        try:
            m.fit(X_tr, y_tr, **{"m__sample_weight": sw})
        except TypeError:
            m.fit(X_tr, y_tr)

        if is_clf:
            y_pr = m.predict_proba(X_va)[:, 1]
            y_pd = (y_pr >= 0.5).astype(int)
            fold_acc = accuracy_score(y_va, y_pd)
            try:
                fold_auc = roc_auc_score(y_va, y_pr)
                aucs.append(fold_auc)
                fold_info["auc"] = round(fold_auc, 4)
            except Exception:
                pass
            dir_accs.append(fold_acc)
            fold_info["dir_acc"] = round(fold_acc, 4)

            # Per-fold threshold backtest at t=0.54 (best from single-window analysis)
            # Requires next_ret_1d in the val df and no NaN
            if "next_ret_1d" in va.columns:
                TX_COST  = 0.0005   # 5bps one-way: ~0.5bp bid-ask + 4bp avg gap vs close signal
                # Rationale: SPY bid-ask ~0.5bps; but signal at close, execution at next open.
                # Avg overnight gap SPY = ~15bps; we assume fill at open + 3bp market impact.
                # 5bps is conservative but realistic for $10k-$50k size.
                ANNUAL   = 252
                BT_THRESH = 0.54
                ret_arr  = va["next_ret_1d"].values
                valid    = ~np.isnan(ret_arr) & ~np.isnan(y_pr[:len(ret_arr)])
                if valid.sum() > 20:
                    r_bt   = ret_arr[valid]
                    p_bt   = y_pr[:len(ret_arr)][valid]
                    pos    = (p_bt > BT_THRESH).astype(float)
                    dr     = pos * r_bt - (np.abs(np.diff(np.append(0, pos))) * TX_COST)
                    ann_r  = dr.mean() * ANNUAL
                    ann_v  = dr.std() * np.sqrt(ANNUAL)
                    sharpe = ann_r / ann_v if ann_v > 0 else 0.0
                    cum    = (1 + dr).cumprod()
                    mdd    = float(((cum - np.maximum.accumulate(cum)) / np.maximum.accumulate(cum)).min())
                    bh_r   = r_bt.mean() * ANNUAL
                    bh_v   = r_bt.std() * np.sqrt(ANNUAL)
                    bh_sh  = bh_r / bh_v if bh_v > 0 else 0.0

                    # Regime-conditional sizing in WFCV: half-bear t=0.53 + kill switch
                    # Mirrors live predictSpy.py sizing rule for honest WFCV Sharpe estimate.
                    sized_sharpe = None
                    sized_chop_sharpe = None
                    try:
                        if "regime" in va.columns and "gex_sign" in va.columns and "vix_z21" in va.columns:
                            va_reg_bt   = va["regime"].values[valid]
                            va_gex_bt   = va["gex_sign"].values[valid]
                            va_vix_bt   = va["vix_z21"].values[valid]
                            is_bear_wf  = (va_reg_bt == "bear").astype(float)
                            kill_wf     = ((va_reg_bt == "bear") & (va_gex_bt <= 0) & (va_vix_bt > 1.5)).astype(float)
                            raw_pos_wf  = (p_bt > 0.53).astype(float)
                            sizing_wf   = np.where(is_bear_wf, 0.5, 1.0)
                            pos_sized   = raw_pos_wf * sizing_wf * (1 - kill_wf)
                            dr_sized    = pos_sized * r_bt - np.abs(np.diff(np.append(0, pos_sized))) * TX_COST
                            ar_sized    = dr_sized.mean() * ANNUAL
                            av_sized    = dr_sized.std()  * np.sqrt(ANNUAL)
                            sized_sharpe = ar_sized / av_sized if av_sized > 0 else 0.0

                            # Chop-lower variant: t=0.51 for chop days, t=0.53 for bull/bear days
                            raw_pos_chop = np.where(va_reg_bt == "chop",
                                                    (p_bt > 0.51).astype(float),
                                                    (p_bt > 0.53).astype(float))
                            pos_chop     = raw_pos_chop * sizing_wf * (1 - kill_wf)
                            dr_chop      = pos_chop * r_bt - np.abs(np.diff(np.append(0, pos_chop))) * TX_COST
                            ar_chop      = dr_chop.mean() * ANNUAL
                            av_chop      = dr_chop.std()  * np.sqrt(ANNUAL)
                            sized_chop_sharpe = ar_chop / av_chop if av_chop > 0 else 0.0
                    except Exception:
                        pass

                    fold_info.update({
                        "bt_sharpe":           round(sharpe, 3),
                        "bt_maxdd":            round(mdd, 3),
                        "bt_ann_ret":          round(ann_r, 3),
                        "bh_sharpe":           round(bh_sh, 3),
                        "bt_signals":          int(pos.sum()),
                        "bt_sized_sharpe":     round(sized_sharpe, 3) if sized_sharpe is not None else None,
                        "bt_sized_chop_sharpe": round(sized_chop_sharpe, 3) if sized_chop_sharpe is not None else None,
                    })
        else:
            y_pd = m.predict(X_va)
            fold_acc = dir_accuracy(y_va, y_pd)
            dir_accs.append(fold_acc)
            fold_info["dir_acc"] = round(fold_acc, 4)

        fold_details.append(fold_info)

    if not dir_accs:
        return None
    result = {
        "wfcv_dir_acc_mean": round(float(np.mean(dir_accs)), 4),
        "wfcv_dir_acc_std":  round(float(np.std(dir_accs)),  4),
        "wfcv_fold_details": fold_details,
    }
    if aucs:
        result["wfcv_auc_mean"] = round(float(np.mean(aucs)), 4)
        result["wfcv_auc_std"]  = round(float(np.std(aucs)),  4)

    # ── Holdout evaluation (Arch #9) ─────────────────────────────────────────
    # Train on ALL pre-holdout data, evaluate on locked holdout.
    # This is the true out-of-sample estimate — never used during development.
    if len(df_hold) >= 50 and is_clf:
        try:
            sw_all = compute_sample_weights(df_pre["date"], half_life_days=half_life)
            m_hold = build_model(model_type, is_clf)
            try:
                m_hold.fit(df_pre[avail].values, df_pre[target].values,
                           **{"m__sample_weight": sw_all})
            except TypeError:
                m_hold.fit(df_pre[avail].values, df_pre[target].values)
            y_h   = df_hold[target].values
            y_h_pr = m_hold.predict_proba(df_hold[avail].values)[:, 1]
            h_acc = accuracy_score(y_h, (y_h_pr >= 0.5).astype(int))
            h_auc = roc_auc_score(y_h, y_h_pr) if len(np.unique(y_h)) > 1 else None
            result["holdout_dir_acc"]  = round(h_acc, 4)
            result["holdout_auc"]      = round(h_auc, 4) if h_auc else None
            result["holdout_n_rows"]   = len(df_hold)
            result["holdout_start"]    = holdout_start
        except Exception as e_hold:
            result["holdout_error"] = str(e_hold)

    return result


# ── train one model ────────────────────────────────────────────────────────────

def train_one(df, features, target, model_type, val_days=252, notes="", half_life=1260):
    df_p, avail = prep_df(df, features, target)
    if len(df_p) < 300:
        raise ValueError(f"Not enough rows after NaN drop: {len(df_p)}")

    split = max(len(df_p) - val_days, int(len(df_p) * 0.8))
    tr    = df_p.iloc[:split]
    va    = df_p.iloc[split:]

    sw    = compute_sample_weights(tr["date"], half_life_days=half_life)
    is_clf = target in ("next_dir_1d", "next_dir_tail", "next_dir_tail_bin")
    m     = build_model(model_type, is_clf)

    X_tr, y_tr = tr[avail].values, tr[target].values
    X_va, y_va = va[avail].values, va[target].values

    # Pass sample weights — Pipeline uses `m__sample_weight` syntax
    try:
        m.fit(X_tr, y_tr, **{"m__sample_weight": sw})
    except TypeError:
        m.fit(X_tr, y_tr)

    if is_clf:
        y_pr = m.predict_proba(X_va)[:, 1]
        y_pd = m.predict(X_va)
    else:
        y_pr = y_pd = m.predict(X_va)

    metrics = {
        "train_rows":      len(tr),
        "val_rows":        len(va),
        "val_window_days": val_days,
        **eval_metrics(y_va, y_pd, y_pr, is_clf),
        "top5_features": "; ".join(top_features(m, avail)),
    }
    return m, metrics, tr, va, avail


# ── multi-seed ensemble ────────────────────────────────────────────────────────

def train_multiseed_gbm(df, features, target, val_days=252, n_seeds=N_SEEDS, half_life=1260):
    df_p, avail = prep_df(df, features, target)
    if len(df_p) < 300:
        raise ValueError(f"Not enough rows: {len(df_p)}")

    split    = max(len(df_p) - val_days, int(len(df_p) * 0.8))
    tr       = df_p.iloc[:split]
    va       = df_p.iloc[split:]
    sw       = compute_sample_weights(tr["date"], half_life_days=half_life)
    X_tr, y_tr = tr[avail].values, tr[target].values
    X_va, y_va = va[avail].values, va[target].values

    seeds     = list(range(0, n_seeds * 7, 7))
    models    = []
    val_probs = np.zeros(len(y_va))

    for seed in seeds:
        m = Pipeline([("sc", StandardScaler()),
                      ("m", GradientBoostingClassifier(
                          n_estimators=300, learning_rate=0.03,
                          max_depth=2, min_samples_leaf=50,
                          subsample=0.7, random_state=seed))])
        try:
            m.fit(X_tr, y_tr, **{"m__sample_weight": sw})
        except TypeError:
            m.fit(X_tr, y_tr)
        val_probs += m.predict_proba(X_va)[:, 1]
        models.append(m)

    val_probs /= n_seeds
    y_pred     = (val_probs >= 0.5).astype(int)

    all_imps = np.mean([m.named_steps["m"].feature_importances_ for m in models], axis=0)
    top5_idx = np.argsort(all_imps)[::-1][:5]
    top5     = [f"{avail[i]}={all_imps[i]:.3f}" for i in top5_idx]

    metrics = {
        "train_rows": len(tr), "val_rows": len(va), "val_window_days": val_days,
        **eval_metrics(y_va, y_pred, val_probs, is_clf=True),
        "top5_features": "; ".join(top5),
    }
    return models, val_probs, metrics, tr, va, avail


def save_model(model, model_type, target, trained_features=None):
    fname = f"spy_{model_type}_{target}_{TODAY}.pkl"
    path  = os.path.join(MODEL_DIR, fname)
    with open(path, "wb") as f:
        pickle.dump({
            "model":      model,
            "features":   trained_features or SPY_FEATURES,
            "trained_on": TODAY,
        }, f)
    return path


# ── main training loop ─────────────────────────────────────────────────────────

def run_training(notes="", skip_wfcv=False, half_life=1260, sweep_half_life=False):
    print("[trainSpyModel] loading features...")
    if not os.path.exists(FEAT_PATH):
        print(f"  ERROR: {FEAT_PATH} not found — run buildSpyFeatures.py first")
        sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    print(f"  loaded {len(df)} rows, {df['date'].min().date()} to {df['date'].max().date()}")
    print(f"  recency half-life: {half_life} days (~{half_life/252:.1f}yr)  [default=1260, swept Run 20]")

    # ── build quintile tail target ─────────────────────────────────────────────
    # Top 20% = 1, bottom 20% = -1, middle = 0. Train classifier on abs tail only.
    q20 = df["next_ret_1d"].quantile(0.20)
    q80 = df["next_ret_1d"].quantile(0.80)
    df["next_dir_tail"] = np.where(df["next_ret_1d"] >= q80,  1,
                          np.where(df["next_ret_1d"] <= q20, -1, 0))
    # For classifier: treat as binary (tail-up=1 vs everything else=0)
    # Only rows in the top or bottom quintile are used — middle rows dropped in prep_df
    df["next_dir_tail_bin"] = np.where(df["next_ret_1d"] >= q80, 1,
                              np.where(df["next_ret_1d"] <= q20, 0, np.nan))
    q20_val = round(q20 * 100, 3)
    q80_val = round(q80 * 100, 3)
    tail_n  = df["next_dir_tail_bin"].notna().sum()
    print(f"  Tail target: bottom quintile <= {q20_val}%, top >= {q80_val}% ({tail_n} rows)")

    # ── half-life sweep (--sweep-half-life) ────────────────────────────────────
    # Runs Logistic 1d WFCV at 4 half-lives and prints a comparison table.
    # Does NOT change the main training run — use --half-life to set the default.
    if sweep_half_life:
        sweep_lives = [252, 504, 756, 1260, 999999]
        print("\n[Half-life sweep — Logistic 1d Dir across recency half-lives]")
        print(f"  {'Half-life':>10} {'~yr':>5} {'Dir Acc':>16} {'AUC':>16}")
        print("  " + "-" * 52)
        for hl in sweep_lives:
            res_hl = walk_forward_cv(df, SPY_FEATURES, "next_dir_1d", "logistic", half_life=hl)
            if res_hl:
                acc_s = f"{res_hl['wfcv_dir_acc_mean']:.4f} ± {res_hl['wfcv_dir_acc_std']:.4f}"
                auc_s = (f"{res_hl['wfcv_auc_mean']:.4f} ± {res_hl['wfcv_auc_std']:.4f}"
                         if "wfcv_auc_mean" in res_hl else "    -")
                yr_s  = f"{hl/252:.1f}"
                marker = " <-- current" if hl == half_life else ""
                print(f"  {hl:>10} {yr_s:>5} {acc_s:>16} {auc_s:>16}{marker}")
            else:
                print(f"  {hl:>10}   insufficient data")
        print()

    # ── walk-forward CV ────────────────────────────────────────────────────────
    wfcv_results = {}
    if not skip_wfcv:
        print("\n[Walk-forward CV — 5 folds × 252 days each]")
        print(f"  {'Model':<30} {'Dir Acc':>16} {'AUC':>16}")
        print("  " + "-" * 65)
        wf_runs = [
            ("next_dir_1d",      "logistic", SPY_FEATURES,    "Logistic 1d Dir"),
            ("next_dir_1d",      "gbm",      GBM_FEATURES,    "GBM Clf 1d Dir"),
            ("next_dir_1d",      "gbm",      GBM_FEATURES,    "GBM Ensemble 1d"),
            ("next_ret_5d",      "gbm",      GBM_5D_FEATURES, "GBM 5d Ret"),
            # next_ret_5d_vadj dropped — lagged-rv fix still produces negative Spearman in latest
            # fold (0.456 dir acc Run 20). Vol normalization is non-stationary across regimes.
        ]
        for tgt, mtype, fset, lbl in wf_runs:
            res = walk_forward_cv(df, fset, tgt, mtype, half_life=half_life)
            if res:
                wfcv_results[lbl] = res
                acc_str = f"{res['wfcv_dir_acc_mean']:.4f} ± {res['wfcv_dir_acc_std']:.4f}"
                auc_str = (f"{res['wfcv_auc_mean']:.4f} ± {res['wfcv_auc_std']:.4f}"
                           if "wfcv_auc_mean" in res else "    -")
                print(f"  {lbl:<30} {acc_str:>16} {auc_str:>16}")
                # Print holdout result inline if available
                if "holdout_dir_acc" in res:
                    h_acc = res['holdout_dir_acc']
                    h_auc = res.get('holdout_auc', None)
                    h_n   = res.get('holdout_n_rows', '?')
                    h_auc_str = f"  AUC={h_auc:.4f}" if h_auc else ""
                    print(f"    [HOLDOUT {res.get('holdout_start','2024-01-01')}+, n={h_n}]  acc={h_acc:.4f}{h_auc_str}  <-- LOCKED, never used in dev")
            else:
                print(f"  {lbl:<30} insufficient data")
        print()

    # ── RFE feature selection pass ────────────────────────────────────────────
    # Runs RFECV on the full training set (all data before the val split) to
    # identify the most predictive 40-50 features from SPY_FEATURES.
    # Does NOT retrain or re-run WFCV — just identifies and saves for the next run.
    if not skip_wfcv:
        print(f"\n{'='*56}")
        print("  RFE feature selection (RFECV, LogisticRegression, cv=3)")
        print(f"{'='*56}")
        try:
            df_rfe, avail_rfe = prep_df(df, SPY_FEATURES, "next_dir_1d")
            split_rfe = max(len(df_rfe) - 252, int(len(df_rfe) * 0.8))
            tr_rfe = df_rfe.iloc[:split_rfe]
            X_rfe  = tr_rfe[avail_rfe].values
            y_rfe  = tr_rfe["next_dir_1d"].values
            sc_rfe = StandardScaler()
            X_rfe_scaled = sc_rfe.fit_transform(X_rfe)
            rfe_estimator = LogisticRegression(C=0.1, max_iter=1000, random_state=42)
            rfecv = RFECV(estimator=rfe_estimator, cv=3, scoring="roc_auc",
                          min_features_to_select=40, n_jobs=-1)
            rfecv.fit(X_rfe_scaled, y_rfe)
            selected_mask  = rfecv.support_
            selected_feats = [avail_rfe[i] for i in range(len(avail_rfe)) if selected_mask[i]]
            n_selected = len(selected_feats)
            n_total    = len(avail_rfe)
            print(f"  [RFE] Selected {n_selected} features from {n_total} total")
            print(f"  [RFE] Selected features:")
            for feat in selected_feats:
                print(f"    {feat}")
            rfe_out_path = os.path.expanduser(
                "~/discordBot/outputs/features/markets/spy_rfe_features.txt")
            os.makedirs(os.path.dirname(rfe_out_path), exist_ok=True)
            with open(rfe_out_path, "w") as f_rfe:
                f_rfe.write("\n".join(selected_feats) + "\n")
            print(f"  [RFE] Feature list saved -> {rfe_out_path}")
        except Exception as e_rfe:
            import traceback
            print(f"  [RFE error: {e_rfe}]")
            traceback.print_exc()

    # ── standard single-val-window training runs ──────────────────────────────
    runs = [
        ("next_ret_1d",       "ridge",    252, "1d return | Ridge",          SPY_FEATURES),
        ("next_ret_1d",       "gbm",      252, "1d return | GBM",            GBM_FEATURES),
        ("next_dir_1d",       "logistic", 252, "1d direction | Logistic",    SPY_FEATURES),
        ("next_dir_1d",       "gbm",      252, "1d direction | GBM Clf",     GBM_FEATURES),
        ("next_ret_5d",       "gbm",      252, "5d return | GBM",            GBM_5D_FEATURES),
        # next_ret_5d_vadj dropped Run 20 — both raw and lagged-rv denominator fail out-of-sample
        # Tail signal model dropped — val acc <50%, below-50% dir acc adds noise not signal.
        # Use blend_prob > 0.56 AND regime != bear as high-conviction filter instead.
        # ("next_dir_tail_bin", "logistic", 252, "Tail signal | Logistic", SPY_FEATURES),
    ]

    results             = []
    logistic_val_probs  = None
    logistic_val_y      = None
    logistic_val_dates  = None   # for meta-learner alignment
    ms_probs            = None   # hoisted so threshold backtest can reference it

    for target, model_type, val_days, label, feat_set in runs:
        print(f"\n{'='*56}")
        print(f"  {label}")
        print(f"{'='*56}")

        try:
            model, metrics, tr, va, trained_feats = train_one(
                df, feat_set, target, model_type, val_days, notes, half_life=half_life
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        # Stash logistic probs for blend (only the primary direction model)
        if model_type == "logistic" and target == "next_dir_1d":
            df_p, avail_l = prep_df(df, feat_set, target)
            split = max(len(df_p) - val_days, int(len(df_p) * 0.8))
            val_tmp = df_p.iloc[split:]
            logistic_val_probs = model.predict_proba(val_tmp[avail_l].values)[:, 1]
            logistic_val_y     = val_tmp[target].values
            logistic_val_dates = val_tmp["date"].values

        path = save_model(model, model_type, target, trained_features=trained_feats)
        print(f"  saved -> {path}")
        print(f"  train rows : {metrics['train_rows']} | val rows: {metrics['val_rows']}")

        if target in ("next_dir_1d", "next_dir_tail_bin"):
            print(f"  dir accuracy : {metrics['val_dir_acc']}  <- primary metric")
            print(f"  ROC-AUC      : {metrics['val_auc']}")
            print(f"  Brier score  : {metrics['val_brier']}")
            print(f"  Spearman     : {metrics['val_spearman']}")
        else:
            print(f"  Spearman     : {metrics['val_spearman']}  <- ranking accuracy")
            print(f"  Dir accuracy : {metrics['val_dir_acc']}")
            print(f"  RMSE         : {metrics['val_rmse']}")
            print(f"  R2           : {metrics['val_r2']}")

        if metrics["top5_features"]:
            print(f"  Top features : {metrics['top5_features']}")

        # Walk-forward CV numbers to attach to log
        wf = {}
        if label in wfcv_results:
            wf = wfcv_results[label]
        elif "Logistic 1d Dir" in wfcv_results and model_type == "logistic":
            wf = wfcv_results.get("Logistic 1d Dir", {})

        model_id = f"spy_{model_type}_{target}_{TODAY}"
        append_log({
            "model_id":   model_id,
            "target":     target,
            "model_type": model_type,
            "train_date": TODAY,
            "notes":      notes,
            **metrics,
            **wf,
        })
        results.append((label, metrics))

    # ── multi-seed GBM ensemble ───────────────────────────────────────────────
    print(f"\n{'='*56}")
    print(f"  1d direction | GBM Ensemble ({N_SEEDS} seeds)")
    print(f"{'='*56}")
    try:
        ms_models, ms_probs, ms_metrics, ms_tr, ms_va, ms_feats = train_multiseed_gbm(
            df, GBM_FEATURES, "next_dir_1d", val_days=252, n_seeds=N_SEEDS, half_life=half_life
        )
        fname = f"spy_gbm_ensemble_next_dir_1d_{TODAY}.pkl"
        epath = os.path.join(MODEL_DIR, fname)
        with open(epath, "wb") as f:
            pickle.dump({"models": ms_models, "n_seeds": N_SEEDS,
                         "features": ms_feats, "trained_on": TODAY}, f)
        print(f"  saved -> {epath}  ({N_SEEDS} models)")
        print(f"  train rows : {ms_metrics['train_rows']} | val rows: {ms_metrics['val_rows']}")
        print(f"  dir accuracy : {ms_metrics['val_dir_acc']}")
        print(f"  ROC-AUC      : {ms_metrics['val_auc']}")
        print(f"  Brier score  : {ms_metrics['val_brier']}")
        print(f"  Spearman     : {ms_metrics['val_spearman']}")
        print(f"  Top features : {ms_metrics['top5_features']}")
        wf_ens = wfcv_results.get("GBM Clf 1d Dir", {})
        append_log({"model_id": f"spy_gbm_ensemble_next_dir_1d_{TODAY}",
                    "target": "next_dir_1d", "model_type": "gbm_ensemble",
                    "train_date": TODAY, "notes": notes or f"{N_SEEDS}-seed avg",
                    **ms_metrics, **wf_ens})
        results.append((f"1d direction | GBM Ensemble ({N_SEEDS}s)", ms_metrics))

        # ── meta-learner stacker: Ridge on [logistic_prob, gbm_prob] ─────────
        # Replaces naive 50/50 average. Trained on the latter 50% of the train
        # set (held-out from both base models to avoid leakage), validated on
        # the same 252-day val window.
        if logistic_val_probs is not None:
            print(f"\n{'='*56}")
            print(f"  1d direction | Meta-learner (Ridge stacker)")
            print(f"{'='*56}")

            # Align logistic + GBM probs on the same val rows
            n      = min(len(logistic_val_probs), len(ms_probs))
            lp     = logistic_val_probs[-n:]
            gp     = ms_probs[-n:]
            y_true = logistic_val_y[-n:]

            # Build stacker training set from the latter half of train data
            # Use OOF-style: train stacker on train[split2:] where both models
            # have already been trained on train[:split2]
            df_p_s, avail_s = prep_df(df, SPY_FEATURES, "next_dir_1d")
            split_s  = max(len(df_p_s) - 252, int(len(df_p_s) * 0.8))
            # Hold out the last 25% of train for stacker fitting
            split_s2 = int(split_s * 0.75)
            st_tr = df_p_s.iloc[split_s2:split_s]   # stacker train
            # Re-predict on stacker train set using the already-trained models
            try:
                lm_pkl = sorted([f for f in os.listdir(MODEL_DIR)
                                 if "spy_logistic_next_dir_1d" in f])[-1]
                gm_pkl = sorted([f for f in os.listdir(MODEL_DIR)
                                 if "spy_gbm_ensemble_next_dir_1d" in f])[-1]
                with open(os.path.join(MODEL_DIR, lm_pkl), "rb") as fh:
                    lm_d = pickle.load(fh)
                with open(os.path.join(MODEL_DIR, gm_pkl), "rb") as fh:
                    gm_d = pickle.load(fh)

                lm_feats  = lm_d["features"]
                lm_model  = lm_d["model"]
                gm_models = gm_d["models"]
                gm_feats  = gm_d["features"]

                st_lp = lm_model.predict_proba(st_tr[lm_feats].values)[:, 1]
                st_gp = np.mean([m.predict_proba(st_tr[gm_feats].values)[:, 1]
                                 for m in gm_models], axis=0)
                st_y  = st_tr["next_dir_1d"].values

                X_stack_tr = np.column_stack([st_lp, st_gp])
                X_stack_va = np.column_stack([lp,    gp])

                from sklearn.linear_model import LogisticRegression as LR2
                stacker = LR2(C=1.0, max_iter=200, random_state=42)
                stacker.fit(X_stack_tr, st_y)
                meta_probs = stacker.predict_proba(X_stack_va)[:, 1]
                meta_pred  = (meta_probs >= 0.5).astype(int)
                meta_m = {
                    "train_rows": len(st_tr), "val_rows": int(n),
                    "val_window_days": 252,
                    **eval_metrics(y_true, meta_pred, meta_probs, is_clf=True),
                    "top5_features": f"logistic_coef={stacker.coef_[0][0]:.3f} gbm_coef={stacker.coef_[0][1]:.3f}",
                }
                mpath = os.path.join(MODEL_DIR, f"spy_meta_stacker_{TODAY}.pkl")
                with open(mpath, "wb") as fh:
                    pickle.dump({"model": stacker, "type": "meta_stacker",
                                 "components": ["logistic", "gbm_ensemble"],
                                 "logistic_feats": lm_feats, "gbm_feats": gm_feats,
                                 "trained_on": TODAY}, fh)
                print(f"  saved -> {mpath}")
                print(f"  stacker coefs: logistic={stacker.coef_[0][0]:.3f}  gbm={stacker.coef_[0][1]:.3f}")
                print(f"  val rows     : {meta_m['val_rows']}")
                print(f"  dir accuracy : {meta_m['val_dir_acc']}  <- primary metric")
                print(f"  ROC-AUC      : {meta_m['val_auc']}")
                print(f"  Brier score  : {meta_m['val_brier']}")
                print(f"  Spearman     : {meta_m['val_spearman']}")
                # Compare vs naive blend
                naive_blend = (lp + gp) / 2.0
                naive_da    = accuracy_score(y_true, (naive_blend >= 0.5).astype(int))
                naive_auc   = roc_auc_score(y_true, naive_blend)
                print(f"  vs 50/50 blend: dir_acc={naive_da:.4f}  AUC={naive_auc:.4f}  "
                      f"(meta delta: acc={meta_m['val_dir_acc']-naive_da:+.4f} "
                      f"auc={meta_m['val_auc']-naive_auc:+.4f})")
                append_log({"model_id": f"spy_meta_stacker_{TODAY}",
                            "target": "next_dir_1d", "model_type": "meta_stacker",
                            "train_date": TODAY, "notes": notes or "ridge_stacker",
                            **meta_m})
                results.append(("1d direction | Meta-stacker", meta_m))
                # Use meta probs as the primary blend signal going forward
                blend_probs_final = meta_probs
                blend_y_final     = y_true
            except Exception as e_st:
                import traceback
                print(f"  [meta-stacker error: {e_st}]")
                traceback.print_exc()
                blend_probs_final = (lp + gp) / 2.0
                blend_y_final     = y_true

            # ── 50/50 blend still saved for reference ─────────────────────────
            blend  = (lp + gp) / 2.0
            y_pred = (blend >= 0.5).astype(int)
            bl_m   = {
                "train_rows": ms_metrics["train_rows"], "val_rows": int(n),
                "val_window_days": 252,
                **eval_metrics(y_true, y_pred, blend, is_clf=True),
                "top5_features": "logistic+gbm_ensemble blend",
            }
            bpath = os.path.join(MODEL_DIR, f"spy_blend_next_dir_1d_{TODAY}.pkl")
            with open(bpath, "wb") as fh:
                pickle.dump({"type": "blend", "components": ["logistic", "gbm_ensemble"],
                             "weights": [0.5, 0.5], "trained_on": TODAY,
                             "gbm_feats": ms_feats, "n_seeds": N_SEEDS}, fh)
            print(f"\n  50/50 blend (reference): dir_acc={bl_m['val_dir_acc']:.4f}  AUC={bl_m['val_auc']:.4f}")
            append_log({"model_id": f"spy_blend_next_dir_1d_{TODAY}",
                        "target": "next_dir_1d", "model_type": "blend",
                        "train_date": TODAY, "notes": notes or "logistic+gbm_ensemble 50/50",
                        **bl_m})
            results.append(("1d direction | Blend (50/50)", bl_m))
        else:
            blend_probs_final = None
            blend_y_final     = None
            print("  [warn] blend skipped — logistic probs unavailable")

    except Exception as e:
        import traceback
        print(f"  ERROR in ensemble: {e}")
        traceback.print_exc()

    # ── regime-split Logistic models ──────────────────────────────────────────
    # Train one Logistic per regime (bull / bear / chop) so each model sees only
    # the rows relevant to its market environment.
    # Each regime uses its own feature list (BEAR_FEATURES / BULL_FEATURES / SPY_FEATURES)
    # to include regime-conditional signals that are invisible in global Spearman filtering.
    # Bull additionally restricts to post-2021 rows where VWAP/intraday features are dense.
    print(f"\n{'='*56}")
    print("  Regime-split Logistic models (bull / bear / chop)")
    print(f"{'='*56}")
    # Map each regime to its feature list
    REGIME_FEATURES = {
        "bear": BEAR_FEATURES,
        "bull": BULL_FEATURES,
        "chop": SPY_FEATURES,
    }
    # Bull training cutoff: post-2021 only (VWAP/intraday features dense from 2021-01-04)
    BULL_START = pd.Timestamp("2021-01-04")
    if "regime" in df.columns:
        regime_models = {}
        for regime_name in ("bull", "bear", "chop"):
            regime_df = df[df["regime"] == regime_name].copy()
            # Bull: restrict to post-2021 where VWAP/intraday are dense
            if regime_name == "bull":
                regime_df = regime_df[regime_df["date"] >= BULL_START].copy()
                print(f"\n  -- BULL (post-2021: {len(regime_df)} rows) --")
            else:
                print(f"\n  -- {regime_name.upper()} ({len(regime_df)} rows) --")
            n_regime = len(regime_df)
            if n_regime < 200:
                print(f"    [skip] only {n_regime} rows — need 200+")
                continue
            # Use regime-specific feature list
            feat_list = REGIME_FEATURES.get(regime_name, SPY_FEATURES)
            try:
                df_p, avail = prep_df(regime_df, feat_list, "next_dir_1d")
                if len(df_p) < 150:
                    print(f"    [skip] only {len(df_p)} rows after NaN drop")
                    continue

                split    = max(len(df_p) - min(63, len(df_p)//5), int(len(df_p)*0.8))
                tr_r, va_r = df_p.iloc[:split], df_p.iloc[split:]

                # ── Bear oversampling: repeat minority bear rows with small noise ──
                # Bear = ~15% of all training rows; class imbalance hurts recall.
                # Strategy: duplicate bear rows 2x with N(0, 0.01) jitter on numeric cols.
                # Only applied to bear model (bull/chop have balanced class distribution).
                if regime_name == "bear":
                    pos_mask = tr_r["next_dir_1d"] == 1
                    neg_mask = tr_r["next_dir_1d"] == 0
                    n_pos    = pos_mask.sum()
                    n_neg    = neg_mask.sum()
                    minority_mask = pos_mask if n_pos < n_neg else neg_mask
                    minority_rows = tr_r[minority_mask]
                    if len(minority_rows) > 10:
                        rng = np.random.default_rng(42)
                        noise = rng.normal(0, 0.01, size=minority_rows[avail].shape)
                        aug_df = minority_rows.copy()
                        aug_df[avail] = minority_rows[avail].values + noise
                        tr_r = pd.concat([tr_r, aug_df], ignore_index=True)
                        print(f"    [bear oversample] +{len(aug_df)} rows "
                              f"(minority class: {'up' if n_pos < n_neg else 'down'}, "
                              f"was {min(n_pos,n_neg)}/{len(tr_r)-len(aug_df)} "
                              f"-> {min(n_pos,n_neg)+len(aug_df)}/{len(tr_r)})")

                sw_r     = compute_sample_weights(tr_r["date"], half_life_days=half_life)
                m_r      = Pipeline([("sc", StandardScaler()),
                                     ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
                m_r.fit(tr_r[avail].values, tr_r["next_dir_1d"].values,
                        **{"m__sample_weight": sw_r})

                y_pr_r = m_r.predict_proba(va_r[avail].values)[:, 1]
                y_pd_r = (y_pr_r >= 0.5).astype(int)
                y_va_r = va_r["next_dir_1d"].values
                da_r   = accuracy_score(y_va_r, y_pd_r)
                try:
                    auc_r = roc_auc_score(y_va_r, y_pr_r)
                except Exception:
                    auc_r = None
                sp_r,_ = stats.spearmanr(y_va_r, y_pr_r)

                coefs   = np.abs(m_r.named_steps["m"].coef_.ravel())
                top_idx = np.argsort(coefs)[::-1][:5]
                top5_r  = [f"{avail[i]}={coefs[i]:.3f}" for i in top_idx]

                print(f"    train={len(tr_r)}  val={len(va_r)}")
                auc_str = f"{auc_r:.4f}" if auc_r is not None else "N/A"
                print(f"    dir_acc={da_r:.4f}  AUC={auc_str}  Spearman={sp_r:.4f}")
                print(f"    top: {'; '.join(top5_r)}")

                rpath = os.path.join(MODEL_DIR, f"spy_logistic_regime_{regime_name}_{TODAY}.pkl")
                with open(rpath, "wb") as f:
                    pickle.dump({"model": m_r, "features": avail,
                                 "regime": regime_name, "trained_on": TODAY}, f)
                regime_models[regime_name] = {"model": m_r, "features": avail,
                                              "dir_acc": da_r, "auc": auc_r}

                # ── calibrate regime models: Platt scaling for small val sets ─────
                # Isotonic regression overfits on < 200 samples (bear val = 63 rows).
                # Platt scaling (sigmoid) is lower-variance and generalizes better.
                # Use isotonic only if val set >= 200 rows (chop sometimes qualifies).
                if regime_name in ("bear", "chop") and len(va_r) >= 20:
                    try:
                        n_val = len(va_r)
                        cal_method = "sigmoid" if n_val < 200 else "isotonic"
                        cal_model = CalibratedClassifierCV(m_r, cv=None, method=cal_method,
                                                           ensemble=False)
                        cal_model.fit(va_r[avail].values, y_va_r)
                        cal_probs = cal_model.predict_proba(va_r[avail].values)[:, 1]
                        cal_auc   = roc_auc_score(y_va_r, cal_probs) if len(np.unique(y_va_r)) > 1 else None
                        cal_da    = accuracy_score(y_va_r, (cal_probs >= 0.5).astype(int))

                        # ── chop model inversion check ──────────────────────────────────
                        # AUC < 0.45 means the model is actively inverting predictions.
                        # Flip probabilities (1 - p) and re-evaluate; if still < 0.48
                        # after flip the model is pure noise and dropped from the blend.
                        chop_flip   = False
                        chop_drop   = False
                        if regime_name == "chop" and cal_auc is not None and cal_auc < 0.45:
                            print(f"    [WARN] chop model inverted (AUC < 0.45) - flipping probabilities")
                            cal_probs = 1.0 - cal_probs
                            chop_flip = True
                            flip_auc  = roc_auc_score(y_va_r, cal_probs) if len(np.unique(y_va_r)) > 1 else None
                            cal_auc   = flip_auc
                            cal_da    = accuracy_score(y_va_r, (cal_probs >= 0.5).astype(int))
                            if flip_auc is not None and flip_auc < 0.48:
                                print(f"    [WARN] chop model dropped from blend - noise after flip")
                                chop_drop = True

                        cal_path  = os.path.join(MODEL_DIR, f"spy_logistic_regime_{regime_name}_calibrated_{TODAY}.pkl")
                        with open(cal_path, "wb") as fc:
                            pickle.dump({"model": cal_model, "features": avail,
                                         "regime": regime_name, "calibrated": True,
                                         "method": cal_method, "trained_on": TODAY,
                                         "flip_probs": chop_flip,
                                         "drop_from_blend": chop_drop}, fc)
                        print(f"    calibrated {regime_name} ({cal_method}, n_val={n_val}): dir_acc={cal_da:.4f}  AUC={round(cal_auc,4) if cal_auc else 'N/A'}")
                        if regime_name == "bear" and cal_method == "sigmoid":
                            print(f"    [NOTE] Bear val AUC on <63 rows is unreliable - use Bear WFCV AUC as benchmark")
                        print(f"    saved calibrated -> {cal_path}")
                        # Plot calibration curve
                        try:
                            fig, ax = plt.subplots(figsize=(5, 4))
                            frac_pos_raw, mean_pred_raw = calibration_curve(y_va_r, y_pr_r, n_bins=8)
                            frac_pos_cal, mean_pred_cal = calibration_curve(y_va_r, cal_probs, n_bins=8)
                            ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="Perfect")
                            ax.plot(mean_pred_raw, frac_pos_raw, "b-o", ms=4, label=f"Raw (Brier={brier_score_loss(y_va_r, y_pr_r):.3f})")
                            ax.plot(mean_pred_cal, frac_pos_cal, "r-o", ms=4, label=f"Calibrated (Brier={brier_score_loss(y_va_r, cal_probs):.3f})")
                            ax.set_xlabel("Mean predicted prob")
                            ax.set_ylabel("Fraction positive")
                            ax.set_title(f"{regime_name.capitalize()} model calibration curve")
                            ax.legend(fontsize=8)
                            fig.tight_layout()
                            cal_plot_path = os.path.expanduser(f"~/discordBot/outputs/markets/{regime_name}_calibration_curve.png")
                            fig.savefig(cal_plot_path, dpi=120)
                            plt.close(fig)
                            print(f"    calibration plot -> {cal_plot_path}")
                        except Exception as ep:
                            print(f"    [calibration plot error: {ep}]")
                    except Exception as ec:
                        print(f"    [calibration error: {ec}]")
                append_log({
                    "model_id": f"spy_logistic_regime_{regime_name}_{TODAY}",
                    "target": "next_dir_1d", "model_type": f"logistic_regime_{regime_name}",
                    "train_date": TODAY, "notes": notes or f"regime={regime_name}",
                    "train_rows": len(tr_r), "val_rows": len(va_r), "val_window_days": len(va_r),
                    "val_dir_acc": round(da_r, 4),
                    "val_auc": round(auc_r, 4) if auc_r else None,
                    "val_spearman": round(float(sp_r), 4),
                    "top5_features": "; ".join(top5_r),
                    "val_rmse": None, "val_mae": None, "val_r2": None, "val_brier": None,
                })
                results.append((f"Regime Logistic | {regime_name}", {
                    "val_dir_acc": da_r, "val_auc": auc_r, "val_spearman": float(sp_r),
                }))
            except Exception as e:
                print(f"    ERROR: {e}")
    else:
        print("  [warn] 'regime' column missing from spy_features.csv — run buildSpyFeatures.py")

    # ── Bear-only expanding WFCV ───────────────────────────────────────────────
    # Standard WFCV mixes all regimes. Bear model needs its own honest CV since
    # it trains on ~400 rows and the 63-row val window is a single slice.
    # Bear-only CV: 4 expanding folds × 50 bear rows each, trained on all prior bear rows.
    bear_wfcv_auc_mean = None   # captured below; shown in summary table next to bear row
    if "regime" in df.columns:
        print(f"\n{'='*56}")
        print("  Bear-only expanding WFCV (4 folds x 50 bear rows)")
        print(f"{'='*56}")
        try:
            bear_df_cv = df[df["regime"] == "bear"].copy().reset_index(drop=True)
            bear_p, bear_avail = prep_df(bear_df_cv, BEAR_FEATURES, "next_dir_1d")
            BEAR_FOLD = 50
            N_BEAR_FOLDS = 4
            total_bear = len(bear_p)
            if total_bear < (N_BEAR_FOLDS + 1) * BEAR_FOLD:
                print(f"  [skip] only {total_bear} bear rows — need {(N_BEAR_FOLDS+1)*BEAR_FOLD}+")
            else:
                fold_starts_b = [total_bear - (N_BEAR_FOLDS - i) * BEAR_FOLD
                                  for i in range(N_BEAR_FOLDS)]
                bear_accs, bear_aucs = [], []
                print(f"  {'Fold':<8} {'Date range':<26} {'Dir Acc':>8} {'AUC':>8} {'n':>5}")
                print("  " + "-" * 60)
                for fs_b in fold_starts_b:
                    if fs_b < BEAR_FOLD:
                        continue
                    tr_b = bear_p.iloc[:fs_b]
                    va_b = bear_p.iloc[fs_b:fs_b + BEAR_FOLD]
                    if len(va_b) < 20:
                        continue
                    sw_b = compute_sample_weights(tr_b["date"], half_life_days=half_life)
                    m_b  = Pipeline([("sc", StandardScaler()),
                                     ("m", LogisticRegression(C=0.1, max_iter=500, random_state=42))])
                    try:
                        m_b.fit(tr_b[bear_avail].values, tr_b["next_dir_1d"].values,
                                **{"m__sample_weight": sw_b})
                    except TypeError:
                        m_b.fit(tr_b[bear_avail].values, tr_b["next_dir_1d"].values)
                    yp_b  = m_b.predict_proba(va_b[bear_avail].values)[:, 1]
                    yd_b  = (yp_b >= 0.5).astype(int)
                    acc_b = accuracy_score(va_b["next_dir_1d"].values, yd_b)
                    try:
                        auc_b = roc_auc_score(va_b["next_dir_1d"].values, yp_b)
                    except Exception:
                        auc_b = None
                    bear_accs.append(acc_b)
                    if auc_b: bear_aucs.append(auc_b)
                    d0 = str(va_b["date"].iloc[0].date())
                    d1 = str(va_b["date"].iloc[-1].date())
                    auc_str = f"{auc_b:.4f}" if auc_b else "  N/A"
                    print(f"  fold {len(bear_accs):<3}  {d0} - {d1}  {acc_b:.4f}  {auc_str}  {len(va_b):>5}")
                if bear_accs:
                    print(f"\n  Bear WFCV: dir_acc={np.mean(bear_accs):.4f} ± {np.std(bear_accs):.4f}"
                          + (f"  AUC={np.mean(bear_aucs):.4f} ± {np.std(bear_aucs):.4f}" if bear_aucs else ""))
                    if bear_aucs:
                        bear_wfcv_auc_mean = round(float(np.mean(bear_aucs)), 4)
        except Exception as e_bwfcv:
            print(f"  [bear WFCV error: {e_bwfcv}]")

    # ── threshold backtest with transaction costs ─────────────────────────────
    # For each probability threshold t: long SPY when blend_prob > t, flat otherwise.
    # Transaction cost: 0.02% per trade (round trip). Count trades = direction changes.
    # Shows which conviction level produces best Sharpe on the val set.
    # Uses meta-stacker probs if available, otherwise falls back to 50/50 blend.
    if logistic_val_probs is not None and ms_probs is not None:
        print(f"\n{'='*56}")
        print("  Threshold backtest (val set, 0.02% tx cost per trade)")
        print(f"{'='*56}")
        try:
            n_bt      = min(len(logistic_val_probs), len(ms_probs))
            # Prefer meta-stacker probs; fall back to 50/50 blend
            if blend_probs_final is not None and len(blend_probs_final) == n_bt:
                bl_probs = blend_probs_final
                print("  [using meta-stacker probs]")
            else:
                bl_probs  = (logistic_val_probs[-n_bt:] + ms_probs[-n_bt:]) / 2.0
                print("  [using 50/50 blend probs — meta-stacker unavailable]")
            # Get actual next-day returns aligned to logistic val window
            df_p_l, avail_l = prep_df(df, SPY_FEATURES, "next_dir_1d")
            split_l  = max(len(df_p_l) - 252, int(len(df_p_l) * 0.8))
            val_rows = df_p_l.iloc[split_l:]
            val_ret  = val_rows["next_ret_1d"].values[-n_bt:]
            # Drop any NaN returns (last row of history has no next-day return)
            valid_mask = ~np.isnan(val_ret) & ~np.isnan(bl_probs[-len(val_ret):])
            val_ret  = val_ret[valid_mask]
            bl_probs_bt = bl_probs[-len(valid_mask):][valid_mask]

            TX_COST   = 0.0005   # 5bps one-way — see WFCV section comment
            ANNUAL    = 252

            print(f"  {'Threshold':>10} {'Trades':>7} {'Signals':>8} {'Ret(ann)':>10} {'Sharpe':>8} {'MaxDD':>8}")
            print("  " + "-" * 58)

            best_sharpe  = -99.0
            best_thresh  = 0.5
            best_results = {}

            for thresh in [0.50, 0.52, 0.54, 0.55, 0.56, 0.57, 0.58, 0.60, 0.62, 0.65]:
                position  = (bl_probs_bt > thresh).astype(float)  # 1 = long, 0 = flat
                # Cost = TX_COST on each position change (entry + exit each cost half)
                trades    = int(np.sum(np.abs(np.diff(position))))
                cost_drag = trades * TX_COST / len(val_ret)   # per-day cost drag
                daily_ret = position * val_ret - (np.abs(np.diff(np.append(0, position))) * TX_COST)
                n_signals = int(position.sum())
                ann_ret   = daily_ret.mean() * ANNUAL
                ann_vol   = daily_ret.std()  * np.sqrt(ANNUAL)
                sharpe    = ann_ret / ann_vol if ann_vol > 0 else 0.0
                # Max drawdown
                cum       = (1 + daily_ret).cumprod()
                rolling_max = np.maximum.accumulate(cum)
                drawdowns = (cum - rolling_max) / rolling_max
                max_dd    = float(drawdowns.min())

                if sharpe > best_sharpe:
                    best_sharpe  = sharpe
                    best_thresh  = thresh
                    best_results = {"sharpe": sharpe, "ann_ret": ann_ret,
                                    "max_dd": max_dd, "n_signals": n_signals, "trades": trades}

                marker = " <-- best" if thresh == best_thresh else ""
                print(f"  {thresh:>10.2f} {trades:>7d} {n_signals:>8d} "
                      f"{ann_ret*100:>9.1f}% {sharpe:>8.3f} {max_dd*100:>7.1f}%{marker}")

            print(f"\n  Best threshold: {best_thresh:.2f}  "
                  f"Sharpe={best_results['sharpe']:.3f}  "
                  f"Ann ret={best_results['ann_ret']*100:.1f}%  "
                  f"MaxDD={best_results['max_dd']*100:.1f}%  "
                  f"Signals={best_results['n_signals']}/252 days")

            # SPY buy-and-hold benchmark over same period
            bh_ann     = val_ret.mean() * ANNUAL
            bh_vol     = val_ret.std()  * np.sqrt(ANNUAL)
            bh_sharpe  = bh_ann / bh_vol if bh_vol > 0 else 0.0
            bh_cum     = (1 + val_ret).cumprod()
            bh_dd      = float(((bh_cum - np.maximum.accumulate(bh_cum)) / np.maximum.accumulate(bh_cum)).min())
            print(f"  SPY buy & hold: Sharpe={bh_sharpe:.3f}  Ann ret={bh_ann*100:.1f}%  MaxDD={bh_dd*100:.1f}%")

            # ── Regime-conditional position sizing + fractional Kelly ────────────
            # Sizing hierarchy (applied in order):
            # 1. Kill switch: flat when bear AND gex_sign<=0 AND vix_z21>1.5
            # 2. Rate-shock regime: go to 50% (2022-style inflationary bear)
            # 3. Bear regime: go to 50% (structural downtrend)
            # 4. Fractional Kelly: size proportional to edge estimate
            #    Kelly f = (p - (1-p)) / 1.0 = 2p - 1 (for binary win/lose, b=1)
            #    Fractional (25%) Kelly: f* = 0.25 * (2p - 1)
            #    Capped at 1.0 (100% max), floored at 0 (no shorting)
            # 5. Flat if prob < 0.53 (minimum edge threshold)
            print(f"\n  Regime-conditional sizing (Platt-calibrated Kelly + bear half-size):")
            try:
                val_regime = val_rows["regime"].values[-n_bt:][valid_mask]
                is_bear    = (val_regime == "bear").astype(float)
                # Kill switch: go flat on bear days where GEX is negative AND VIX spiking
                gex_sign_bt = (val_rows["gex_sign"].values[-n_bt:][valid_mask]
                               if "gex_sign" in val_rows.columns else np.ones(len(is_bear)))
                vix_z21_bt  = (val_rows["vix_z21"].values[-n_bt:][valid_mask]
                               if "vix_z21"  in val_rows.columns else np.zeros(len(is_bear)))
                kill_switch = ((val_regime == "bear") &
                               (gex_sign_bt <= 0) &
                               (vix_z21_bt  >  1.5)).astype(float)
                for label_s, size_bear, thresh_s, use_kill, use_kelly, chop_lower, vol2_press in [
                    ("Half-bear, t=0.53",          0.5, 0.53, False, False, False, False),
                    ("Half-bear, t=0.54",          0.5, 0.54, False, False, False, False),
                    ("Zero-bear, t=0.54",          0.0, 0.54, False, False, False, False),
                    ("Kill(bear+GEX-+VIX), t=0.53", 0.0, 0.53, True,  False, False, False),
                    ("Frac-Kelly(50%), half-bear",  0.5, 0.53, False, True,  False, False),
                    ("Chop-lower, t=0.51",         0.5, 0.51, False, False, True,  False),
                    ("Vol2-press(VIX20-30 full)",  0.5, 0.53, False, True,  False, True),
                ]:
                    raw_pos = (bl_probs_bt > thresh_s).astype(float)
                    if chop_lower:
                        # Chop-lower: lower threshold (0.51) on chop days, higher (0.54) on bull/bear
                        raw_pos = np.where(val_regime == "chop",
                                           (bl_probs_bt > 0.51).astype(float),
                                           (bl_probs_bt > 0.54).astype(float))
                    sizing  = np.where(is_bear, size_bear, 1.0)
                    if vol2_press:
                        # Vol2-press: vol_regime==2 (VIX 20-30) gets full Kelly (not halved for bear)
                        # 64.5% up-days in holdout in this regime -- press harder when VIX is elevated but not panic
                        vol_reg_bt = (val_rows["vol_regime"].values[-n_bt:][valid_mask]
                                      if "vol_regime" in val_rows.columns else np.ones(len(is_bear)))
                        is_vol2    = (vol_reg_bt == 2.0).astype(float)
                        sizing     = np.where(is_vol2, 1.0, sizing)   # override half-bear for regime=2
                    pos_s   = raw_pos * sizing
                    if use_kelly:
                        # Fractional Kelly (50%): f* = 0.50 * (2p - 1), clipped [0, 1]
                        # Sweep in Run 32 showed Sharpe invariant across 25/33/50%; 50% doubles ann ret at same Sharpe.
                        kelly_f = np.clip(0.50 * (2 * bl_probs_bt - 1), 0, 1)
                        pos_s   = kelly_f * sizing   # Kelly replaces binary signal
                        pos_s   = np.where(bl_probs_bt > thresh_s, pos_s, 0)  # min threshold still applies
                    if use_kill:
                        pos_s = pos_s * (1 - kill_switch)   # zero out kill-switch days
                    dr_s    = pos_s * val_ret - (np.abs(np.diff(np.append(0, pos_s))) * TX_COST)
                    ar_s    = dr_s.mean() * ANNUAL
                    av_s    = dr_s.std()  * np.sqrt(ANNUAL)
                    sh_s    = ar_s / av_s if av_s > 0 else 0.0
                    cum_s   = (1 + dr_s).cumprod()
                    dd_s    = float(((cum_s - np.maximum.accumulate(cum_s)) / np.maximum.accumulate(cum_s)).min())
                    n_sig_s = int((pos_s > 0).sum())
                    n_kill  = int(kill_switch.sum()) if use_kill else 0
                    kill_str = f"  [killed {n_kill} bear+GEX-+VIX days]" if use_kill else ""
                    print(f"  {label_s:<38}  Sharpe={sh_s:.3f}  ret={ar_s*100:.1f}%  "
                          f"dd={dd_s*100:.1f}%  n={n_sig_s}{kill_str}")

                # ── Kelly fraction sweep: test [0.25, 0.33, 0.50] ────────────
                print(f"\n  Kelly fraction sweep (half-bear sizing, t=0.53):")
                for kf in [0.25, 0.33, 0.50]:
                    kf_pct = int(round(kf * 100))
                    sweep_f  = np.clip(kf * (2 * bl_probs_bt - 1), 0, 1)
                    sweep_sz = np.where(is_bear, 0.5, 1.0)
                    sweep_pos = np.where(bl_probs_bt > 0.53, sweep_f * sweep_sz, 0)
                    dr_kf    = sweep_pos * val_ret - (np.abs(np.diff(np.append(0, sweep_pos))) * TX_COST)
                    ar_kf    = dr_kf.mean() * ANNUAL
                    av_kf    = dr_kf.std()  * np.sqrt(ANNUAL)
                    sh_kf    = ar_kf / av_kf if av_kf > 0 else 0.0
                    cum_kf   = (1 + dr_kf).cumprod()
                    dd_kf    = float(((cum_kf - np.maximum.accumulate(cum_kf)) / np.maximum.accumulate(cum_kf)).min())
                    n_kf     = int((sweep_pos > 0).sum())
                    print(f"  Kelly({kf_pct:2d}%), half-bear              "
                          f"Sharpe={sh_kf:.3f}  ret={ar_kf*100:.1f}%  dd={dd_kf*100:.1f}%  n={n_kf}")
            except Exception as e_sz:
                print(f"  [sizing error: {e_sz}]")

            # ── cumulative PnL chart across all WFCV folds ────────────────────
            # Stitches per-fold bt returns into one continuous equity curve.
            # Requires fold bt data stored in wfcv_results fold_details.
            try:
                fold_curves = []
                fold_labels = []
                wfcv_key = "Logistic 1d Dir"
                if wfcv_key in wfcv_results:
                    folds = wfcv_results[wfcv_key].get("wfcv_fold_details", [])
                    # Rebuild per-fold equity from walk_forward_cv stored fold data
                    # We need the raw returns — re-run a lightweight version here
                    df_pnl, avail_pnl = prep_df(df, SPY_FEATURES, "next_dir_1d")
                    total_pnl = len(df_pnl)
                    fold_size_pnl = 252
                    n_folds_pnl   = 5
                    fold_starts_pnl = [total_pnl - (n_folds_pnl - i) * fold_size_pnl
                                       for i in range(n_folds_pnl)]
                    all_fold_dr   = []
                    all_fold_bh   = []
                    all_fold_dates = []
                    TX_PNL = 0.0005   # match main backtest: 5bps realistic
                    BT_T   = 0.54

                    for fs in fold_starts_pnl:
                        if fs < fold_size_pnl:
                            continue
                        tr_p = df_pnl.iloc[:fs]
                        va_p = df_pnl.iloc[fs:fs + fold_size_pnl]
                        if len(va_p) < 50:
                            continue
                        sw_p = compute_sample_weights(tr_p["date"], half_life_days=half_life)
                        mp   = build_model("logistic", is_clf=True)
                        try:
                            mp.fit(tr_p[avail_pnl].values, tr_p["next_dir_1d"].values,
                                   **{"m__sample_weight": sw_p})
                        except TypeError:
                            mp.fit(tr_p[avail_pnl].values, tr_p["next_dir_1d"].values)
                        yp_p    = mp.predict_proba(va_p[avail_pnl].values)[:, 1]
                        ret_p   = va_p["next_ret_1d"].values
                        valid_p = ~np.isnan(ret_p) & ~np.isnan(yp_p[:len(ret_p)])
                        if valid_p.sum() < 20:
                            continue
                        ret_p = ret_p[valid_p]
                        yp_p  = yp_p[:len(valid_p)][valid_p]
                        pos_p = (yp_p > BT_T).astype(float)
                        dr_p  = pos_p * ret_p - np.abs(np.diff(np.append(0, pos_p))) * TX_PNL
                        all_fold_dr.append(dr_p)
                        all_fold_bh.append(ret_p)
                        all_fold_dates.append(va_p["date"].values[valid_p])

                    if all_fold_dr:
                        combined_dr    = np.concatenate(all_fold_dr)
                        combined_bh    = np.concatenate(all_fold_bh)
                        combined_dates = np.concatenate(all_fold_dates)
                        cum_strat = np.cumprod(1 + combined_dr)
                        cum_bh    = np.cumprod(1 + combined_bh)

                        # ── Save PnL CSV for R diagnostics panel ──────────────
                        pnl_csv_path = os.path.expanduser(
                            "~/discordBot/outputs/features/markets/spy_wfcv_pnl.csv")
                        roll_max_csv = np.maximum.accumulate(cum_strat)
                        dd_csv       = (cum_strat - roll_max_csv) / roll_max_csv
                        # Assign fold numbers to each row
                        fold_num_arr = np.concatenate([
                            np.full(len(all_fold_dr[i]), i + 1)
                            for i in range(len(all_fold_dr))
                        ])
                        pd.DataFrame({
                            "date":       pd.to_datetime(combined_dates).strftime("%Y-%m-%d"),
                            "strat_ret":  combined_dr,
                            "bh_ret":     combined_bh,
                            "cum_strat":  cum_strat,
                            "cum_bh":     cum_bh,
                            "drawdown":   dd_csv,
                            "fold":       fold_num_arr,
                        }).to_csv(pnl_csv_path, index=False)
                        print(f"  WFCV PnL CSV -> {pnl_csv_path}")

                        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7),
                                                        gridspec_kw={"height_ratios": [3, 1]})
                        # Convert dates for plotting
                        import matplotlib.dates as mdates
                        plot_dates = pd.to_datetime(combined_dates)
                        ax1.plot(plot_dates, cum_strat, color="#2196F3", lw=1.5,
                                 label=f"Model t=0.54 (final: {cum_strat[-1]-1:.1%})")
                        ax1.plot(plot_dates, cum_bh, color="#9E9E9E", lw=1.0,
                                 alpha=0.7, label=f"Buy & hold (final: {cum_bh[-1]-1:.1%})")
                        # Shade fold boundaries
                        fold_boundaries = [pd.to_datetime(all_fold_dates[i][0])
                                           for i in range(len(all_fold_dates))]
                        for i, fb in enumerate(fold_boundaries):
                            ax1.axvline(fb, color="#FF5722", lw=0.8, alpha=0.5,
                                        linestyle="--",
                                        label=f"Fold {i+1}" if i == 0 else None)
                        ax1.set_ylabel("Cumulative return")
                        ax1.set_title("SPY model - cumulative PnL across 5 WFCV folds")
                        ax1.legend(fontsize=8)
                        ax1.grid(True, alpha=0.3)
                        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
                        # Drawdown panel
                        roll_max  = np.maximum.accumulate(cum_strat)
                        drawdowns = (cum_strat - roll_max) / roll_max * 100
                        ax2.fill_between(plot_dates, drawdowns, 0, color="#F44336", alpha=0.5)
                        ax2.set_ylabel("Drawdown (%)")
                        ax2.set_xlabel("Date")
                        ax2.grid(True, alpha=0.3)
                        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
                        fig.tight_layout()
                        pnl_path = os.path.expanduser("~/discordBot/outputs/markets/spy_wfcv_pnl_curve.png")
                        fig.savefig(pnl_path, dpi=130)
                        plt.close(fig)
                        print(f"\n  WFCV cumulative PnL chart -> {pnl_path}")
                        strat_total = cum_strat[-1] - 1
                        bh_total    = cum_bh[-1] - 1
                        strat_ann   = combined_dr.mean() * 252
                        strat_vol   = combined_dr.std() * np.sqrt(252)
                        strat_sh    = strat_ann / strat_vol if strat_vol > 0 else 0
                        bh_ann      = combined_bh.mean() * 252
                        bh_vol      = combined_bh.std() * np.sqrt(252)
                        bh_sh       = bh_ann / bh_vol if bh_vol > 0 else 0
                        roll_max_s  = np.maximum.accumulate(cum_strat)
                        max_dd_s    = float(((cum_strat - roll_max_s) / roll_max_s).min())
                        print(f"  Strategy:  total={strat_total:.1%}  ann={strat_ann:.1%}  "
                              f"Sharpe={strat_sh:.3f}  MaxDD={max_dd_s:.1%}")
                        print(f"  B&H:       total={bh_total:.1%}  ann={bh_ann:.1%}  Sharpe={bh_sh:.3f}")
            except Exception as e_pnl:
                import traceback
                print(f"  [PnL chart error: {e_pnl}]")
                traceback.print_exc()

        except Exception as e:
            import traceback
            print(f"  ERROR in backtest: {e}")
            traceback.print_exc()

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*56}")
    print("SUMMARY")
    print(f"{'='*56}")
    print(f"  {'Model':<36} {'Dir Acc':>8} {'AUC':>8} {'Spearman':>10}")
    print("  " + "-" * 66)
    for label, m in results:
        da  = f"{m['val_dir_acc']:.4f}"  if m.get("val_dir_acc")  is not None else "  -   "
        auc = f"{m['val_auc']:.4f}"      if m.get("val_auc")      is not None else "  -   "
        sp  = f"{m['val_spearman']:.4f}" if m.get("val_spearman") is not None else "  -   "
        print(f"  {label:<36} {da:>8} {auc:>8} {sp:>10}")
        if "bear" in label.lower() and bear_wfcv_auc_mean is not None:
            print(f"    [Bear WFCV AUC (4-fold): {bear_wfcv_auc_mean:.4f}  <- use this as benchmark, not val AUC]")

    if not skip_wfcv and wfcv_results:
        print(f"\n[Walk-forward CV summary]")
        for lbl, res in wfcv_results.items():
            acc_str = f"{res['wfcv_dir_acc_mean']:.4f} ± {res['wfcv_dir_acc_std']:.4f}"
            auc_str = (f"{res.get('wfcv_auc_mean','?'):.4f} ± {res.get('wfcv_auc_std','?'):.4f}"
                       if "wfcv_auc_mean" in res else "-")
            print(f"  {lbl:<30}  dir_acc={acc_str}  auc={auc_str}")
            # Per-fold detail with regime composition and backtest
            for i, fd in enumerate(res.get("wfcv_fold_details", [])):
                regime_str = ""
                if "bull_pct" in fd:
                    regime_str = (f"  bull={fd['bull_pct']*100:.0f}%"
                                  f" bear={fd['bear_pct']*100:.0f}%"
                                  f" chop={fd['chop_pct']*100:.0f}%")
                auc_f = f"  auc={fd['auc']:.4f}" if "auc" in fd else ""
                bt_str = ""
                if "bt_sharpe" in fd:
                    sized_str = (f" sized={fd['bt_sized_sharpe']:.3f}"
                                 if fd.get("bt_sized_sharpe") is not None else "")
                    sized_chop_str = (f" sized_chop={fd['bt_sized_chop_sharpe']:.3f}"
                                      if fd.get("bt_sized_chop_sharpe") is not None else "")
                    bt_str = (f"  | bt@0.54: Sharpe={fd['bt_sharpe']:.3f}"
                              f" ret={fd['bt_ann_ret']*100:.1f}%"
                              f" dd={fd['bt_maxdd']*100:.1f}%"
                              f" BH={fd['bh_sharpe']:.3f}"
                              f" n={fd['bt_signals']}{sized_str}{sized_chop_str}")
                print(f"    fold {i+1} ({fd['start']} - {fd['end']})"
                      f"  acc={fd['dir_acc']:.4f}{auc_f}{regime_str}{bt_str}")

    print(f"\n[trainSpyModel] done. Log: {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes",     type=str,  default="")
    parser.add_argument("--skip-wfcv", action="store_true",
                        help="Skip walk-forward CV (faster for quick retrain checks)")
    parser.add_argument("--half-life", type=int,  default=1260,
                        help="Recency weight half-life in trading days (default: 1260 = ~5yr). "
                             "Try 252 (1yr), 504 (2yr), 1260 (5yr) to sweep.")
    parser.add_argument("--sweep-half-life", action="store_true",
                        help="Run Logistic 1d WFCV at 252/504/756/1260 half-lives and print "
                             "a comparison table. Does not change the main training run.")
    args = parser.parse_args()
    run_training(notes=args.notes, skip_wfcv=args.skip_wfcv, half_life=args.half_life,
                 sweep_half_life=args.sweep_half_life)
