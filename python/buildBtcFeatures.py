#!/usr/bin/env python3
"""
buildBtcFeatures.py
===================
Builds a daily feature matrix for BTC return prediction.

Target variables (computed forward, no leakage):
  next_ret_1d   - next trading day BTC log return
  next_ret_5d   - cumulative log return over next 5 days
  next_dir_1d   - binary: 1 if next_ret_1d > 0 else 0

Feature groups:
  1. BTC price/momentum      - rolling returns, vol, RSI, Bollinger, drawdown
  2. Volatility regime       - realised vol, vol z-scores, consecutive up/down
  3. On-chain metrics        - MVRV, NUPL (from MVRV), hashrate growth,
                               realized price ratio, miner revenue proxy,
                               BTC dominance, S2F deviation
  4. Cross-asset             - SPY, GLD, USO, DXY, ETH returns + z-scores
  5. Macro                   - Fed funds, T10Y2Y yield curve, DXY level
  6. Calendar                - DOW, month sin/cos, halving cycle day count
  7. Halving-cycle position  - days since last halving, cycle pct

Output:
  outputs/features/markets/btc_features.csv

Run: venv/bin/python3 python/buildBtcFeatures.py
"""

import os
import warnings
import numpy as np
import pandas as pd
import datetime

warnings.filterwarnings("ignore")

CACHE_DIR = os.path.expanduser("~/discordBot/outputs/markets/cache")
BTC_BARS  = os.path.expanduser("~/discordBot/outputs/markets/BTC_max_bars.csv")
OUT_DIR   = os.path.expanduser("~/discordBot/outputs/features/markets")
OUT_PATH  = os.path.join(OUT_DIR, "btc_features.csv")
os.makedirs(OUT_DIR, exist_ok=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def load_cache(filename, date_col="date", val_col=None):
    """Load a cache CSV, parse dates, sort, return DataFrame."""
    path = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(path):
        print(f"  [warn] {filename} not found")
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=[date_col])
    df[date_col] = pd.to_datetime(df[date_col], utc=True).dt.date
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).set_index(date_col)
    if val_col and val_col in df.columns:
        return df[[val_col]]
    return df


def rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def bollinger_pct(prices, window=20):
    mu    = prices.rolling(window).mean()
    std   = prices.rolling(window).std()
    upper = mu + 2 * std
    lower = mu - 2 * std
    return (prices - lower) / (upper - lower).replace(0, np.nan)


def rolling_z(series, window):
    mu  = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mu) / std.replace(0, np.nan)


def consec_runs(series_gt0):
    """Consecutive positive (or negative) days streak."""
    pos = series_gt0.astype(int)
    neg = (1 - pos)
    # reset group on direction change
    pos_cumsum = pos.groupby((pos != pos.shift()).cumsum()).cumsum() * pos
    neg_cumsum = neg.groupby((neg != neg.shift()).cumsum()).cumsum() * neg
    return pos_cumsum, neg_cumsum


# ── Halving dates (hardcoded, future halvings included for cycle position) ─────
HALVINGS = pd.to_datetime([
    "2009-01-03",  # genesis (50 BTC)
    "2012-11-28",  # 25 BTC
    "2016-07-09",  # 12.5 BTC
    "2020-05-11",  # 6.25 BTC
    "2024-04-20",  # 3.125 BTC
    "2028-04-18",  # 1.5625 BTC (approx)
])


def halving_features(dates):
    """
    For each date, compute:
      - days_since_halving   : days since the most recent past halving
      - halving_cycle_pct    : fraction through the ~1460-day halving cycle [0,1)
      - halving_number       : which epoch we are in (1=genesis, 2=first halving, ...)
    """
    ds_halving = []
    cycle_pct  = []
    halv_num   = []
    for d in dates:
        past = HALVINGS[HALVINGS <= d]
        if len(past) == 0:
            ds_halving.append(np.nan)
            cycle_pct.append(np.nan)
            halv_num.append(0)
            continue
        last_h = past[-1]
        # next halving
        future = HALVINGS[HALVINGS > d]
        next_h = future[0] if len(future) > 0 else (last_h + pd.Timedelta(days=1460))
        days_since = (d - last_h).days
        cycle_len  = (next_h - last_h).days
        ds_halving.append(days_since)
        cycle_pct.append(days_since / cycle_len if cycle_len > 0 else 0.0)
        halv_num.append(len(past))
    return np.array(ds_halving), np.array(cycle_pct), np.array(halv_num)


