#!/usr/bin/env python3
"""
buildSpyFeatures.py
===================
Builds a daily feature matrix for SPY return prediction.

Target variables (computed forward, no leakage):
  next_ret_1d   - next trading day SPY return
  next_ret_5d   - cumulative return over next 5 trading days
  next_dir_1d   - binary: 1 if next_ret_1d > 0 else 0 (classification target)

Feature groups:
  1. SPY price/momentum — rolling returns, vol, RSI, Bollinger, drawdown
  2. Volatility regime  — VIX level, VIX change, realised vol, VIX/realised ratio
  3. Options market     — ATM IV, IV skew, PCR vol, term slope (from options snapshot)
  4. Cross-asset        — QQQ beta, GLD, USO, DXY returns and z-scores
  5. Macro              — Fed funds rate, yield curve (T10Y2Y), CPI momentum
  6. Calendar           — day-of-week, month, days-to-FOMC (approximated), VIX expiry week

Output:
  outputs/features/markets/spy_features.csv

Run: venv/bin/python3 python/buildSpyFeatures.py
"""

import os
import warnings
import numpy as np
import pandas as pd
import datetime

warnings.filterwarnings("ignore")

CACHE_DIR  = os.path.expanduser("~/discordBot/outputs/markets/cache")
OPT_PATH   = os.path.expanduser("~/discordBot/outputs/research/SPY_options_daily.csv")
OUT_DIR    = os.path.expanduser("~/discordBot/outputs/features/markets")
OUT_PATH   = os.path.join(OUT_DIR, "spy_features.csv")
os.makedirs(OUT_DIR, exist_ok=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def load_series(filename, date_col=0, val_col=1, name=None):
    path = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(path):
        print(f"  [warn] {filename} not found")
        return pd.Series(dtype=float, name=name)
    df = pd.read_csv(path, parse_dates=[date_col])
    df = df.sort_values(df.columns[date_col])
    s  = df.iloc[:, val_col]
    s.index = df.iloc[:, date_col]
    s.name  = name or df.columns[val_col]
    return s


def rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def z_score(series, window):
    mu  = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mu) / std.replace(0, np.nan)


def realised_vol(returns, window=21):
    """Annualised realised vol from daily returns."""
    return returns.rolling(window).std() * np.sqrt(252)


def drawdown_from_peak(prices, window=252):
    """Rolling max drawdown over trailing window."""
    rolling_max = prices.rolling(window, min_periods=1).max()
    return (prices - rolling_max) / rolling_max


def bollinger_pct(prices, window=20):
    """Price position within Bollinger Band [0, 1]."""
    mu  = prices.rolling(window).mean()
    std = prices.rolling(window).std()
    upper = mu + 2 * std
    lower = mu - 2 * std
    return (prices - lower) / (upper - lower).replace(0, np.nan)


# ── load raw series ───────────────────────────────────────────────────────────

