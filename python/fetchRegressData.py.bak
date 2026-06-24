#!/usr/bin/env python3
# fetchRegressData.py - assemble regression dataset from regressCache
#
# Usage:
#   python3 fetchRegressData.py <target_type> <target_symbol> <var_keys> <lookback> <timeframe> [lags]
#
#   target_type  : stocks | crypto
#   target_symbol: e.g. BTC, AAPL, ETH
#   var_keys     : comma-separated regressor keys (e.g. SPY,VIX,EUR_USD,HASHRATE)
#   lookback     : 1yr | 2yr | 3yr | 5yr | max
#   timeframe    : 1d (ignored - always daily)
#   lags         : none | ar | short | medium | full  (default: none)
#                  none  - no lags
#                  ar    - lag TARGET ONLY at 1,2,5,10,15,30,90,126,252 days
#                  short - lag ALL cols at 1,2,3,5 days
#                  medium- lag ALL cols at 1,2,3,5,10,15,30,90 days
#                  full  - lag ALL cols at 1,2,3,5,10,15,30,90,126,252 days
#
# Level variables (_val: VIX, M2, UNRATE, CPI, T10Y2Y, FEDFUNDS, WTI, DXY, GOLD, HASHRATE)
# are converted to 1-day first-differences before any lagging so we are always
# studying deltas, not levels.
#
# Prints JSON metadata to stdout (last line).

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from regressCache import fetch_regressor, fetch_target, col_name, REGRESSORS

if len(sys.argv) < 6:
    print("ERROR: Usage: fetchRegressData.py <target_type> <target_symbol> <var_keys> <lookback> <timeframe> [lags]",
          file=sys.stderr)
    sys.exit(1)

target_type   = sys.argv[1].lower()
target_symbol = sys.argv[2].upper()
var_keys_str  = sys.argv[3]
lookback      = sys.argv[4].lower()
lag_preset    = sys.argv[6].lower() if len(sys.argv) > 6 else "none"

# ── lag presets ────────────────────────────────────────────────────────────────
LAG_PRESETS = {
    "none":   {"target_only": False, "lags": []},
    "ar":     {"target_only": True,  "lags": [1, 2, 5, 10, 15, 30, 90, 126, 252]},
    "short":  {"target_only": False, "lags": [1, 2, 3, 5]},
    "medium": {"target_only": False, "lags": [1, 2, 3, 5, 10, 15, 30, 90]},
    "full":   {"target_only": False, "lags": [1, 2, 3, 5, 10, 15, 30, 90, 126, 252]},
}

if lag_preset not in LAG_PRESETS:
    print(f"WARNING: unknown lag preset '{lag_preset}', defaulting to 'none'", file=sys.stderr)
    lag_preset = "none"

lag_cfg    = LAG_PRESETS[lag_preset]
lag_depths = lag_cfg["lags"]
ar_only    = lag_cfg["target_only"]
max_lag    = max(lag_depths) if lag_depths else 0

# ── lookback ───────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = {"1yr": 365, "2yr": 730, "3yr": 1095, "5yr": 1825, "max": 9999}
lookback_days = LOOKBACK_DAYS.get(lookback, 730)
# pull extra history to cover the lag window + 1 extra day for diffing
fetch_extra  = max_lag + 30 + 1
start_date   = date(2010, 1, 1) if lookback == "max" else date.today() - timedelta(days=lookback_days + fetch_extra)
cutoff_date  = date(2010, 1, 1) if lookback == "max" else date.today() - timedelta(days=lookback_days)

OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets/")
os.makedirs(OUT_DIR, exist_ok=True)

# ── helper: first-difference a level column ────────────────────────────────────
def to_delta(df, col):
    """
    Replace a _val level column with its 1-day absolute change (_chg).
    Returns df with the column renamed and differenced.
    """
    new_col = col.replace("_val", "_chg")
    df = df.copy()
    df[new_col] = df[col].diff()          # absolute change: x_t - x_{t-1}
    df = df.drop(columns=[col])
    return df, new_col

# ── fetch target ───────────────────────────────────────────────────────────────
print(f"[fetchRegressData] target={target_type}:{target_symbol} lookback={lookback} lags={lag_preset}", file=sys.stderr)
try:
    df_target = fetch_target(target_type, target_symbol)
except Exception as e:
    print(f"ERROR: could not fetch target {target_symbol}: {e}", file=sys.stderr)
    sys.exit(1)

