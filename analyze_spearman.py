import pandas as pd
import numpy as np
from scipy.stats import spearmanr

# Load data
df = pd.read_csv('/home/jhcv/discordBot/outputs/features/markets/spy_features.csv')

# Parse date column
date_cols = [c for c in df.columns if 'date' in c.lower()]
if date_cols:
    df[date_cols[0]] = pd.to_datetime(df[date_cols[0]])
    df = df.set_index(date_cols[0])

print(f"Loaded {len(df)} rows, {len(df.columns)} columns")
print("Columns:", list(df.columns))

# Drop rows where next_dir_1d is NaN
df = df.dropna(subset=['next_dir_1d'])
print(f"After dropping NaN next_dir_1d: {len(df)} rows")

# Define columns to exclude (non-features)
EXCLUDE_COLS = {
    'SPY_ret', 'QQQ_ret', 'GLD_ret', 'USO_ret', 'VIX', 'T10Y2Y', 
    'DXY_ret', 'FEDFUNDS', 'next_ret_1d', 'next_ret_5d', 'next_dir_1d'
}

# Also exclude any column named 'date' or similar if still present
feature_cols = [c for c in df.columns if c not in EXCLUDE_COLS]
print(f"\nFeature columns: {len(feature_cols)}")

# SPARSE_FEATURES set
SPARSE_FEATURES = {
    'opt_atm_iv_avg', 'opt_iv_skew_otm', 'opt_iv_term_slope', 'opt_pcr_vol', 'opt_vega_weighted_iv',
    'XLB_ret','XLC_ret','XLE_ret','XLF_ret','XLI_ret','XLK_ret','XLP_ret','XLRE_ret','XLU_ret','XLV_ret','XLY_ret',
    'sector_risk_on_r5','sector_risk_off_r5','sector_rotation_r5','xlf_spy_rs_5d','xle_spy_rs_5d',
    'sector_dispersion_1d','sector_dispersion_r5',
    'vwap_dev_close','vwap_dev_open','vwap_cross_count','vwap_time_above_pct',
    'high_vol_above_vwap','vol_concentration','vwap_dev_z21','vwap_dev_r5','vol_vwap_corr_5d',
    'block_active_flag','block_dollar_flow_5d','block_net_direction_5d','block_dev_mean_5d',
    'block_dark_pool_5d','days_since_last_block','block_highdev_5d',
    'first_hour_ret','last_hour_ret','am_range','pm_range',
    'gap_fill_flag','vwap_dev_am','open_drive_flag','vol_am_pct',
    'late_reversal_flag','premarket_ret','premarket_vol_ratio','overnight_gap',
}

results = []

for col in feature_cols:
    series = df[col]
    target_dir = df['next_dir_1d']
    
    # Get rows where both feature and target are non-NaN
    mask = series.notna() & target_dir.notna()
    n = mask.sum()
    coverage = n / len(df) * 100
    
    if n < 10:
        results.append({
            'feature': col,
            'spearman_vs_dir': np.nan,
            'pval_dir': np.nan,
            'spearman_vs_5d': np.nan,
            'pval_5d': np.nan,
            'is_sparse': col in SPARSE_FEATURES,
            'coverage_pct': coverage,
            'n': n
        })
        continue
    
    rho_dir, pval_dir = spearmanr(series[mask], target_dir[mask])
    
    # vs next_ret_5d
    if 'next_ret_5d' in df.columns:
        target_5d = df['next_ret_5d']
        mask5 = series.notna() & target_5d.notna()
        if mask5.sum() >= 10:
            rho_5d, pval_5d = spearmanr(series[mask5], target_5d[mask5])
        else:
            rho_5d, pval_5d = np.nan, np.nan
    else:
        rho_5d, pval_5d = np.nan, np.nan
    
    results.append({
        'feature': col,
        'spearman_vs_dir': rho_dir,
        'pval_dir': pval_dir,
        'spearman_vs_5d': rho_5d,
        'pval_5d': pval_5d,
        'is_sparse': col in SPARSE_FEATURES,
        'coverage_pct': round(coverage, 1),
        'n': n
    })

results_df = pd.DataFrame(results)
results_df['abs_rho_dir'] = results_df['spearman_vs_dir'].abs()
results_df = results_df.sort_values('abs_rho_dir', ascending=False).reset_index(drop=True)
results_df['weak_signal'] = results_df['abs_rho_dir'] < 0.02

# Save to CSV
out_path = '/home/jhcv/discordBot/outputs/spearman_feature_correlations.csv'
results_df.to_csv(out_path, index=False)
print(f"\nSaved to {out_path}")

# Print full table
pd.set_option('display.max_rows', 200)
pd.set_option('display.float_format', '{:.4f}'.format)
pd.set_option('display.max_colwidth', 40)
pd.set_option('display.width', 160)

print("\n" + "="*120)
print("SPEARMAN CORRELATIONS - sorted by |rho| vs next_dir_1d")
print("="*120)
display_cols = ['feature', 'spearman_vs_dir', 'pval_dir', 'spearman_vs_5d', 'pval_5d', 'is_sparse', 'coverage_pct', 'weak_signal']
print(results_df[display_cols].to_string(index=True))

print("\n--- SPARSE features present in the dataset ---")
sparse_in_data = results_df[results_df['is_sparse']]
print(sparse_in_data[display_cols].to_string(index=False))

print("\n--- Top 20 features by |rho| vs next_dir_1d ---")
print(results_df[display_cols].head(20).to_string(index=False))

print("\n--- Features flagged as WEAK (|rho| < 0.02) ---")
weak = results_df[results_df['weak_signal']]
print(f"Count: {len(weak)}")
print(weak['feature'].tolist())
