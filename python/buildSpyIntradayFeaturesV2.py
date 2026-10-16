#!/usr/bin/env python3
"""
buildSpyIntradayFeaturesV2.py
===============================
Richer 1-min-bar-derived DAILY features for the SPY primary model (Oct'26,
James's call: "we're missing most of what I'm trying to capture by
ignoring intraday/overnight moves ... keep the macro features, incorporate
1min data"). Extends (does not replace) buildIntradayFeatures.py's 12
features -- this is additive, same join convention (one row per trading
day, joined by date into buildSpyFeatures.py exactly like the existing
INTRADAY_COLS block, zero lookahead: TODAY's intraday shape is a feature
used to predict TOMORROW's direction, never used to predict today's own
close).

New feature groups, each one unlocked specifically by 1-min resolution
(none of these are computable from daily OHLC):

  1. REALIZED VOLATILITY (the headline capability daily bars can't give
     you): sum of squared 1-min log returns over the regular session is a
     dramatically better vol estimator than close-to-close daily vol
     (Andersen/Bollerslev realized-vol literature). Includes a jump-robust
     bipower-variation estimator and the jump-contribution share, since
     the CURRENT primary model has zero way to distinguish "today moved
     because of a slow grind" vs "today moved because of one 2-minute
     spike" -- very different predictive implications for tomorrow.

  2. OVERNIGHT vs SESSION return DECOMPOSITION: explicit separate columns
     for the overnight (prev close -> today open) and session-only
     (today open -> today close) components of the day's full move, so
     the model can learn that these have different drivers (overnight =
     macro/news/futures-driven while market closed; session = order-flow/
     technical-driven) instead of only seeing the blended next_dir_1d
     target as today's pipeline does.

  3. FINER TIME-BUCKET RETURNS: the existing pipeline only has first_hour/
     last_hour/am_range/pm_range (4 broad windows). Adds 6 ~65-min
     intraday buckets covering the full regular session, so sequencing
     information (front-loaded vs back-loaded moves) isn't thrown away.

  4. OPENING RANGE: first-15-min high/low range (classic day-trading
     signal) and whether the session closed outside that range (breakout
     persistence vs opening-range containment).

  5. VOLUME PROFILE: Shannon entropy of volume across 13 half-hour
     buckets (low entropy = one concentrated burst, e.g. a scheduled
     macro print; high = evenly distributed) and a signed volume
     imbalance proxy (up-bar volume share vs down-bar volume share,
     using close>open as the up/down proxy since true tick data isn't
     available).

  6. FULL-SESSION VWAP deviation at the close (existing pipeline only has
     the AM-window version) and session range features (intraday runup/
     drawdown relative to the open).

Output: outputs/features/markets/spy_intraday_features_v2.csv
  columns: date, + ~20 new feature columns (see FEAT_COLS below)

Usage:
  venv/bin/python3 python/buildSpyIntradayFeaturesV2.py
"""
import os
import glob
import warnings
import datetime
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR     = os.path.expanduser("~/discordBot")
INTRADAY_DIR = os.path.join(BASE_DIR, "outputs", "markets", "intraday")
DAILY_BARS   = os.path.join(BASE_DIR, "outputs", "markets", "SPY_max_bars.csv")
OUT_PATH     = os.path.join(BASE_DIR, "outputs", "features", "markets",
                             "spy_intraday_features_v2.csv")

ET = "America/New_York"
ROPEN, RCLOSE = datetime.time(9, 30), datetime.time(16, 0)
PRE_OPEN = datetime.time(4, 0)
ANNUAL_MIN_BARS = 252 * 390   # ~390 regular-session 1-min bars/day


def assign_session(ts):
    t = ts.dt.time
    cond = [(t >= PRE_OPEN) & (t < ROPEN), (t >= ROPEN) & (t < RCLOSE)]
    return np.select(cond, ["pre", "regular"], default="post")


def safe_div(num, denom, fill=np.nan):
    try:
        if denom == 0 or pd.isna(denom):
            return fill
        return num / denom
    except Exception:
        return fill


def load_daily_bars():
    df = pd.read_csv(DAILY_BARS, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "close"]].rename(columns={"close": "daily_close"})