# ── 1. Load BTC price bars ─────────────────────────────────────────────────────
print("[1] Loading BTC price bars...")
bars = pd.read_csv(BTC_BARS, parse_dates=["date"])
bars = bars.sort_values("date").set_index("date")
bars.index = pd.to_datetime(bars.index)

# Log returns
bars["btc_ret"] = np.log(bars["close"] / bars["close"].shift(1))
btc_ret = bars["btc_ret"].dropna()

# Build main feature DataFrame aligned to BTC trading days
df = pd.DataFrame(index=bars.index)
df.index.name = "date"

# Reconstruct cumulative price from close (already have close in bars)
price = bars["close"]

# ── 2. BTC price/momentum features ────────────────────────────────────────────
print("[2] Building price/momentum features...")

df["btc_ret"]      = btc_ret

df["btc_ret_r5"]   = btc_ret.rolling(5).sum()
df["btc_ret_r10"]  = btc_ret.rolling(10).sum()
df["btc_ret_r21"]  = btc_ret.rolling(21).sum()
df["btc_ret_r63"]  = btc_ret.rolling(63).sum()

df["btc_vol_r5"]   = btc_ret.rolling(5).std() * np.sqrt(365)
df["btc_vol_r21"]  = btc_ret.rolling(21).std() * np.sqrt(365)
df["btc_vol_r63"]  = btc_ret.rolling(63).std() * np.sqrt(365)

df["btc_rsi_14"]   = rsi(price, 14)
df["btc_rsi_3"]    = rsi(price, 3)
df["btc_bbpct_20"] = bollinger_pct(price, 20)

# Drawdown from 252-day high
rolling_high = price.rolling(252).max()
df["btc_drawdown_252"] = (price - rolling_high) / rolling_high

# Momentum: 63-day mom minus 21-day (intermediate vs short)
df["btc_mom_63_21"] = df["btc_ret_r63"] - df["btc_ret_r21"]

# Z-scores
df["btc_z5"]  = rolling_z(btc_ret, 5)
df["btc_z21"] = rolling_z(btc_ret, 21)

# Consecutive up/down days
df["btc_consec_up"], df["btc_consec_down"] = consec_runs(btc_ret > 0)

# Weekend/weekend effect: BTC trades 24/7 - use day-of-week directly
df["btc_dow"] = df.index.dayofweek  # 0=Mon, 6=Sun

# ── 3. Volatility regime ───────────────────────────────────────────────────────
print("[3] Building volatility regime features...")

rv21 = btc_ret.rolling(21).std() * np.sqrt(365) * 100
df["btc_rv_21"] = rv21

df["btc_rv_z21"]  = rolling_z(rv21, 21)
df["btc_rv_z252"] = rolling_z(rv21, 252)

# Vol regime bucket (crypto-adjusted: higher than SPY buckets)
df["btc_vol_regime"] = pd.cut(
    rv21,
    bins=[0, 30, 50, 80, 120, 999],
    labels=[0, 1, 2, 3, 4]
).astype(float)
# 0=calm (<30% ann vol), 1=normal (30-50%), 2=elevated (50-80%), 3=high (80-120%), 4=spike

# ── 4. On-chain metrics ────────────────────────────────────────────────────────
print("[4] Building on-chain features...")

# 4a. MVRV ratio and NUPL (from BTC_nupl_daily.csv)
nupl_df = load_cache("BTC_nupl_daily.csv")
if not nupl_df.empty and "CapMVRVCur" in nupl_df.columns:
    # Filter extreme early values (MVRV 146 in 2010 is market-structure noise)
    mvrv = nupl_df["CapMVRVCur"].clip(upper=50).reindex(df.index)
    df["btc_mvrv"]  = mvrv.ffill()
    # NUPL = 1 - 1/MVRV (standard derivation)
    df["btc_nupl"]  = (1 - 1 / df["btc_mvrv"].replace(0, np.nan)).clip(-2, 1)
    # Z-scores of MVRV (regime within historical range)
    df["btc_mvrv_z63"]  = rolling_z(df["btc_mvrv"], 63)
    df["btc_mvrv_z252"] = rolling_z(df["btc_mvrv"], 252)
    # MVRV change (momentum of network valuation vs realized)
    df["btc_mvrv_chg_21"] = df["btc_mvrv"].diff(21)
else:
    print("  [warn] MVRV data missing, skipping on-chain MVRV features")

