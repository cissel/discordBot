#!/usr/bin/env python3
"""
SPY ML Model State Analysis
"""
import os
import sys
import glob
import pickle
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy")

# ── Replicate definitions from trainSpyModel.py ──────────────────────────────

SPY_FEATURES = [
    "spy_ret_r5", "spy_rsi_3", "spy_z21", "spy_consec_up", "spy_consec_down",
    "spy_drawdown_252", "vix_level", "vix_z21", "vol_regime",
    "vvix_z21", "vvix_chg_5d", "vix_term_z21",
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    "gld_ret_1d", "gld_z21", "gld_spy_corr_21",
    "qqq_ret_1d",
    "xle_spy_rs_5d",
    "fedfunds_chg_21d",
    "yield_curve_z63",
    "macro_event_x_regime", "macro_event_window",
    "fomc_window", "fomc_before", "fomc_after", "days_to_fomc",
    "gex_chg_5d", "dix_z21", "dix_chg_5d",
    "is_friday",
    "sector_risk_off_r5",
    "XLF_ret", "XLI_ret", "XLY_ret",
    "vwap_dev_open", "vwap_dev_r5", "vol_concentration",
    "vwap_cross_count", "vwap_time_above_pct", "vwap_dev_am", "vol_vwap_corr_5d",
    "open_drive_flag", "late_reversal_flag", "overnight_gap", "gap_fill_flag", "am_range",
    "block_active_flag",
]

SPARSE_FEATURES = [
    "opt_atm_iv_avg", "opt_iv_skew_otm", "opt_iv_term_slope",
    "opt_pcr_vol", "opt_vega_weighted_iv",
    "vwap_dev_open", "vwap_dev_r5", "vol_concentration",
    "vwap_cross_count", "vwap_time_above_pct", "vwap_dev_am", "vol_vwap_corr_5d",
    "open_drive_flag", "late_reversal_flag", "overnight_gap", "gap_fill_flag", "am_range",
    "premarket_ret", "premarket_vol_ratio",
    "sector_dispersion_r5",
]

GBM_FEATURES = [f for f in SPY_FEATURES if f not in ("XLF_ret", "XLI_ret", "XLY_ret")]

GBM_5D_FEATURES = sorted(set(GBM_FEATURES) | {
    "spy_vol_r5", "spy_vol_r10", "spy_vol_r21", "spy_vol_r63",
    "rv_21",
    "vix_term_slope",
    "gex_b",
    "sector_dispersion_r5",
    "premarket_ret",
    "premarket_vol_ratio",
    "spy_rsi_14",
    "spy_ret_r63",
    "spy_bbpct_20",
    "uso_ret_5d",
    "vix_z252",
    "days_to_cpi",
})


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