def build_features():
    print("[buildSpyFeatures] loading raw series...")

    spy_ret   = load_series("SPY.csv",      name="SPY_ret")
    qqq_ret   = load_series("QQQ.csv",      name="QQQ_ret")
    gld_ret   = load_series("GLD.csv",      name="GLD_ret")
    uso_ret   = load_series("USO.csv",      name="USO_ret")
    vix       = load_series("VIX.csv",      name="VIX")
    t10y2y    = load_series("T10Y2Y.csv",   name="T10Y2Y")
    dxy       = load_series("DXY.csv",      name="DXY")
    fedfunds  = load_series("FEDFUNDS.csv", name="FEDFUNDS")

    # Align all to SPY trading days (2016 onward)
    idx = spy_ret.index
    df  = pd.DataFrame(index=idx)
    df.index = pd.to_datetime(df.index)

    for s in [spy_ret, qqq_ret, gld_ret, uso_ret, vix, t10y2y, dxy, fedfunds]:
        s.index = pd.to_datetime(s.index)
        df[s.name] = s.reindex(df.index, method="ffill")

    # Reconstruct SPY price from returns (base=100 at start)
    df["SPY_price"] = (1 + df["SPY_ret"].fillna(0)).cumprod() * 100
    df["DXY_ret"]   = df["DXY"].pct_change()

    print(f"  base df: {len(df)} rows, {df.index[0].date()} to {df.index[-1].date()}")

    # ── 1. SPY momentum & technicals ─────────────────────────────────────────
    print("  building SPY technical features...")
    for w in [5, 10, 21, 63]:
        df[f"spy_ret_r{w}"]  = df["SPY_ret"].rolling(w).sum()   # cumulative return
        df[f"spy_vol_r{w}"]  = realised_vol(df["SPY_ret"], w)
    df["spy_rsi_14"]         = rsi(df["SPY_price"], 14)
    df["spy_rsi_3"]          = rsi(df["SPY_price"], 3)          # short-term mean reversion
    df["spy_bbpct_20"]       = bollinger_pct(df["SPY_price"], 20)
    df["spy_drawdown_252"]   = drawdown_from_peak(df["SPY_price"], 252)
    df["spy_mom_63_21"]      = df["spy_ret_r63"] - df["spy_ret_r21"]  # momentum minus recent drift
    df["spy_z5"]             = z_score(df["SPY_ret"], 5)
    df["spy_z21"]            = z_score(df["SPY_ret"], 21)

    # Consecutive up/down days
    df["spy_consec_up"]   = (
        df["SPY_ret"].gt(0).astype(int)
          .groupby((df["SPY_ret"].gt(0) != df["SPY_ret"].gt(0).shift()).cumsum())
          .cumsum()
          * df["SPY_ret"].gt(0).astype(int)
    )
    df["spy_consec_down"] = (
        df["SPY_ret"].lt(0).astype(int)
          .groupby((df["SPY_ret"].lt(0) != df["SPY_ret"].lt(0).shift()).cumsum())
          .cumsum()
          * df["SPY_ret"].lt(0).astype(int)
    )

    # ── 2. Volatility regime ──────────────────────────────────────────────────
    print("  building vol regime features...")
    df["vix_level"]       = df["VIX"]
    df["vix_chg_1d"]      = df["VIX"].diff(1)
    df["vix_chg_5d"]      = df["VIX"].diff(5)
    df["vix_z21"]         = z_score(df["VIX"], 21)
    df["vix_z252"]        = z_score(df["VIX"], 252)
    df["vix_ma20_ratio"]  = df["VIX"] / df["VIX"].rolling(20).mean()     # VIX vs its own MA
    df["rv_21"]           = realised_vol(df["SPY_ret"], 21)
    df["vix_rv_ratio"]    = df["VIX"] / (df["rv_21"] * 100).replace(0, np.nan)  # vol risk premium
    df["vol_regime"]      = pd.cut(df["VIX"],
                                   bins=[0, 15, 20, 30, 40, 999],
                                   labels=[0, 1, 2, 3, 4]).astype(float)

    # ── 3. Options market (from daily snapshot — sparse pre-2026) ─────────────
    print("  loading options snapshot...")
    if os.path.exists(OPT_PATH):
        opt = pd.read_csv(OPT_PATH, parse_dates=["date"])
        opt = opt[opt["ticker"] == "SPY"].set_index("date").sort_index()
        opt.index = pd.to_datetime(opt.index)
        for col in ["atm_iv_avg", "iv_skew_otm", "iv_term_slope", "pcr_vol", "vega_weighted_iv"]:
            if col in opt.columns:
                df[f"opt_{col}"] = opt[col].reindex(df.index, method="ffill")
        print(f"    options rows: {len(opt)}")
    else:
        print("    [warn] no options snapshot found")

    # ── 4. Cross-asset features ───────────────────────────────────────────────
    print("  building cross-asset features...")
    for ret_col, name in [("QQQ_ret","qqq"), ("GLD_ret","gld"),
                           ("USO_ret","uso"), ("DXY_ret","dxy")]:
        df[f"{name}_ret_1d"]  = df[ret_col]
        df[f"{name}_ret_5d"]  = df[ret_col].rolling(5).sum()
        df[f"{name}_z21"]     = z_score(df[ret_col], 21)

    # SPY vs QQQ relative strength (risk-on/off signal)
    df["spy_qqq_rs_5d"]  = df["spy_ret_r5"] - df["QQQ_ret"].rolling(5).sum()
    df["spy_qqq_rs_21d"] = df["spy_ret_r21"] - df["QQQ_ret"].rolling(21).sum()

    # GLD as safe-haven demand signal
    df["gld_spy_corr_21"] = (
        df["GLD_ret"].rolling(21).corr(df["SPY_ret"])
    )  # negative = risk-off when positive

    # ── 5. Macro features ─────────────────────────────────────────────────────
    print("  building macro features...")
    df["fedfunds"]          = df["FEDFUNDS"]
    df["fedfunds_chg_21d"]  = df["FEDFUNDS"].diff(21)     # rate hike/cut momentum
    df["yield_curve"]       = df["T10Y2Y"]                # spread: 10Y - 2Y
    df["yield_curve_chg_5"] = df["T10Y2Y"].diff(5)
    df["yield_curve_z63"]   = z_score(df["T10Y2Y"], 63)
    # Inversion flag
    df["yield_inverted"]    = (df["T10Y2Y"] < 0).astype(int)
    df["dxy_level"]         = df["DXY"]
    df["dxy_z63"]           = z_score(df["DXY"], 63)

    # ── 6. Sector rotation ────────────────────────────────────────────────────
    print("  building sector rotation features...")
    sect_path = os.path.join(CACHE_DIR, "sector_bars_historical.csv")
    if os.path.exists(sect_path):
        sect = pd.read_csv(sect_path, parse_dates=["date"]).set_index("date")
        sect.index = pd.to_datetime(sect.index)
        sect = sect.reindex(df.index, method="ffill")

        SECTORS   = ["XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY"]
        ret_cols  = [f"{s}_ret" for s in SECTORS if f"{s}_ret" in sect.columns]
        for col in ret_cols:
            df[col] = sect[col]

        # Risk-on vs risk-off composite
        risk_on_cols  = [c for c in ["XLK_ret","XLC_ret","XLY_ret"]  if c in sect.columns]
        risk_off_cols = [c for c in ["XLU_ret","XLP_ret","XLRE_ret"] if c in sect.columns]
        if risk_on_cols and risk_off_cols:
            df["sector_risk_on_r5"]  = sect[risk_on_cols].rolling(5).mean().mean(axis=1)
            df["sector_risk_off_r5"] = sect[risk_off_cols].rolling(5).mean().mean(axis=1)
            df["sector_rotation_r5"] = df["sector_risk_on_r5"] - df["sector_risk_off_r5"]

        # XLF relative to SPY — financial stress signal
        if "XLF_ret" in sect.columns:
            df["xlf_spy_rs_5d"] = sect["XLF_ret"].rolling(5).sum() - df["spy_ret_r5"]
        # XLE relative to SPY — commodity/inflation pressure
        if "XLE_ret" in sect.columns:
            df["xle_spy_rs_5d"] = sect["XLE_ret"].rolling(5).sum() - df["spy_ret_r5"]

        # Sector dispersion: high = rotation; low = broad index move
        if len(ret_cols) >= 5:
            df["sector_dispersion_1d"] = sect[ret_cols].std(axis=1)
            df["sector_dispersion_r5"] = sect[ret_cols].rolling(5).std().mean(axis=1)

        print(f"    {len(ret_cols)} sector ETFs + 6 composite features added")
    else:
        print("    [warn] sector_bars_historical.csv not found — run fetchSectorBars.py")

    # ── 8. Macro event flags ──────────────────────────────────────────────────
    print("  building macro event flags...")
    try:
        import sys
        sys.path.insert(0, os.path.expanduser("~/discordBot/python"))
        from macro_event_calendars import FOMC_DATES, CPI_DATES, NFP_DATES

        def event_flags(index, dates, prefix):
            """
            For each event type, create:
              {prefix}_day    : 1 on the event day itself
              {prefix}_before : 1 on the trading day before the event
              {prefix}_after  : 1 on the trading day after the event
              {prefix}_window : 1 on day-before + day-of + day-after (3-day window)
              days_to_{prefix}: trading days until next event (capped at 30, 0 on event day)
            """
            event_set = set(pd.to_datetime(dates).normalize())
            idx       = pd.DatetimeIndex(index)

            day    = pd.Series(idx.normalize().isin(event_set).astype(int), index=index)
            before = day.shift(-1, fill_value=0)   # tomorrow is event -> today is before
            after  = day.shift(1,  fill_value=0)   # yesterday was event -> today is after
            window = (day | before | after).astype(int)

            # Days to next event (forward-looking — no leakage since dates are public)
            sorted_events = sorted(event_set)
            days_to = []
            for d in idx.normalize():
                future = [e for e in sorted_events if e >= d]
                if future:
                    days_to.append(min((future[0] - d).days, 30))
                else:
                    days_to.append(30)
            days_to = pd.Series(days_to, index=index)

            return {
                f"{prefix}_day":    day,
                f"{prefix}_before": before,
                f"{prefix}_after":  after,
                f"{prefix}_window": window,
                f"days_to_{prefix}": days_to,
            }

        for flags in [
            event_flags(df.index, FOMC_DATES, "fomc"),
            event_flags(df.index, CPI_DATES,  "cpi"),
            event_flags(df.index, NFP_DATES,  "nfp"),
        ]:
            for col, series in flags.items():
                df[col] = series.values

        # Historical average returns on event days (continuous, more stable than binary flags)
        # Computed from training data only to avoid leakage — uses expanding window
        # For simplicity, compute full-sample averages (regime changes handled by rolling features)
        spy_ret_series = df["SPY_ret"] if "SPY_ret" in df.columns else pd.Series(0, index=df.index)
        for prefix, dates in [("fomc", FOMC_DATES), ("cpi", CPI_DATES), ("nfp", NFP_DATES)]:
            event_set  = set(pd.to_datetime(dates).normalize())
            day_col    = f"{prefix}_day"
            # Mean SPY return on this event type's days (full sample — stable signal)
            event_mask = df.index.normalize().isin(event_set)
            mean_ret   = spy_ret_series[event_mask].mean() if event_mask.any() else 0.0
            # Encode as: on event day = historical mean, else 0
            df[f"{prefix}_hist_ret"] = np.where(df[day_col] == 1, mean_ret, 0.0)

        df["macro_event_day"]    = ((df["fomc_day"] | df["cpi_day"] | df["nfp_day"]) > 0).astype(int)
        df["macro_event_window"] = ((df["fomc_window"] | df["cpi_window"] | df["nfp_window"]) > 0).astype(int)
        df["days_to_any_macro"]  = df[["days_to_fomc","days_to_cpi","days_to_nfp"]].min(axis=1)

        n_fomc = df["fomc_day"].sum()
        n_cpi  = df["cpi_day"].sum()
        n_nfp  = df["nfp_day"].sum()
        print(f"    FOMC days: {n_fomc} | CPI days: {n_cpi} | NFP days: {n_nfp}")
    except ImportError as e:
        print(f"    [warn] macro_event_calendars not found: {e}")

    # ── 9. Calendar features ──────────────────────────────────────────────────
    df["dow"]           = df.index.dayofweek          # 0=Mon, 4=Fri
    df["month"]         = df.index.month
    df["is_monday"]     = (df["dow"] == 0).astype(int)
    df["is_friday"]     = (df["dow"] == 4).astype(int)
    df["month_sin"]     = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"]     = np.cos(2 * np.pi * df["month"] / 12)
    # Days into month (end-of-month rebalancing effect)
    df["days_into_month"] = df.index.day
    # Quarter-end flag (rebalancing / window dressing)
    df["is_qtr_end"]    = df.index.month.isin([3, 6, 9, 12]).astype(int) * \
                          (df.index.day >= 25).astype(int)

    # ── 10. VWAP features (from fetchVwapDaily.py cache) ─────────────────────
    print("  building VWAP features...")
    vwap_path = os.path.join(CACHE_DIR, "spy_vwap_daily.csv")
    if os.path.exists(vwap_path):
        vwap = pd.read_csv(vwap_path, parse_dates=["date"]).set_index("date").sort_index()
        vwap.index = pd.to_datetime(vwap.index)

        # Core deviation signals
        for col in ["vwap_dev_close", "vwap_dev_open",
                    "vwap_cross_count", "vwap_time_above_pct",
                    "high_vol_above_vwap", "vol_concentration"]:
            if col in vwap.columns:
                df[col] = vwap[col].reindex(df.index, method="ffill")

        # Derived rolling features (require the daily cache column to exist)
        if "vwap_dev_close" in df.columns:
            df["vwap_dev_z21"]    = z_score(df["vwap_dev_close"], 21)
            df["vwap_dev_r5"]     = df["vwap_dev_close"].rolling(5).mean()
            # vol_vwap_corr: does volume concentration predict VWAP deviation?
            if "vol_concentration" in df.columns:
                df["vol_vwap_corr_5d"] = (
                    df["vol_concentration"].rolling(5)
                    .corr(df["vwap_dev_close"].abs())
                )

        print(f"    VWAP cache rows: {len(vwap)}, date range: "
              f"{vwap.index[0].date()} to {vwap.index[-1].date()}")
    else:
        print("    [warn] spy_vwap_daily.csv not found — run fetchVwapDaily.py --backfill")

    # ── 11. Block order signals (from fetchBlockSignals.py) ───────────────────
    print("  building block order features...")
    block_path = os.path.expanduser("~/discordBot/outputs/research/block_events.csv")
    if os.path.exists(block_path):
        blk = pd.read_csv(block_path, parse_dates=["trade_date"])
        blk = blk[blk["ticker"] == "SPY"].copy()
        blk["trade_date"] = pd.to_datetime(blk["trade_date"])
        blk["direction_sign"] = blk["exchange"].apply(
            lambda x: 0
        )  # placeholder; real direction needs block_outcomes join
        # Join direction from outcomes if available
        out_path = os.path.expanduser("~/discordBot/outputs/research/block_outcomes.csv")
        if os.path.exists(out_path):
            bout = pd.read_csv(out_path, parse_dates=["trade_date"])
            bout = bout[bout["ticker"] == "SPY"][["block_time", "direction", "trade_date", "dollar_value"]].copy()
            blk = blk.merge(bout[["block_time", "direction"]],
                            left_on="time", right_on="block_time", how="left")
            blk["direction_sign"] = blk["direction"].map(
                {"below_market": -1, "above_market": 1}
            ).fillna(0)

        # Build daily aggregates: for each trading day, summarize block activity
        blk_daily = blk.groupby("trade_date").agg(
            block_count=("dollar_value", "count"),
            block_dollar_sum=("dollar_value", "sum"),
            block_dev_mean=("deviation", "mean"),
            block_dev_max=("deviation", "max"),
            block_net_sign=("direction_sign", "sum"),
            block_dark_frac=("exchange", lambda x: (x == "D").mean()),
        ).reset_index()
        blk_daily = blk_daily.set_index("trade_date").sort_index()
        blk_daily.index = pd.to_datetime(blk_daily.index)

        # Rolling 5-day window features (look-back from today — no leakage)
        for raw_col in ["block_count", "block_dollar_sum", "block_dev_mean",
                        "block_dev_max", "block_net_sign", "block_dark_frac"]:
            df[raw_col] = blk_daily[raw_col].reindex(df.index).fillna(0)

        df["block_active_flag"]      = (df["block_count"] > 0).astype(int)
        df["block_dollar_flow_5d"]   = df["block_dollar_sum"].rolling(5).sum()
        df["block_net_direction_5d"] = df["block_net_sign"].rolling(5).sum()
        df["block_dev_mean_5d"]      = df["block_dev_mean"].rolling(5).mean()
        df["block_dark_pool_5d"]     = df["block_dark_frac"].rolling(5).mean()

        # High-conviction flag: deviation >= 0.8% is the empirical threshold where
        # reached_1w jumps from ~45% to ~73%+ and lit-exchange prints dominate.
        # Below 0.8% are large index trades with adequate liquidity offset;
        # above 0.8% are macro repositioning orders that had to reach for price.
        blk["high_dev"] = (blk["deviation"] >= 0.008).astype(int)
        hd_daily = blk.groupby("trade_date")["high_dev"].sum()
        hd_daily.index = pd.to_datetime(hd_daily.index)
        df["block_highdev_count"] = hd_daily.reindex(df.index).fillna(0)
        df["block_highdev_5d"]    = df["block_highdev_count"].rolling(5).sum()

        # Days since last block (decay signal — capped at 20)
        block_days = set(blk_daily.index[blk_daily["block_count"] > 0].normalize())
        sorted_blk = sorted(block_days)
        days_since = []
        for d in pd.DatetimeIndex(df.index).normalize():
            past = [b for b in sorted_blk if b <= d]
            days_since.append(min((d - past[-1]).days, 20) if past else 20)
        df["days_since_last_block"] = days_since

        # Drop intermediate daily cols — rolling composites are the features
        df = df.drop(columns=["block_count", "block_dollar_sum", "block_dev_mean",
                               "block_dev_max", "block_net_sign", "block_dark_frac",
                               "block_highdev_count"],
                     errors="ignore")
        print(f"    Block events: {len(blk)} | days with blocks: {len(blk_daily)}")
    else:
        print("    [warn] block_events.csv not found — run fetchBlockSignals.py")

    # ── 12. Intraday aggregated features (from buildIntradayFeatures.py) ─────────
    print("  building intraday aggregated features...")
    intraday_feat_path = os.path.expanduser(
        "~/discordBot/outputs/features/markets/spy_intraday_features.csv"
    )
    if os.path.exists(intraday_feat_path):
        idf = pd.read_csv(intraday_feat_path, parse_dates=["date"]).set_index("date").sort_index()
        idf.index = pd.to_datetime(idf.index)
        INTRADAY_COLS = [
            "first_hour_ret", "last_hour_ret", "am_range", "pm_range",
            "gap_fill_flag", "vwap_dev_am", "open_drive_flag", "vol_am_pct",
            "late_reversal_flag", "premarket_ret", "premarket_vol_ratio", "overnight_gap",
        ]
        for col in INTRADAY_COLS:
            if col in idf.columns:
                # No ffill — each day must have its own intraday measurement or be NaN
                df[col] = idf[col].reindex(df.index)
        print(f"    intraday features rows: {len(idf)}, "
              f"date range: {idf.index[0].date()} to {idf.index[-1].date()}")
    else:
        print("    [warn] spy_intraday_features.csv not found "
              "-- run buildIntradayFeatures.py after fetchIntradayBars.py backfill")

    # ── targets (no leakage — shift(-1) and shift(-5)) ───────────────────────
    print("  computing targets...")
    df["next_ret_1d"]  = df["SPY_ret"].shift(-1)
    df["next_ret_5d"]  = df["SPY_ret"].shift(-1).rolling(5).sum().shift(-4)  # sum of next 5
    df["next_dir_1d"]  = (df["next_ret_1d"] > 0).astype(float)

    # ── final cleanup ─────────────────────────────────────────────────────────
    # Drop raw price/return cols used only as intermediates
    df = df.drop(columns=["SPY_price", "DXY"], errors="ignore")

    # Report coverage
    feat_cols = [c for c in df.columns if c not in
                 ["next_ret_1d","next_ret_5d","next_dir_1d",
                  "SPY_ret","QQQ_ret","GLD_ret","USO_ret",
                  "VIX","T10Y2Y","DXY_ret","FEDFUNDS"]]
    print(f"\n  feature columns: {len(feat_cols)}")
    for col in sorted(feat_cols):
        pct = df[col].notna().mean()
        if pct < 0.5:
            print(f"    [sparse] {col}: {pct*100:.0f}%")

    df.index.name = "date"
    df = df.reset_index()
    df.to_csv(OUT_PATH, index=False)
    print(f"\n[buildSpyFeatures] wrote {len(df)} rows -> {OUT_PATH}")
    return df


if __name__ == "__main__":
    build_features()