df_target["date"] = pd.to_datetime(df_target["date"]).dt.date
df_target = df_target[df_target["date"] >= start_date].reset_index(drop=True)
target_col = df_target.columns[-1]   # always a _ret column - no diffing needed
print(f"[fetchRegressData] target rows={len(df_target)}", file=sys.stderr)

# ── fetch regressors ────────────────────────────────────────────────────────────
var_keys = [k.strip().upper() for k in var_keys_str.split(",") if k.strip()]
frames   = [df_target]
reg_cols = []

for key in var_keys:
    if key not in REGRESSORS:
        print(f"WARNING: unknown key '{key}', skipping", file=sys.stderr)
        continue
    print(f"[fetchRegressData] fetching {key}", file=sys.stderr)
    try:
        df_r = fetch_regressor(key, verbose=True)
    except Exception as e:
        print(f"WARNING: could not fetch {key}: {e} - skipping", file=sys.stderr)
        continue

    df_r["date"] = pd.to_datetime(df_r["date"]).dt.date
    df_r = df_r[df_r["date"] >= start_date].reset_index(drop=True)
    col  = df_r.columns[-1]

    # convert level variables to first-differences
    if col.endswith("_val"):
        df_r, col = to_delta(df_r, col)
        print(f"[fetchRegressData] {key}: converted level -> delta ({col})", file=sys.stderr)

    frames.append(df_r)
    reg_cols.append(col)

if not reg_cols:
    print("ERROR: no valid regressors", file=sys.stderr)
    sys.exit(1)

# ── align contemporaneous data ─────────────────────────────────────────────────
merged = frames[0]
for fr in frames[1:]:
    merged = merged.merge(fr, on="date", how="inner")

merged = merged.sort_values("date").reset_index(drop=True)

# ── add lag columns ────────────────────────────────────────────────────────────
# Decide which contemporaneous columns get lagged
if lag_preset == "none" or not lag_depths:
    cols_to_lag = []
elif ar_only:
    cols_to_lag = [target_col]          # AR only: just the target
else:
    cols_to_lag = [target_col] + reg_cols   # ADL: target + all regressors

lag_col_names = []
for col in cols_to_lag:
    for lag in lag_depths:
        lag_col = f"{col}_lag{lag}"
        merged[lag_col] = merged[col].shift(lag)
        lag_col_names.append(lag_col)

if lag_col_names:
    print(f"[fetchRegressData] added {len(lag_col_names)} lag cols (preset={lag_preset}, max_lag={max_lag})", file=sys.stderr)

# ── drop NAs (from diff + lags) and apply lookback cutoff ─────────────────────
merged = merged.dropna().reset_index(drop=True)
merged = merged[merged["date"] >= cutoff_date].reset_index(drop=True)

n_obs     = len(merged)
n_params  = 1 + len(reg_cols) + len(lag_col_names)   # intercept + contemp + lags
obs_ratio = n_obs / n_params if n_params > 0 else 999

print(f"[fetchRegressData] aligned rows={n_obs} params={n_params} ratio={obs_ratio:.1f}", file=sys.stderr)

if n_obs < 20:
    print(f"ERROR: only {n_obs} overlapping obs - need at least 20", file=sys.stderr)
    sys.exit(1)

if obs_ratio < 5:
    print(f"WARNING: obs/params={obs_ratio:.1f} (<5) - high overfit risk. Use longer lookback or fewer lags.", file=sys.stderr)

# ── write CSV ──────────────────────────────────────────────────────────────────
safe_sym = target_symbol.replace("/", "")
out_path = os.path.join(OUT_DIR, f"regress_{safe_sym}_{lookback}.csv")
merged.to_csv(out_path, index=False)

all_reg_cols = reg_cols + lag_col_names

meta = {
    "target_col":       target_col,
    "regressor_cols":   all_reg_cols,
    "regressor_labels": [REGRESSORS[k]["label"] for k in var_keys if k in REGRESSORS],
    "n_obs":            n_obs,
    "n_params":         n_params,
    "obs_ratio":        round(obs_ratio, 1),
    "lag_preset":       lag_preset,
    "lag_depths":       lag_depths,
    "date_start":       str(merged["date"].iloc[0]),
    "date_end":         str(merged["date"].iloc[-1]),
    "out_path":         out_path,
    "target_symbol":    target_symbol,
}
print(json.dumps(meta))