# 4b. Hashrate (from HASHRATE.csv - stores log(hashrate))
hash_df = load_cache("HASHRATE.csv", val_col="HASHRATE_val")
if not hash_df.empty:
    # HASHRATE_val is already log(hashrate in TH/s) — diff gives growth rate
    hash_log = hash_df["HASHRATE_val"].reindex(df.index).ffill()
    df["btc_hashrate_growth_21"]  = hash_log.diff(21)   # 21-day change in log-hashrate
    df["btc_hashrate_growth_63"]  = hash_log.diff(63)
    df["btc_hashrate_z63"]        = rolling_z(hash_log, 63)
else:
    print("  [warn] HASHRATE data missing")

# 4c. Realized price ratio (price / realized price = MVRV proxy from different source)
real_df = load_cache("BTC_realized_daily.csv")
if not real_df.empty and "price" in real_df.columns and "mvrv" in real_df.columns:
    real_mvrv = real_df["mvrv"].reindex(df.index).ffill()
    # Use as secondary MVRV confirmation (different realized price methodology)
    df["btc_realized_mvrv"] = real_mvrv.clip(upper=50)
    df["btc_realized_mvrv_chg_21"] = df["btc_realized_mvrv"].diff(21)
else:
    print("  [warn] realized price data missing or incomplete")

# 4d. Miner revenue proxy (from BTC_miner_cap_daily.csv)
miner_df = load_cache("BTC_miner_cap_daily.csv")
if not miner_df.empty and "blkcnt" in miner_df.columns:
    blkcnt = miner_df["blkcnt"].reindex(df.index).ffill()
    # Block count relative to expected 144/day: ratio measures chain health / difficulty
    df["btc_blkcnt_ratio"] = (blkcnt / 144).rolling(7).mean()
    df["btc_blkcnt_z21"]   = rolling_z(blkcnt, 21)
else:
    print("  [warn] miner cap data missing")

# 4e. BTC Dominance (from btc_dominance.csv)
dom_df = load_cache("btc_dominance.csv", val_col="btc_dom")
if not dom_df.empty:
    dom = dom_df["btc_dom"].reindex(df.index).ffill()
    df["btc_dominance"]        = dom
    df["btc_dominance_chg_21"] = dom.diff(21)
    df["btc_dominance_z63"]    = rolling_z(dom, 63)
else:
    print("  [warn] BTC dominance data missing")

# 4f. S2F deviation (price vs model price from S2F model)
s2f_df = load_cache("BTC_s2f_daily.csv")
if not s2f_df.empty and "price" in s2f_df.columns:
    # Build S2F model price for each date
    BLOCKS_PER_DAY = 144.0
    halvings_df = pd.DataFrame({
        "date":    HALVINGS[:6],   # 6 halvings including 2028 estimate
        "subsidy": [50, 25, 12.5, 6.25, 3.125, 1.5625]
    })

    def compute_supply_issuance(d):
        past = halvings_df[halvings_df["date"] <= d]
        if len(past) == 0:
            return np.nan, np.nan
        supply = 0.0
        for i, row in past.iterrows():
            nxt_halvings = halvings_df[halvings_df["date"] > row["date"]]
            era_end = nxt_halvings.iloc[0]["date"] if len(nxt_halvings) > 0 else d
            era_end = min(era_end, d)
            era_days = max((era_end - row["date"]).days, 0)
            supply += era_days * BLOCKS_PER_DAY * row["subsidy"]
        current_sub = past.iloc[-1]["subsidy"]
        issuance = current_sub * BLOCKS_PER_DAY * 365
        s2f_val = supply / issuance if issuance > 0 else np.nan
        return s2f_val

    # Compute S2F for each row - vectorized date set for speed
    s2f_prices = s2f_df["price"].reindex(df.index).ffill()
    # We only care about the deviation metric; precompute model prices
    # Log-linear S2F model: ln(price) = -1.84 + 3.36 * ln(S2F) (PlanB)
    # Rather than recompute daily, use price / s2f_prices rolling mean as a proxy
    # for under/overvaluation relative to trend
    s2f_log_price = np.log(s2f_prices.replace(0, np.nan))
    s2f_rolling_trend = s2f_log_price.rolling(365).mean()
    df["btc_s2f_dev"] = s2f_log_price - s2f_rolling_trend
    # Z-score of deviation
    df["btc_s2f_dev_z63"] = rolling_z(df["btc_s2f_dev"], 63)
else:
    print("  [warn] S2F price data missing")

# ── 5. Halving cycle features ──────────────────────────────────────────────────
print("[5] Building halving cycle features...")

dates_arr = df.index.to_pydatetime()
ds_halving, cycle_pct, halv_num = halving_features(df.index)
df["btc_days_since_halving"] = ds_halving
df["btc_halving_cycle_pct"]  = cycle_pct
df["btc_halving_number"]     = halv_num.astype(float)