# ── Load data ─────────────────────────────────────────────────────────────────
print("=" * 70)
print("LOADING spy_features.csv")
print("=" * 70)
df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
print(f"  Total rows: {len(df)}, date range: {df['date'].min().date()} to {df['date'].max().date()}")
print(f"  Total columns: {len(df.columns)}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: Spearman rho of every feature vs next_ret_5d
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SECTION 1: Spearman rho — all features vs next_ret_5d")
print("=" * 70)

target_col = "next_ret_5d"
df_valid = df.dropna(subset=[target_col])
print(f"  Rows with valid next_ret_5d: {len(df_valid)}")

rho_results = {}
skipped = []
for col in df.columns:
    if col in ("date", target_col):
        continue
    sub = df_valid[[col, target_col]].dropna()
    if len(sub) < 50:
        skipped.append(col)
        continue
    try:
        rho, pval = stats.spearmanr(sub[col], sub[target_col])
        rho_results[col] = (rho, pval)
    except Exception as e:
        skipped.append(col)

rho_series = pd.Series({k: v[0] for k, v in rho_results.items()})
rho_abs    = rho_series.abs().sort_values(ascending=False)

print(f"\n  Features computed: {len(rho_results)}, skipped (< 50 rows): {len(skipped)}")
print()

print("  ── TOP 20 by |rho| vs next_ret_5d ──")
print(f"  {'Feature':<35} {'rho':>8}  {'|rho|':>7}  {'p-val':>10}")
print("  " + "-" * 65)
for feat in rho_abs.head(20).index:
    rho, pval = rho_results[feat]
    print(f"  {feat:<35} {rho:>+8.4f}  {abs(rho):>7.4f}  {pval:>10.2e}")

print()
print("  ── BOTTOM 20 by |rho| vs next_ret_5d (weakest signal) ──")
print(f"  {'Feature':<35} {'rho':>8}  {'|rho|':>7}  {'p-val':>10}")
print("  " + "-" * 65)
for feat in rho_abs.tail(20).index:
    rho, pval = rho_results[feat]
    print(f"  {feat:<35} {rho:>+8.4f}  {abs(rho):>7.4f}  {pval:>10.2e}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: Row survival — GBM_FEATURES vs GBM_5D_FEATURES after prep_df
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SECTION 2: Row survival after prep_df — GBM_FEATURES vs GBM_5D_FEATURES")
print("=" * 70)

TARGET_5D  = "next_ret_5d"
TARGET_1D  = "next_ret_1d"

# Check GBM_FEATURES (1d target)
df_gbm, avail_gbm = prep_df(df, GBM_FEATURES, TARGET_1D)
print(f"\n  GBM_FEATURES (target=next_ret_1d):")
print(f"    features in df:          {len(avail_gbm)}")
print(f"    rows after prep_df:      {len(df_gbm)}")
print(f"    date range:              {df_gbm['date'].min().date()} to {df_gbm['date'].max().date()}")

# Check GBM_5D_FEATURES (5d target)
df_5d, avail_5d = prep_df(df, GBM_5D_FEATURES, TARGET_5D)
print(f"\n  GBM_5D_FEATURES (target=next_ret_5d):")
print(f"    features in df:          {len(avail_5d)}")
print(f"    rows after prep_df:      {len(df_5d)}")
print(f"    date range:              {df_5d['date'].min().date()} to {df_5d['date'].max().date()}")

print(f"\n  Row difference (1d vs 5d): {len(df_gbm) - len(df_5d)}")

# Show which 5d-specific features are NOT in GBM_FEATURES
extra_5d_feats = sorted(set(GBM_5D_FEATURES) - set(GBM_FEATURES))
print(f"\n  Extra features in GBM_5D_FEATURES (not in GBM_FEATURES): {len(extra_5d_feats)}")
for f in extra_5d_feats:
    in_df   = f in df.columns
    is_sparse = f in SPARSE_FEATURES
    if in_df:
        nan_count = df[f].isna().sum()
        nan_pct   = 100 * nan_count / len(df)
        print(f"    {f:<30} in_df={in_df}  sparse={is_sparse}  NaN={nan_count} ({nan_pct:.1f}%)")
    else:
        print(f"    {f:<30} in_df={in_df}  NOT IN CSV")

# Drill into which core (non-sparse) 5d features cause NaN drops
core_5d = [f for f in avail_5d if f not in SPARSE_FEATURES]
df_5d_imputed = df.copy()
for col in SPARSE_FEATURES:
    if col in df_5d_imputed.columns:
        med = df_5d_imputed[col].median()
        df_5d_imputed[col] = df_5d_imputed[col].fillna(med if pd.notna(med) else 0.0)
nan_counts_core = df_5d_imputed[core_5d + [TARGET_5D]].isna().sum()
nan_counts_nonzero = nan_counts_core[nan_counts_core > 0]
print(f"\n  Core (non-sparse) 5d features with NaN (causing row drops):")
if len(nan_counts_nonzero) == 0:
    print("    None — all core features are complete")
else:
    for feat, cnt in nan_counts_nonzero.items():
        print(f"    {feat:<35} NaN count: {cnt} ({100*cnt/len(df):.1f}%)")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: Feature importances from latest 5d pkl
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SECTION 3: Feature importances — latest spy_gbm_next_ret_5d_*.pkl")
print("=" * 70)

pkl_pattern = os.path.join(MODEL_DIR, "spy_gbm_next_ret_5d_*.pkl")
pkl_files   = sorted(glob.glob(pkl_pattern))
if not pkl_files:
    print("  ERROR: No matching pkl files found!")
else:
    latest_pkl = pkl_files[-1]
    print(f"\n  Loading: {os.path.basename(latest_pkl)}")
    with open(latest_pkl, "rb") as f:
        obj = pickle.load(f)

    print(f"  Keys in pkl: {list(obj.keys())}")
    print(f"  trained_on:  {obj.get('trained_on', 'N/A')}")

    model    = obj["model"]
    features = obj.get("features", [])
    print(f"  Number of features in pkl: {len(features)}")

    inner = model.named_steps.get("m")
    if hasattr(inner, "feature_importances_"):
        imps = inner.feature_importances_
        idx  = np.argsort(imps)[::-1]
        print(f"\n  Feature importances (all {len(features)}, sorted descending):")
        print(f"  {'Rank':<6} {'Feature':<35} {'Importance':>12}  {'Cumulative':>12}")
        print("  " + "-" * 70)
        cumsum = 0.0
        for rank, i in enumerate(idx, 1):
            cumsum += imps[i]
            print(f"  {rank:<6} {features[i]:<35} {imps[i]:>12.5f}  {cumsum:>11.4f}%"
                  .replace("  %", "%"))
            # Reformat — just print cleanly
        print()
        # Clean reprint
        print(f"  {'Rank':<6} {'Feature':<35} {'Importance':>12}  {'CumSum':>10}")
        print("  " + "-" * 68)
        cumsum = 0.0
        for rank, i in enumerate(idx, 1):
            cumsum += imps[i]
            print(f"  {rank:<6} {features[i]:<35} {imps[i]:>12.6f}  {cumsum:>10.4f}")
    else:
        print("  Model does not have feature_importances_")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: Regime distribution in val set
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SECTION 4: Regime distribution in val set (last 252 rows, next_dir_1d not NaN)")
print("=" * 70)

df_labeled = df.dropna(subset=["next_dir_1d"]).reset_index(drop=True)
val_set    = df_labeled.tail(252).copy()
print(f"\n  df rows with non-NaN next_dir_1d:  {len(df_labeled)}")
print(f"  Val set rows (last 252):           {len(val_set)}")
print(f"  Val set date range:                {val_set['date'].min().date()} to {val_set['date'].max().date()}")

if "regime" in val_set.columns:
    regime_counts = val_set["regime"].value_counts()
    print(f"\n  Regime distribution:")
    for regime, count in regime_counts.items():
        pct = 100 * count / len(val_set)
        print(f"    {regime:<15} {count:>5} rows  ({pct:.1f}%)")
else:
    print("\n  'regime' column not found in spy_features.csv")
    print("  Checking for regime-related columns...")
    regime_cols = [c for c in df.columns if "regime" in c.lower() or "bull" in c.lower() or "bear" in c.lower()]
    print(f"  Regime-related columns: {regime_cols}")

    # Try to infer from available signal
    if "vol_regime" in val_set.columns:
        print(f"\n  vol_regime distribution in val set:")
        vc = val_set["vol_regime"].value_counts().sort_index()
        for v, c in vc.items():
            pct = 100 * c / len(val_set)
            print(f"    vol_regime={v:<6} {c:>5} rows  ({pct:.1f}%)")

    # Check if there's a spy_regime or similar
    if "spy_regime" in val_set.columns:
        print(f"\n  spy_regime distribution in val set:")
        vc = val_set["spy_regime"].value_counts()
        for v, c in vc.items():
            pct = 100 * c / len(val_set)
            print(f"    {v:<15} {c:>5} rows  ({pct:.1f}%)")

print()
# Also look at what columns ARE available for regime
all_cols = sorted(df.columns.tolist())
regime_like = [c for c in all_cols if any(x in c.lower() for x in ["regime", "bull", "bear", "chop"])]
print(f"  All regime-like column names in spy_features.csv: {regime_like}")

# If regime logistic models exist, use their prediction to infer label
# The regime pkl files are bull/bear/chop logistic models
# Check the val set based on next_ret_5d sign and vol
if "next_ret_5d" in val_set.columns and "vix_level" in val_set.columns:
    print("\n  Approximate regime classification based on vol_regime + next_ret_5d sign:")
    vol_med = df["vix_level"].median()
    val_set["approx_regime"] = "chop"
    val_set.loc[(val_set["next_ret_5d"] > 0) & (val_set["vix_level"] < vol_med), "approx_regime"] = "bull"
    val_set.loc[(val_set["next_ret_5d"] < 0) & (val_set["vix_level"] > vol_med), "approx_regime"] = "bear"
    vc = val_set["approx_regime"].value_counts()
    for v, c in vc.items():
        pct = 100 * c / len(val_set)
        print(f"    {v:<15} {c:>5} rows  ({pct:.1f}%)")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: GBM_5D_FEATURES list and SPARSE overlap
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SECTION 5: GBM_5D_FEATURES — full list + SPARSE_FEATURES overlap")
print("=" * 70)

print(f"\n  GBM_5D_FEATURES has {len(GBM_5D_FEATURES)} features (sorted alphabetically as defined):")
print()
in_sparse  = [f for f in GBM_5D_FEATURES if f in SPARSE_FEATURES]
not_sparse = [f for f in GBM_5D_FEATURES if f not in SPARSE_FEATURES]

for i, f in enumerate(GBM_5D_FEATURES, 1):
    sparse_tag = "  *** SPARSE ***" if f in SPARSE_FEATURES else ""
    in_csv_tag = "" if f in df.columns else "  [NOT IN CSV]"
    print(f"  {i:>3}. {f:<35}{sparse_tag}{in_csv_tag}")

print()
print(f"  Summary:")
print(f"    Total GBM_5D_FEATURES:          {len(GBM_5D_FEATURES)}")
print(f"    In SPARSE_FEATURES:             {len(in_sparse)}")
print(f"    Not in SPARSE_FEATURES (core):  {len(not_sparse)}")
print()
print(f"  Features in GBM_5D_FEATURES ∩ SPARSE_FEATURES:")
for f in in_sparse:
    nan_count = df[f].isna().sum() if f in df.columns else "N/A"
    nan_pct   = f"{100*nan_count/len(df):.1f}%" if isinstance(nan_count, (int, np.integer)) else "N/A"
    print(f"    {f:<35} NaN: {nan_count} ({nan_pct})")

print()
print(f"  Features in GBM_5D_FEATURES added vs GBM_FEATURES (5d-specific):")
for f in sorted(set(GBM_5D_FEATURES) - set(GBM_FEATURES)):
    sparse_tag = "SPARSE" if f in SPARSE_FEATURES else "core"
    in_csv_tag = "in_csv" if f in df.columns else "NOT IN CSV"
    nan_count  = df[f].isna().sum() if f in df.columns else "N/A"
    nan_pct    = f"{100*nan_count/len(df):.1f}%" if isinstance(nan_count, (int, np.integer)) else "N/A"
    print(f"    {f:<35} [{sparse_tag}]  [{in_csv_tag}]  NaN: {nan_count} ({nan_pct})")

print()
print("=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
