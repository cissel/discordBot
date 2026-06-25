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
    df["vix_rv_spread"]   = df["VIX"] - (df["rv_21"] * 100)                     # IV premium in pts
    df["vol_regime"]      = pd.cut(df["VIX"],
                                   bins=[0, 15, 20, 30, 40, 999],
                                   labels=[0, 1, 2, 3, 4]).astype(float)
    # VIX spike flag: same-day VIX jump > 2pts AND VIX now above its 20d MA
    # Captures regime-transition days (when the bear model most often gets whipsawed)
    df["vix_spike_flag"]  = (
        (df["vix_chg_1d"] > 2.0) & (df["vix_ma20_ratio"] > 1.0)
    ).astype(float)

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
    # 63-day version — less noisy, more stable across regimes
    df["gld_spy_corr_63"] = (
        df["GLD_ret"].rolling(63).corr(df["SPY_ret"])
    )
    # Regime-conditioned GLD/SPY correlation — fixes non-stationarity of gld_spy_corr_21.
    # Pre-2020 gold had different correlation structure (QE era). Conditioning on vol_regime
    # lets GBM learn separate weights per vol environment without era contamination.
    df["gld_corr_x_vol"] = df["gld_spy_corr_21"] * df["vol_regime"]
    # Era-conditional GLD correlation — addresses structural break in gold/SPY relationship.
    # QE era (pre-2022): gold and SPY both lifted by liquidity — correlation ~flat/positive.
    # Post-2022 QT/rate-hike era: gold = genuine flight-to-safety, correlation reliably negative.
    # qt_era=1 from 2022-01-01 onward (first Fed rate hike cycle since 2018, structural break).
    qt_era = (df.index >= "2022-01-01").astype(float)
    df["gld_corr_x_era"]   = df["gld_spy_corr_21"] * qt_era          # corr signal only in QT era
    df["gld_corr_era_flag"] = df["gld_spy_corr_21"] * (1 - qt_era)    # corr signal only in QE era
    df["gld_corr63_x_era"]  = df["gld_spy_corr_63"] * qt_era          # 63d version QT era

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

        # ── macro_event_x_regime: interaction of event window with rate direction ──
        # macro_event_window fires the same binary flag regardless of whether we're
        # hiking (events = negative) or cutting (events = positive). This sign flip
        # across regimes is why a raw macro_event_window flag hurts GBM.
        # Solution: multiply by the direction of the rate cycle so the model sees
        # a signed signal: positive = event in easing cycle, negative = event in hiking cycle.
        ff_direction = df["FEDFUNDS"].diff().fillna(0)           # >0 = hike, <0 = cut, 0 = hold
        # Map to regime: hiking=+1, cutting=-1, hold=0
        rate_regime = np.where(ff_direction > 0, 1,
                      np.where(ff_direction < 0, -1, 0)).astype(float)
        # Where hold, carry the last known direction forward (rate regime is persistent)
        rate_regime_filled = pd.Series(rate_regime, index=df.index)
        rate_regime_filled = rate_regime_filled.replace(0, np.nan).ffill().fillna(0)
        df["macro_event_x_regime"] = df["macro_event_window"] * rate_regime_filled

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

    # ── 13. Order flow / CVD features — REMOVED ──────────────────────────────
    # Lee-Ready tick classification on SPY ETF is structurally contaminated by
    # MOC/closing auction flow (index rebalancers, APs). Correlation with returns
    # is -0.655 same-day (inverted) and ~49% directional accuracy next-day (below
    # coin flip). Stripping the close window does not fix it. Dropped June 2026.

    # ── 14. Dealer GEX features (SqueezeMetrics DIX/GEX, 2011-present) ───────
    print("  building GEX features...")
    gex_path = os.path.expanduser(
        "~/discordBot/outputs/markets/cache/spy_gex_daily.csv"
    )
    if os.path.exists(gex_path):
        gex = pd.read_csv(gex_path, parse_dates=["date"]).set_index("date").sort_index()
        gex.index = pd.to_datetime(gex.index)

        # Normalise GEX to billions for interpretability; keeps z-scores unit-free
        gex["gex_b"] = gex["gex"] / 1e9

        # Rolling stats (needs sorted daily series — gex already is)
        gex["gex_z21"]    = ((gex["gex_b"] - gex["gex_b"].rolling(21).mean())
                             / gex["gex_b"].rolling(21).std())
        gex["gex_z63"]    = ((gex["gex_b"] - gex["gex_b"].rolling(63).mean())
                             / gex["gex_b"].rolling(63).std())
        gex["gex_chg_5d"] = gex["gex_b"].diff(5)
        gex["gex_chg_1d"] = gex["gex_b"].diff(1)
        # Sign: +1 = long gamma (stabilising), -1 = short gamma (destabilising)
        gex["gex_sign"]   = np.sign(gex["gex_b"])
        # DIX: dark pool sentiment (0.33-0.55 range; higher = more bullish dark pool)
        gex["dix_z21"]    = ((gex["dix"] - gex["dix"].rolling(21).mean())
                             / gex["dix"].rolling(21).std())
        gex["dix_chg_5d"] = gex["dix"].diff(5)

        GEX_COLS = [
            "gex_b", "gex_sign", "gex_z21", "gex_z63",
            "gex_chg_1d", "gex_chg_5d",
            "dix", "dix_z21", "dix_chg_5d",
        ]
        for col in GEX_COLS:
            if col in gex.columns:
                # Dense series (2011+) — plain reindex, no ffill needed
                df[col] = gex[col].reindex(df.index)

        print(f"    GEX cache rows: {len(gex)}, "
              f"date range: {gex.index[0].date()} to {gex.index[-1].date()}")
        gex_cov = df["gex_b"].notna().mean()
        print(f"    gex_b coverage in spy_features: {gex_cov*100:.1f}%")
    else:
        print("    [warn] spy_gex_daily.csv not found "
              "-- run python/fetchGexDaily.py to populate")

    # ── 15. VIX term structure + VVIX (vol-of-vol) ────────────────────────────
    print("  building VIX term structure / VVIX features...")
    vix_term_path = os.path.expanduser(
        "~/discordBot/outputs/markets/cache/vix_term_history.csv"
    )
    if os.path.exists(vix_term_path):
        vt = pd.read_csv(vix_term_path, parse_dates=["date"]).set_index("date").sort_index()
        vt.index = pd.to_datetime(vt.index)

        # ── VVIX (vol-of-vol) ──────────────────────────────────────────
        vt["vvix_z21"]    = ((vt["vvix"] - vt["vvix"].rolling(21).mean())
                             / vt["vvix"].rolling(21).std())
        vt["vvix_chg_5d"] = vt["vvix"].diff(5)

        # ── VIX term structure slope ───────────────────────────────────
        # Front-end backwardation (slope < 0) precedes drawdowns;
        # normalise by VIX level so a 2pt spread means the same across regimes.
        vt["vix_term_slope"] = (vt["vix3m"] - vt["vix9d"]) / vt["vix9d"].replace(0, np.nan)
        vt["vix_term_z21"]   = ((vt["vix_term_slope"] - vt["vix_term_slope"].rolling(21).mean())
                                / vt["vix_term_slope"].rolling(21).std())

        # ── VIX - RV spread (implied minus realized fear premium) ──────
        # rv_21 already built in section 2 (annualised fraction); *100 to match VIX scale.
        if "rv_21" in df.columns:
            vt_rv = df["rv_21"].reindex(vt.index) * 100
            vt["vix_rv_spread"] = vt["vix9d"] - vt_rv   # >0 = options expensive vs realised
        else:
            vt["vix_rv_spread"] = np.nan

        VT_COLS = [
            "vvix", "vvix_z21", "vvix_chg_5d",
            "vix_term_slope", "vix_term_z21",
            "vix_rv_spread",
        ]
        for col in VT_COLS:
            if col in vt.columns:
                # Dense series (2016+) — plain reindex, no ffill
                df[col] = vt[col].reindex(df.index)

        print(f"    vix_term cache rows: {len(vt)}, "
              f"range: {vt.index[0].date()} to {vt.index[-1].date()}")
        cov = df["vvix"].notna().mean()
        print(f"    vvix coverage in spy_features: {cov*100:.1f}%")
        slope_cov = df["vix_term_slope"].notna().mean()
        print(f"    vix_term_slope coverage: {slope_cov*100:.1f}%")
    else:
        print("    [warn] vix_term_history.csv not found "
              "-- run python/fetchVixTermHistory.py to populate")

    # ── 16. Market regime label (rule-based, for regime-split models) ──────────
    # Three regimes derived purely from lagged data — no leakage:
    #   bull  = low VIX (< p33) AND price above both 50d and 200d MA
    #   bear  = very elevated VIX (> p80) AND price more than 3% below 200d MA
    #           (both conditions required — stricter threshold reduces false bear labels)
    #   chop  = everything else (includes elevated VIX but price above MAs,
    #            and low VIX with price below MAs — transitional states)
    #
    # The AND requirement prevents mislabeling elevated-VIX-in-uptrend days as "bear".
    # The 3% gap filter (spy_cum < spy_ma200 * 0.97) ensures the bear label only fires
    # during genuine, confirmed downtrends — not marginal 200d MA breaches.
    # Those borderline days belong in chop — the bear model should only train on genuine downtrends.
    print("  building regime labels...")
    spy_cum   = (1 + df["SPY_ret"].fillna(0)).cumprod()
    spy_ma50  = spy_cum.rolling(50,  min_periods=50).mean()
    spy_ma200 = spy_cum.rolling(200, min_periods=200).mean()
    vix_p33   = df["vix_level"].quantile(0.33)
    vix_p67   = df["vix_level"].quantile(0.67)
    vix_p80   = df["vix_level"].quantile(0.80)
    # Bull: VIX calm (< p33) AND price trending up on both timeframes
    bull_mask = (df["vix_level"] < vix_p33) & (spy_cum > spy_ma50) & (spy_cum > spy_ma200)
    # Bear: VIX very elevated (> p80) AND price more than 3% below 200d MA (genuine downtrend)
    bear_mask = (df["vix_level"] > vix_p80) & (spy_cum < spy_ma200 * 0.97)
    # Chop: everything else
    df["regime"] = np.where(bull_mask, "bull", np.where(bear_mask, "bear", "chop"))
    regime_counts = df["regime"].value_counts()
    print(f"    bull={regime_counts.get('bull',0)}  bear={regime_counts.get('bear',0)}  chop={regime_counts.get('chop',0)}")
    print(f"    VIX p33={vix_p33:.1f}  p67={vix_p67:.1f}  p80={vix_p80:.1f}")

    # ── 17. Regime-change recency + interaction features ─────────────────────
    # days_since_regime_change: how many days since the regime last flipped.
    # Stale momentum features carry the old trend for weeks after a flip —
    # this gives the model a way to discount them during transitions.
    # Capped at 63 to stay bounded; ~2-3 weeks is the critical window.
    print("  building regime-change + interaction features...")
    regime_enc = {"bull": 0, "chop": 1, "bear": 2}
    regime_int = df["regime"].map(regime_enc).fillna(1)
    regime_changed = (regime_int != regime_int.shift(1)).astype(int)
    days_since_chg = []
    count = 63
    for changed in regime_changed:
        count = 0 if changed else count + 1
        days_since_chg.append(min(count, 63))
    df["days_since_regime_change"] = days_since_chg
    df["regime_transition_flag"] = (df["days_since_regime_change"] <= 5).astype(float)

    # Momentum features × days_since_regime_change: fast-fade stale momentum
    # after a flip. High value = regime is established; low = in transition.
    regime_stability = df["days_since_regime_change"] / 63.0   # 0=fresh flip, 1=stable
    df["mom_x_regime_stability"]  = df["spy_ret_r21"]   * regime_stability
    df["mom63_x_regime_stability"] = df["spy_ret_r63"]  * regime_stability

    # Bear intraday confirmation: open_drive_flag × bear_flag
    # A strong open drive down in a bear regime is a powerful same-day confirmation.
    # open_drive_flag is already in features (-0.083 Spearman overall, fires hardest in bear)
    bear_flag = (df["regime"] == "bear").astype(float)
    bull_flag = (df["regime"] == "bull").astype(float)
    chop_flag = (df["regime"] == "chop").astype(float)
    if "open_drive_flag" in df.columns:
        df["open_drive_x_bear"] = df["open_drive_flag"] * bear_flag
        df["open_drive_x_bull"] = df["open_drive_flag"] * bull_flag

    # vix_rv_ratio × regime: vol risk premium fires hardest in bear
    if "vix_rv_ratio" in df.columns:
        df["vix_rv_x_bear"] = df["vix_rv_ratio"] * bear_flag
        df["vix_rv_x_chop"] = df["vix_rv_ratio"] * chop_flag

    # dix_chg_5d × regime: dark pool flow direction conditional on regime
    if "dix_chg_5d" in df.columns:
        df["dix_chg_x_bear"] = df["dix_chg_5d"] * bear_flag
        df["dix_chg_x_bull"] = df["dix_chg_5d"] * bull_flag

    # sector_risk_off_r5 × bear: flight-to-safety rotation strongest in downtrends
    if "sector_risk_off_r5" in df.columns:
        df["sector_riskoff_x_bear"] = df["sector_risk_off_r5"] * bear_flag

    # ── 17b. Rate-shock regime features (Arch #2) ──────────────────────────────
    # The 2022 bear was driven by Fed tightening velocity — a qualitatively different
    # regime from price-driven bears (2020 COVID, 2008 GFC). Rate-shock regime fires
    # when the Fed is hiking aggressively: >100bps over 63 trading days.
    # Captured as a continuous feature (tightening speed) + discrete flag.
    if "FEDFUNDS" in df.columns:
        ff = df["FEDFUNDS"].ffill()
        # Rate change over rolling 63-day window (approx 3 months / 2 FOMC meetings)
        ff_chg_63  = ff.diff(63)
        # Tightening velocity z-score (how aggressive is current hiking vs history)
        ff_chg_252 = ff.diff(252)
        ff_spd_z63 = (ff_chg_63 - ff_chg_63.rolling(252).mean()) / (ff_chg_63.rolling(252).std() + 1e-9)
        df["rate_chg_63d"]       = ff_chg_63           # raw rate change 63d (bps ~ %)
        df["rate_chg_252d"]      = ff_chg_252           # raw rate change 252d
        df["rate_shock_flag"]    = (ff_chg_63 > 1.0).astype(float)   # >100bps in 63d = shock
        df["rate_easing_flag"]   = (ff_chg_63 < -0.5).astype(float)  # >50bps cut in 63d = easing
        df["rate_speed_z63"]     = ff_spd_z63           # z-score of tightening speed
        # Interaction: rate shock × bear (the 2022-specific compound regime)
        df["rate_shock_x_bear"]  = df["rate_shock_flag"] * bear_flag
        df["rate_speed_x_bear"]  = df["rate_speed_z63"]  * bear_flag
        print(f"    rate-shock regime: {int(df['rate_shock_flag'].sum())} shock days, "
              f"{int(df['rate_easing_flag'].sum())} easing days")

    # ── 17c. Returns-distribution regime features (Arch #6) ────────────────────
    # Replaces endogenous VIX/price regime with forward-looking distribution stats.
    # These characterize the RETURNS ENVIRONMENT rather than the price level:
    #   - realized skewness of last N days (negative = left-tail environment)
    #   - realized kurtosis (high = fat-tail / stressed)
    #   - rolling drawdown depth (how far from recent peak)
    #   - regime_age_z: how old is the current regime vs its historical duration
    if "SPY_ret" in df.columns:
        r = df["SPY_ret"].fillna(0)
        # Rolling realized skewness and kurtosis (21d and 63d)
        df["ret_skew_21"]  = r.rolling(21).skew()
        df["ret_skew_63"]  = r.rolling(63).skew()
        df["ret_kurt_21"]  = r.rolling(21).kurt()
        # Standardized skewness z-score (vs 252d history) — negative = fear regime
        skew_mu = df["ret_skew_21"].rolling(252).mean()
        skew_sd = df["ret_skew_21"].rolling(252).std() + 1e-9
        df["ret_skew_z21"] = (df["ret_skew_21"] - skew_mu) / skew_sd
        # Rolling drawdown from 63d peak (continuous, not discrete regime)
        cum_63 = r.rolling(63).apply(lambda x: (1 + x).prod(), raw=True)
        roll_max = r.rolling(63).apply(lambda x: (1 + x).cumprod().max(), raw=True)
        df["drawdown_63d"]  = ((cum_63 - roll_max) / (roll_max + 1e-9)).clip(-1, 0)
        # Regime age z-score: days_since_regime_change normalized by historical
        # average duration of that regime type. Long-lived regimes may be ending.
        if "days_since_regime_change" in df.columns:
            dsc = df["days_since_regime_change"]
            dsc_mean = dsc.rolling(504).mean()
            dsc_std  = dsc.rolling(504).std() + 1e-9
            df["regime_age_z"] = (dsc - dsc_mean) / dsc_std
        print(f"    returns-distribution regime: ret_skew_21, ret_kurt_21, drawdown_63d, regime_age_z added")

    # ── 18. Moon phase + weather features ─────────────────────────────────────
    # Moon phase: daily astronomical data, no API needed (pure math in fetchMoonPhase.py)
    # Weather: NYC Central Park + Chicago O'Hare daily TMAX/TMIN/PRCP from NOAA CDO API
    # Both are loaded from cache CSVs and merged on date (index).
    # Reference: Dichev & Janes (2003, JoF) lunar cycles; Hirshleifer & Shumway (2003, JoF) weather
    print("  loading moon phase + weather features...")
    MOON_PATH    = os.path.join(CACHE_DIR, "moon_phase_daily.csv")
    WEATHER_PATH = os.path.join(CACHE_DIR, "weather_daily.csv")

    # Moon phase
    if os.path.exists(MOON_PATH):
        moon_df = pd.read_csv(MOON_PATH, parse_dates=["date"])
        moon_df = moon_df.set_index("date")
        moon_cols = ["moon_phase", "moon_phase_sin", "moon_phase_cos",
                     "days_to_full_moon", "days_to_new_moon",
                     "moon_full_flag", "moon_new_flag"]
        for col in moon_cols:
            if col in moon_df.columns:
                df[col] = moon_df[col].reindex(df.index)
        n_moon = df["moon_phase"].notna().sum() if "moon_phase" in df.columns else 0
        print(f"    moon phase coverage: {n_moon}/{len(df)} rows ({n_moon/len(df)*100:.0f}%)")
    else:
        print(f"    [warn] moon phase cache not found — run fetchMoonPhase.py")

    # Weather (NYC + Chicago)
    if os.path.exists(WEATHER_PATH):
        wx_df = pd.read_csv(WEATHER_PATH, parse_dates=["date"])
        wx_df = wx_df.set_index("date")
        wx_cols = [c for c in wx_df.columns if any(
            c.startswith(pfx) for pfx in
            ["tmax_", "tmin_", "prcp_", "snow_", "tavg_",
             "temp_range_", "prcp_flag_", "cold_flag_", "hot_flag_", "snow_flag_"]
        )]
        for col in wx_cols:
            df[col] = wx_df[col].reindex(df.index)
        n_nyc = df["tavg_nyc"].notna().sum() if "tavg_nyc" in df.columns else 0
        n_chi = df["tavg_chi"].notna().sum() if "tavg_chi" in df.columns else 0
        print(f"    weather coverage: NYC={n_nyc} rows, CHI={n_chi} rows")
        # sunshine proxy: no precip + wide temp range = clear day
        if "prcp_flag_nyc" in df.columns and "temp_range_nyc" in df.columns:
            df["sunshine_proxy_nyc"] = (
                (1 - df["prcp_flag_nyc"].fillna(0)) *
                df["temp_range_nyc"].fillna(df["temp_range_nyc"].median())
            )
        if "prcp_flag_chi" in df.columns and "temp_range_chi" in df.columns:
            df["sunshine_proxy_chi"] = (
                (1 - df["prcp_flag_chi"].fillna(0)) *
                df["temp_range_chi"].fillna(df["temp_range_chi"].median())
            )
    else:
        print(f"    [warn] weather cache not found — run fetchWeatherDaily.py (needs NOAA_CDO_TOKEN in .env)")

    # ── 19. Alternative / sentiment data ──────────────────────────────────────
    # Loads from three cache CSVs:
    #   fetchAlternativeData.py  -> alternative_data_daily.csv  (wiki, congress, holidays, MTA)
    #   fetchAlternativeData2.py -> alt_data2_daily.csv         (daylight, PCR, earnings, approval)
    #   fetchGoogleTrends.py     -> google_trends_daily.csv     (trends: SPY, crash, recession...)
    # All merged on date index; NaN-safe (missing rows handled by SPARSE_FEATURES imputation).
    ALT1_PATH = os.path.join(CACHE_DIR, "alternative_data_daily.csv")
    ALT2_PATH = os.path.join(CACHE_DIR, "alt_data2_daily.csv")
    TRENDS_PATH = os.path.join(CACHE_DIR, "google_trends_daily.csv")

    if os.path.exists(ALT1_PATH):
        alt1 = pd.read_csv(ALT1_PATH, parse_dates=["date"]).set_index("date")
        alt1_cols = [c for c in alt1.columns if c != "date"]
        for col in alt1_cols:
            df[col] = alt1[col].reindex(df.index)
        print(f"  alt data 1: {len(alt1_cols)} cols, wikipedia={df['wiki_total_views'].notna().sum() if 'wiki_total_views' in df.columns else 0} rows")
    else:
        print(f"  [warn] alternative_data_daily.csv not found — run fetchAlternativeData.py")

    if os.path.exists(ALT2_PATH):
        alt2 = pd.read_csv(ALT2_PATH, parse_dates=["date"]).set_index("date")
        alt2_cols = [c for c in alt2.columns if c != "date"]
        for col in alt2_cols:
            df[col] = alt2[col].reindex(df.index)
        print(f"  alt data 2: {len(alt2_cols)} cols, daylight={df['daylight_hours_nyc'].notna().sum() if 'daylight_hours_nyc' in df.columns else 0} rows")
    else:
        print(f"  [warn] alt_data2_daily.csv not found — run fetchAlternativeData2.py")

    if os.path.exists(TRENDS_PATH):
        trends = pd.read_csv(TRENDS_PATH, parse_dates=["date"]).set_index("date")
        trends_cols = [c for c in trends.columns if c != "date"]
        # CRITICAL: apply +2 business-day publication lag.
        # Google weekly Trends covers Mon-Sun, published ~Tuesday after week ends.
        # Without lag, Monday predictions see data from a week not yet published — look-ahead.
        # Shift the index forward 2bd so that any given date only sees prior-week Trends.
        trends_lagged = trends[trends_cols].copy()
        trends_lagged.index = trends_lagged.index + pd.tseries.offsets.BusinessDay(2)
        trends_lagged = trends_lagged[~trends_lagged.index.duplicated(keep="last")]
        for col in trends_cols:
            df[col] = trends_lagged[col].reindex(df.index)
        trend_cov = df["trends_spy"].notna().mean() if "trends_spy" in df.columns else 0
        print(f"  google trends: {len(trends_cols)} cols, trends_spy={trend_cov:.0%} coverage (+2bd publication lag applied)")
    else:
        print(f"  [info] google_trends_daily.csv not found — run fetchGoogleTrends.py (optional)")

    # ── 20. Sentiment + macro sentiment data ───────────────────────────────────
    # fetchSentimentData.py    -> sentiment_daily.csv    (AAII + Crypto Fear & Greed)
    # fetchMacroSentiment.py   -> macro_sentiment_daily.csv (ICSA, UMCSENT, CSCICP03)
    SENTIMENT_PATH     = os.path.join(CACHE_DIR, "sentiment_daily.csv")
    MACRO_SENT_PATH    = os.path.join(CACHE_DIR, "macro_sentiment_daily.csv")

    if os.path.exists(SENTIMENT_PATH):
        sent = pd.read_csv(SENTIMENT_PATH, parse_dates=["date"]).set_index("date")
        for col in [c for c in sent.columns if c != "date"]:
            df[col] = sent[col].reindex(df.index)
        aaii_cov = df["aaii_bull_bear_spread"].notna().mean() if "aaii_bull_bear_spread" in df.columns else 0
        cfg_cov  = df["cfg_z21"].notna().mean() if "cfg_z21" in df.columns else 0
        print(f"  sentiment: aaii={aaii_cov:.0%}, crypto_fear_greed={cfg_cov:.0%}")
    else:
        print(f"  [warn] sentiment_daily.csv not found — run fetchSentimentData.py")

    if os.path.exists(MACRO_SENT_PATH):
        msent = pd.read_csv(MACRO_SENT_PATH, parse_dates=["date"]).set_index("date")
        for col in [c for c in msent.columns if c != "date"]:
            df[col] = msent[col].reindex(df.index)
        icsa_cov = df["icsa_z52"].notna().mean() if "icsa_z52" in df.columns else 0
        print(f"  macro sentiment: icsa={icsa_cov:.0%} coverage")
    else:
        print(f"  [warn] macro_sentiment_daily.csv not found — run fetchMacroSentiment.py")

    # ── 20b. Regime × sentiment interaction features (Arch #8) ─────────────────
    # Top features from R29/R30: trends_fear_z21, trends_buy_stocks, trends_volatility,
    # wiki_economy_views, sunshine_proxy_chi. These likely have regime-conditional effects:
    # a recession-search spike in 2019 (bull) meant nothing; same spike in 2022 (bear) was signal.
    bear_f  = (df["regime"] == "bear").astype(float)  if "regime" in df.columns else pd.Series(0, index=df.index)
    bull_f  = (df["regime"] == "bull").astype(float)  if "regime" in df.columns else pd.Series(0, index=df.index)
    chop_f  = (df["regime"] == "chop").astype(float)  if "regime" in df.columns else pd.Series(0, index=df.index)

    for feat, label in [
        ("trends_fear_z21",        "fear_z21"),
        ("trends_crash",           "crash"),
        ("trends_recession",       "recession"),
        ("trends_volatility",      "vol"),
        ("trends_buy_stocks",      "buy"),
        ("trends_distress_index",  "distress"),
        ("aaii_bull_bear_spread",  "aaii_spread"),
        ("cfg_z21",                "cfg_z21"),
        ("icsa_z52",               "icsa_z52"),
    ]:
        if feat in df.columns:
            df[f"{label}_x_bear"] = df[feat] * bear_f
            df[f"{label}_x_bull"] = df[feat] * bull_f
            df[f"{label}_x_chop"] = df[feat] * chop_f

    # Rate-shock × key sentiment: does fear signal amplify in rate-shock regime?
    if "rate_shock_flag" in df.columns:
        for feat, label in [
            ("trends_fear_z21",   "fear_z21"),
            ("trends_recession",  "recession"),
            ("icsa_z52",          "icsa"),
        ]:
            if feat in df.columns:
                df[f"{label}_x_rate_shock"] = df[feat] * df["rate_shock_flag"]

    print(f"  regime×sentiment interactions: added")

    # ── 21. Reddit WSB sentiment (score-based only — count capped at 100) ──────
    # fetchRedditWSB.py -> wsb_daily.csv
    # wsb_post_count is capped at 100 (API pagination limit) — useless as volume signal.
    # wsb_avg_score and wsb_avg_comments carry genuine virality/sentiment signal.
    WSB_PATH = os.path.join(CACHE_DIR, "wsb_daily.csv")
    if os.path.exists(WSB_PATH):
        wsb = pd.read_csv(WSB_PATH, parse_dates=["date"]).set_index("date")
        # Only wire score-based cols — skip post_count and posts_z21 (capped noise)
        wsb_cols = [c for c in wsb.columns if c not in ("wsb_post_count", "wsb_posts_z21")]
        for col in wsb_cols:
            df[col] = wsb[col].reindex(df.index)
        score_cov = df["wsb_avg_score"].notna().mean() if "wsb_avg_score" in df.columns else 0
        print(f"  reddit WSB: {len(wsb_cols)} cols, avg_score={score_cov:.0%} coverage")
    else:
        print(f"  [info] wsb_daily.csv not found — run fetchRedditWSB.py (optional)")

    # ── 22. CBOE SKEW Index + VIX9D ───────────────────────────────────────────
    # fetchCboeSkew.py -> cboe_skew_daily.csv
    # SKEW measures tail-risk premium: high SKEW = market buying OTM puts.
    # Range ~100-160+; level, z-score, 5d change, and bear-regime interaction.
    # VIX9D = 9-day VIX (short-term fear); vix9d_vix30_ratio = term structure slope.
    SKEW_PATH = os.path.join(CACHE_DIR, "cboe_skew_daily.csv")
    if os.path.exists(SKEW_PATH):
        skew_df = pd.read_csv(SKEW_PATH, parse_dates=["date"]).set_index("date").sort_index()
        skew_df.index = pd.to_datetime(skew_df.index)
        skew_df = skew_df[~skew_df.index.duplicated(keep="first")]

        # Level and z-scores
        s = skew_df["cboe_skew"]
        skew_df["skew_z21"]    = (s - s.rolling(21).mean()) / (s.rolling(21).std() + 1e-9)
        skew_df["skew_z63"]    = (s - s.rolling(63).mean()) / (s.rolling(63).std() + 1e-9)
        skew_df["skew_chg_5d"] = s.diff(5)
        skew_df["skew_chg_1d"] = s.diff(1)
        # High SKEW flag: top tercile (>= 130 historically ~ p67)
        skew_df["skew_high_flag"] = (s >= s.rolling(252).quantile(0.67)).astype(float)

        # VIX9D / VIX30 ratio: > 1 = inverted (short-term fear > long-term) -> near-term event risk
        if "vix9d" in skew_df.columns and "VIX" in df.columns:
            vix30_aligned = df["VIX"].reindex(skew_df.index, method=None)
            vix30_aligned = vix30_aligned[~vix30_aligned.index.duplicated(keep="first")]
            skew_dedup    = skew_df[~skew_df.index.duplicated(keep="first")]
            skew_df["vix9d_vix30_ratio"] = (
                skew_dedup["vix9d"] / vix30_aligned.replace(0, np.nan)
            )
        # VIX9D z21
        if "vix9d" in skew_df.columns:
            v9 = skew_df["vix9d"]
            skew_df["vix9d_z21"] = (v9 - v9.rolling(21).mean()) / (v9.rolling(21).std() + 1e-9)

        SKEW_COLS = [
            "cboe_skew", "skew_z21", "skew_z63",
            "skew_chg_5d", "skew_chg_1d", "skew_high_flag",
            "vix9d", "vix9d_z21", "vix9d_vix30_ratio",
        ]
        for col in SKEW_COLS:
            if col in skew_df.columns:
                df[col] = skew_df[col].reindex(df.index)

        skew_cov = df["cboe_skew"].notna().mean()
        print(f"  CBOE SKEW: {len(SKEW_COLS)} cols, coverage={skew_cov:.0%}")
    else:
        print(f"  [info] cboe_skew_daily.csv not found — run fetchCboeSkew.py")


    df["next_ret_1d"]  = df["SPY_ret"].shift(-1)
    df["next_ret_5d"]  = df["SPY_ret"].shift(-1).rolling(5).sum().shift(-4)  # sum of next 5
    df["next_dir_1d"]  = (df["next_ret_1d"] > 0).astype(float)
    # Vol-adjusted 5d target: next_ret_5d / expected vol over 5 days.
    # Use rv_21 LAGGED by 5 days — the vol prevailing at the START of the window,
    # not during it. This removes the denominator correlation that killed the
    # original implementation (rv_21 is high exactly when big moves happen).
    rv_lagged = df["rv_21"].shift(5)
    df["next_ret_5d_vadj"] = (
        df["next_ret_5d"] / (rv_lagged * np.sqrt(5 / 252))
    ).clip(-3, 3)
    df["next_dir_5d_vadj"] = (df["next_ret_5d_vadj"] > 0).astype(float)

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