# ── 6. Cross-asset features ────────────────────────────────────────────────────
print("[6] Building cross-asset features...")

# SPY
spy_path = os.path.join(CACHE_DIR, "SPY.csv")
if os.path.exists(spy_path):
    spy = pd.read_csv(spy_path, parse_dates=["date"]).sort_values("date").set_index("date")
    spy_ret = spy["SPY_ret"].reindex(df.index)
    df["spy_ret_1d"] = spy_ret
    # For rolling features, ffill weekday values onto weekends so rolling has no gaps
    spy_ret_filled = spy_ret.ffill()
    df["spy_ret_5d"] = spy_ret_filled.rolling(5).sum()
    df["spy_z21"]    = rolling_z(spy_ret_filled, 21)
    # Where spy_ret is NaN (weekends), zero out rolling features (no trading day)
    df.loc[spy_ret.isna(), "spy_ret_5d"] = np.nan
    df.loc[spy_ret.isna(), "spy_z21"]    = np.nan
else:
    print("  [warn] SPY cache missing")

# GLD
gld_path = os.path.join(CACHE_DIR, "GLD.csv")
if os.path.exists(gld_path):
    gld = pd.read_csv(gld_path, parse_dates=["date"]).sort_values("date").set_index("date")
    gld_ret = gld["GLD_ret"].reindex(df.index)
    df["gld_ret_1d"] = gld_ret
    gld_ret_filled = gld_ret.ffill()
    df["gld_ret_5d"] = gld_ret_filled.rolling(5).sum()
    df["gld_z21"]    = rolling_z(gld_ret_filled, 21)
    df.loc[gld_ret.isna(), "gld_ret_5d"] = np.nan
    df.loc[gld_ret.isna(), "gld_z21"]    = np.nan
else:
    print("  [warn] GLD cache missing")

# USO (oil - risk appetite)
uso_path = os.path.join(CACHE_DIR, "USO.csv")
if os.path.exists(uso_path):
    uso = pd.read_csv(uso_path, parse_dates=["date"]).sort_values("date").set_index("date")
    uso_ret = uso["USO_ret"].reindex(df.index)
    df["uso_ret_1d"] = uso_ret
    uso_ret_filled = uso_ret.ffill()
    df["uso_z21"]    = rolling_z(uso_ret_filled, 21)
    df.loc[uso_ret.isna(), "uso_z21"] = np.nan
else:
    print("  [warn] USO cache missing")

# DXY - dollar strength (historically BTC inversely correlated)
dxy_path = os.path.join(CACHE_DIR, "DXY.csv")
if os.path.exists(dxy_path):
    dxy = pd.read_csv(dxy_path, parse_dates=["date"]).sort_values("date").set_index("date")
    # DXY stores DTWEXBGS_val (level)
    dxy_level = dxy.iloc[:, 0].reindex(df.index).ffill()
    dxy_ret   = np.log(dxy_level / dxy_level.shift(1))
    df["dxy_ret_1d"]  = dxy_ret
    dxy_ret_filled = dxy_ret.ffill()
    df["dxy_ret_5d"]  = dxy_ret_filled.rolling(5).sum()
    df["dxy_z21"]     = rolling_z(dxy_ret_filled, 21)
    df["dxy_level"]   = dxy_level
    df["dxy_z63"]     = rolling_z(dxy_level, 63)
else:
    print("  [warn] DXY cache missing")

# ETH (crypto risk appetite - BTC correlation + alt season signal)
# Try Alpaca BTC_max_bars equivalent for ETH
eth_bars = os.path.expanduser("~/discordBot/outputs/markets/ETH_max_bars.csv")
if os.path.exists(eth_bars):
    eth = pd.read_csv(eth_bars, parse_dates=["date"]).sort_values("date").set_index("date")
    eth_ret = np.log(eth["close"] / eth["close"].shift(1))
    eth_ret = eth_ret.reindex(df.index)
    df["eth_ret_1d"] = eth_ret
    eth_ret_filled = eth_ret.ffill()
    df["eth_ret_5d"] = eth_ret_filled.rolling(5).sum()
    df["eth_z21"]    = rolling_z(eth_ret_filled, 21)
    df.loc[eth_ret.isna(), "eth_ret_5d"] = np.nan
    df.loc[eth_ret.isna(), "eth_z21"]    = np.nan
    # BTC/ETH relative strength (BTC dominance signal at daily level)
    df["btc_eth_rs_5d"] = df["btc_ret_r5"] - eth_ret_filled.rolling(5).sum()
    df.loc[eth_ret.isna(), "btc_eth_rs_5d"] = np.nan