def load_intraday():
    files = sorted(glob.glob(os.path.join(INTRADAY_DIR, "SPY_*_1min.csv")))
    if not files:
        raise FileNotFoundError(f"No intraday CSVs at {INTRADAY_DIR}")
    parts = []
    for fp in files:
        tmp = pd.read_csv(fp)
        parts.append(tmp)
        print(f"  Loaded {os.path.basename(fp)}: {len(tmp):,} rows")
    df = pd.concat(parts, ignore_index=True)
    ts_col = "date" if "date" in df.columns else "timestamp"
    df = df.rename(columns={ts_col: "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df = df.sort_values("ts").reset_index(drop=True)
    if "session" not in df.columns:
        df["session"] = assign_session(df["ts"])
    df["trade_date"] = df["ts"].dt.normalize().dt.tz_localize(None).dt.date
    return df


def bipower_variation(log_rets):
    """Barndorff-Nielsen & Shephard jump-robust vol estimator: scaled sum
    of |r_i| * |r_{i-1}| (adjacent absolute log returns), converges to
    integrated variance even in the presence of jumps (unlike realized
    variance, which jumps inflate)."""
    r = np.asarray(log_rets)
    if len(r) < 2:
        return np.nan
    abs_r = np.abs(r)
    mu1 = np.sqrt(2 / np.pi)   # E|Z| for standard normal Z
    bpv = (1.0 / (mu1 ** 2)) * np.sum(abs_r[1:] * abs_r[:-1])
    return float(bpv)


def shannon_entropy(weights):
    w = np.asarray(weights, dtype=float)
    total = w.sum()
    if total <= 0:
        return np.nan
    p = w / total
    p = p[p > 0]
    ent = -np.sum(p * np.log(p))
    max_ent = np.log(len(w)) if len(w) > 0 else 1.0
    return float(ent / max_ent) if max_ent > 0 else np.nan   # normalized [0,1]


def compute_day_features_v2(day_df, prev_close):
    reg = day_df[day_df["session"] == "regular"].sort_values("ts").reset_index(drop=True)
    result = {}

    if reg.empty or len(reg) < 30:
        for k in V2_FEAT_COLS[1:]:
            result[k] = np.nan
        return result

    t = reg["ts"].dt.time
    close = reg["close"].astype(float).values
    open_ = reg["open"].astype(float).values
    high = reg["high"].astype(float).values
    low = reg["low"].astype(float).values
    vol = reg["volume"].astype(float).values

    session_open = float(open_[0])
    session_close = float(close[-1])
    session_high = float(high.max())
    session_low = float(low.min())

    log_rets = np.diff(np.log(close))
    log_rets = log_rets[np.isfinite(log_rets)]

    # ── 1. Realized volatility (full session, 1-min bars) ────────────────
    rv_full = float(np.sum(log_rets ** 2))
    result["rv_1min_full_ann"] = np.sqrt(rv_full * ANNUAL_MIN_BARS / max(len(log_rets), 1)) if rv_full > 0 else np.nan

    am_mask = t < datetime.time(12, 0)
    pm_mask = ~am_mask
    close_am = close[am_mask.values] if am_mask.sum() > 1 else close[:1]
    close_pm = close[pm_mask.values] if pm_mask.sum() > 1 else close[-1:]
    rets_am = np.diff(np.log(close_am)) if len(close_am) > 1 else np.array([])
    rets_pm = np.diff(np.log(close_pm)) if len(close_pm) > 1 else np.array([])
    rv_am = float(np.sum(rets_am ** 2)) if len(rets_am) else np.nan
    rv_pm = float(np.sum(rets_pm ** 2)) if len(rets_pm) else np.nan
    result["rv_1min_am_ann"] = np.sqrt(rv_am * ANNUAL_MIN_BARS / max(len(rets_am), 1)) if (rv_am and rv_am > 0) else np.nan
    result["rv_1min_pm_ann"] = np.sqrt(rv_pm * ANNUAL_MIN_BARS / max(len(rets_pm), 1)) if (rv_pm and rv_pm > 0) else np.nan

    # ── Bipower variation + jump share ────────────────────────────────────
    bpv = bipower_variation(log_rets)
    result["bpv_1min_ann"] = np.sqrt(bpv * ANNUAL_MIN_BARS / max(len(log_rets), 1)) if (bpv and bpv > 0) else np.nan
    if rv_full > 0 and not np.isnan(bpv):
        jump_component = max(rv_full - bpv, 0.0)
        result["jump_ratio"] = float(jump_component / rv_full)
    else:
        result["jump_ratio"] = np.nan

    # ── 2. Overnight vs session return decomposition ─────────────────────
    if not pd.isna(prev_close) and prev_close > 0:
        result["overnight_ret_v2"] = safe_div(session_open - prev_close, prev_close)
    else:
        result["overnight_ret_v2"] = np.nan
    result["session_only_ret"] = safe_div(session_close - session_open, session_open)

    # ── 3. Finer time-bucket returns (6 buckets across 9:30-16:00) ────────
    bucket_edges = [
        (datetime.time(9, 30), datetime.time(10, 15)),
        (datetime.time(10, 15), datetime.time(11, 15)),
        (datetime.time(11, 15), datetime.time(12, 30)),
        (datetime.time(12, 30), datetime.time(13, 45)),
        (datetime.time(13, 45), datetime.time(15, 0)),
        (datetime.time(15, 0), datetime.time(16, 0)),
    ]
    for i, (b_start, b_end) in enumerate(bucket_edges, start=1):
        mask = (t >= b_start) & (t < b_end)
        bars = reg[mask.values]
        if len(bars) >= 2:
            o = float(bars["open"].iloc[0])
            c = float(bars["close"].iloc[-1])
            result[f"bucket{i}_ret"] = safe_div(c - o, o)
        else:
            result[f"bucket{i}_ret"] = np.nan

    # ── 4. Opening range ──────────────────────────────────────────────────
    or_mask = t <= datetime.time(9, 45)
    or_bars = reg[or_mask.values]
    if not or_bars.empty and session_open > 0:
        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        result["or_15m_range"] = safe_div(or_high - or_low, session_open)
        result["or_15m_breakout_up"] = int(session_close > or_high)
        result["or_15m_breakout_down"] = int(session_close < or_low)
    else:
        result["or_15m_range"] = np.nan
        result["or_15m_breakout_up"] = np.nan
        result["or_15m_breakout_down"] = np.nan

    # ── 5. Volume profile: entropy + signed imbalance ────────────────────
    half_hour_edges = pd.date_range("09:30", "16:00", freq="30min").time
    bucket_vols = []
    for i in range(len(half_hour_edges) - 1):
        mask = (t >= half_hour_edges[i]) & (t < half_hour_edges[i + 1])
        bucket_vols.append(float(vol[mask.values].sum()))
    result["vol_entropy_30m"] = shannon_entropy(bucket_vols)

    up_mask = close > open_
    down_mask = close < open_
    up_vol = float(vol[up_mask].sum())
    down_vol = float(vol[down_mask].sum())
    total_dir_vol = up_vol + down_vol
    result["signed_vol_imbalance"] = safe_div(up_vol - down_vol, total_dir_vol)

    # ── 6. Full-session VWAP deviation + session range ───────────────────
    total_vol = float(vol.sum())
    if total_vol > 0 and "vw" in reg.columns:
        session_vwap = float((reg["vw"].astype(float).values * vol).sum() / total_vol)
    else:
        session_vwap = float(close.mean())
    result["vwap_dev_close_full"] = safe_div(session_close - session_vwap, session_vwap)
    result["session_runup"] = safe_div(session_high - session_open, session_open)
    result["session_drawdown"] = safe_div(session_low - session_open, session_open)

    return result


V2_FEAT_COLS = [
    "date",
    "rv_1min_full_ann", "rv_1min_am_ann", "rv_1min_pm_ann",
    "bpv_1min_ann", "jump_ratio",
    "overnight_ret_v2", "session_only_ret",
    "bucket1_ret", "bucket2_ret", "bucket3_ret",
    "bucket4_ret", "bucket5_ret", "bucket6_ret",
    "or_15m_range", "or_15m_breakout_up", "or_15m_breakout_down",
    "vol_entropy_30m", "signed_vol_imbalance",
    "vwap_dev_close_full", "session_runup", "session_drawdown",
]


def main():
    print("=" * 60)
    print("buildSpyIntradayFeaturesV2.py")
    print("=" * 60)

    print("\nLoading daily bars for prev-close reference...")
    daily = load_daily_bars()
    daily["prev_close"] = daily["daily_close"].shift(1)
    prev_close_map = dict(zip(daily["date"].dt.date, daily["prev_close"]))
    print(f"  Daily bars: {len(daily):,} rows ({daily['date'].min().date()} to {daily['date'].max().date()})")

    print("\nLoading intraday 1-min bars...")
    intraday = load_intraday()
    print(f"  Total 1-min bars: {len(intraday):,}")
    trade_dates = sorted(intraday["trade_date"].unique())
    print(f"  Trading days: {len(trade_dates)} ({trade_dates[0]} to {trade_dates[-1]})")

    print("\nComputing v2 features per day...")
    rows = []
    for i, td in enumerate(trade_dates):
        day_df = intraday[intraday["trade_date"] == td]
        prev_close = prev_close_map.get(td, np.nan)
        feats = compute_day_features_v2(day_df, prev_close)
        feats["date"] = str(td)
        rows.append(feats)
        if (i + 1) % 300 == 0:
            print(f"  ... {i+1}/{len(trade_dates)} days")

    out = pd.DataFrame(rows)[V2_FEAT_COLS]
    out = out.sort_values("date").reset_index(drop=True)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out.to_csv(OUT_PATH, index=False)

    print(f"\nN rows written : {len(out):,}")
    print(f"Date range     : {out['date'].min()} to {out['date'].max()}")
    print("\nFeature coverage (non-null %):")
    for col in V2_FEAT_COLS[1:]:
        pct = out[col].notna().mean() * 100
        print(f"  {col:<24s}: {pct:6.1f}%")
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