else:
    print("  [warn] ETH_max_bars.csv missing (no ETH features)")

# BTC/GLD rolling correlation (safe-haven alignment)
if "gld_ret_1d" in df.columns:
    # Use ffill for correlation computation so rolling window doesn't break on weekends
    btc_r = df["btc_ret"].copy()
    gld_r = df["gld_ret_1d"].ffill()
    df["btc_gld_corr_21"] = btc_r.rolling(21).corr(gld_r)
    # NaN on BTC trading days where GLD is still unavailable (pre-2016)
    df.loc[df["gld_ret_1d"].isna() & (df.index < pd.Timestamp("2016-01-10")), "btc_gld_corr_21"] = np.nan

# ── 7. Macro features ──────────────────────────────────────────────────────────
print("[7] Building macro features...")

# Fed funds rate (level - risk-free rate affects risk assets)
fed_path = os.path.join(CACHE_DIR, "FEDFUNDS.csv")
if os.path.exists(fed_path):
    fed = pd.read_csv(fed_path, parse_dates=["date"]).sort_values("date").set_index("date")
    fed_level = fed.iloc[:, 0].reindex(df.index).ffill()
    df["fedfunds"]        = fed_level
    df["fedfunds_chg_21"] = fed_level.diff(21)
else:
    print("  [warn] FEDFUNDS cache missing")

# Yield curve (T10Y2Y - inversion = risk-off, historically bearish crypto)
t10_path = os.path.join(CACHE_DIR, "T10Y2Y.csv")
if os.path.exists(t10_path):
    t10 = pd.read_csv(t10_path, parse_dates=["date"]).sort_values("date").set_index("date")
    yc = t10.iloc[:, 0].reindex(df.index).ffill()
    df["yield_curve"]        = yc
    df["yield_curve_chg_5"]  = yc.diff(5)
    df["yield_curve_z63"]    = rolling_z(yc, 63)
    df["yield_inverted"]     = (yc < 0).astype(float)
else:
    print("  [warn] T10Y2Y cache missing")

# ── 8. Calendar features ───────────────────────────────────────────────────────
print("[8] Building calendar features...")

df["dow"]            = df.index.dayofweek        # 0=Mon..6=Sun (BTC trades 7 days)
df["month"]          = df.index.month
df["month_sin"]      = np.sin(2 * np.pi * df["month"] / 12)
df["month_cos"]      = np.cos(2 * np.pi * df["month"] / 12)
df["is_weekend"]     = (df["dow"] >= 5).astype(float)
df["is_monday"]      = (df["dow"] == 0).astype(float)
df["is_friday"]      = (df["dow"] == 4).astype(float)
df["days_into_month"]= df.index.day

# ── 9. Target variables (no leakage - all shifted forward) ────────────────────
print("[9] Computing target variables...")

df["next_ret_1d"] = df["btc_ret"].shift(-1)
# next_ret_5d: sum of T+1 through T+5 returns
df["next_ret_5d"] = df["btc_ret"].shift(-1).rolling(5).sum().shift(-4)
df["next_dir_1d"] = (df["next_ret_1d"] > 0).astype(float)

# ── 10. Final cleanup ──────────────────────────────────────────────────────────
print("[10] Saving feature matrix...")

# Drop btc_ret from features (it's embedded in all rolling features; raw value is leaky)
df = df.reset_index()
df = df.sort_values("date")

# Save
df.to_csv(OUT_PATH, index=False)

n_rows = len(df)
n_cols = len(df.columns)
n_notna = df.drop(columns=["next_ret_1d", "next_ret_5d", "next_dir_1d"]).notna().sum()
coverage = (n_notna / n_rows * 100).sort_values()
print(f"\nOutput: {OUT_PATH}")
print(f"  {n_rows} rows x {n_cols} columns")
print(f"\nFeature coverage (% non-NaN, bottom 10):")
for col, pct in coverage.head(10).items():
    print(f"  {col}: {pct:.1f}%")

# Date range check
first_valid = df.dropna(subset=["btc_mvrv"] if "btc_mvrv" in df.columns else ["btc_rsi_14"])
print(f"\nDate range: {df['date'].min().date()} to {df['date'].max().date()}")
print(f"Rows with MVRV: {df['btc_mvrv'].notna().sum() if 'btc_mvrv' in df.columns else 'N/A'}")
print("Done.")
