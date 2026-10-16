#!/usr/bin/env python3
"""
mm_dashboard/app.py
====================
Live browser dashboard for marketMaker.py (/mm) - tick-level price chart
with horizontal lines for the bot's resting buy/sell order prices, so you
can watch orders get hit in near real time.

Two data sources, both read-only:
  1. TICK-LEVEL LIVE DATA (default view) - written by live_poller.py, a
     separate persistent websocket client holding direct Alpaca market-data
     + trade-updates streams. Files:
       outputs/markets/mm_live_ticks_{symbol}.json   - every quote/trade tick
       outputs/markets/mm_live_orders_{symbol}.json  - current resting buy/sell
       outputs/markets/mm_live_fills_{symbol}.json   - recent fills, instant
     This is what gives sub-second latency and a real "last 2 minutes" view.
     Requires live_poller.py to be running (systemd service mm-dashboard-poller).
  2. CYCLE LOG (fallback / longer history) - marketMaker.py's own
     mm_cycle_log_{symbol}.csv / mm_fills_log_{symbol}.csv, snapshotted only
     once per ~6min cron cycle. Used for the "long window" views (1h+) where
     tick-level granularity would be too much data anyway, and as a fallback
     if live_poller.py isn't running.

Usage:
  venv/bin/python3 python/mm_dashboard/app.py              # 0.0.0.0:8899
  venv/bin/python3 python/mm_dashboard/app.py --port 9000
"""
import os
import json
import time
import argparse
import datetime
import pandas as pd
from flask import Flask, jsonify, request, render_template_string

OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets")
SYMBOLS = ["SOXL", "TQQQ", "GME"]
LIVE_STALE_SEC = 10  # if live_poller.py's files haven't updated in this long, treat as down
LOCAL_TZ = "America/New_York"  # the Pi's local tz - what naive timestamps in these logs actually are

app = Flask(__name__)


def parse_mixed_tz(series):
    """marketMaker.py's logged 'timestamp' column mixes tz-AWARE UTC strings
    ("...T13:38:27.757229Z", from an older logging format) with tz-NAIVE
    strings ("...T10:48:35", current format) across its history - and the
    naive ones are LOCAL Pi time (America/New_York), NOT UTC, confirmed by
    cross-checking mm_fills_log_SOXL.csv's naive 'timestamp' column against
    its always-UTC-aware 'filled_at' column for the same row (e.g.
    timestamp=11:06:37 naive vs filled_at=15:00:52Z UTC - exactly the 4h EDT
    offset). A blanket utc=True/format=mixed pass (the first fix attempted)
    silently treats naive values AS IF already UTC, which parses without
    error but is wrong by exactly the local UTC offset - confirmed as a
    second bug via the equity curve's live tail jumping from 11:12 to 15:13
    (4h gap) when stitched against live_poller.py's genuinely-UTC tick data.
    Fix: parse naive values with tz_localize(LOCAL_TZ) (DST-aware) then
    convert everything to UTC, instead of assuming naive == UTC.

    Vectorized (not a per-row .apply()): splits the series into the
    tz-aware-string subset and the naive-string subset by a cheap string
    suffix check, bulk-parses each subset with pd.to_datetime, localizes the
    naive subset, converts both to UTC, then reassembles by original index.
    The row-wise .apply() version this replaced took ~1-2s per ~5000-row
    CSV and was the dominant cost in build_mm_pnl_curve() - confirmed via
    direct timing that pegged app.py at ~99% CPU because /api/equity is
    polled every 1-5s by the dashboard's live refresh loop, so a multi-second
    per-call cost compounds immediately."""
    s = series.astype(str)
    is_aware = s.str.endswith("Z")
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns, UTC]")
    if is_aware.any():
        out.loc[is_aware] = pd.to_datetime(s[is_aware], utc=True, errors="coerce")
    naive_mask = ~is_aware & series.notna()
    if naive_mask.any():
        naive_parsed = pd.to_datetime(s[naive_mask], errors="coerce")
        localized = naive_parsed.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
        out.loc[naive_mask] = localized
    return out


def cycle_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_cycle_log_{symbol}.csv")


def fills_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_fills_log_{symbol}.csv")


def state_path(symbol):
    return os.path.join(OUT_DIR, f"mm_state_{symbol}.json")


def live_ticks_path(symbol):
    return os.path.join(OUT_DIR, f"mm_live_ticks_{symbol}.json")


def live_orders_path(symbol):
    return os.path.join(OUT_DIR, f"mm_live_orders_{symbol}.json")


def live_fills_path(symbol):
    return os.path.join(OUT_DIR, f"mm_live_fills_{symbol}.json")


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def live_poller_alive():
    """Check freshness of the live tick files, not just existence - a dead
    poller leaves stale files sitting around, which would otherwise look
    like a working live feed with zero new ticks."""
    p = live_ticks_path(SYMBOLS[0])
    if not os.path.exists(p):
        return False
    return (time.time() - os.path.getmtime(p)) < LIVE_STALE_SEC


def load_state(symbol):
    return load_json(state_path(symbol)) or {}


_cycle_cache = {}   # path -> (mtime, parsed_df) - avoids re-parsing a multi-thousand-row CSV on every poll
_fills_cache = {}


def _load_parsed_csv_cached(path, cache, dedupe_fills=False):
    """mtime-gated cache: only re-read + re-parse a CSV when it has actually
    changed on disk. marketMaker.py's cron only appends to these files every
    ~1 minute, but /api/equity (which calls through here) is polled by the
    dashboard every 1-5s - without this cache, build_mm_pnl_curve() was
    re-parsing the full cycle+fills history (up to ~5700 rows) on every
    single poll, confirmed costing ~1.5-5.5s of CPU per call and pegging
    app.py at ~99% CPU (the 1-2s of that was parse_mixed_tz's original
    row-wise .apply(), now also fixed to be vectorized - the two fixes
    compound: vectorizing cut the per-parse cost roughly 10x, and caching
    cuts the NUMBER of parses to roughly 1 per minute instead of 1 per
    poll).

    dedupe_fills=True applies the SAME dedup marketMaker.py's own
    compute_pnl_from_fills() already does on activity_id (fallback
    order_id+side+qty+filled_at) before computing P&L from fills -
    discovered missing here when TQQQ/GME showed nonzero position/unrealized
    P&L on the dashboard while actually flat at the broker (confirmed via
    GET /v2/positions). Root cause: mm_fills_log_{symbol}.csv has duplicate
    rows for the same activity_id (the bot's own fill-activity poller can
    re-log a fill it already saw, e.g. across a crash/restart) - the
    dashboard was reading the raw CSV with no dedup, double/triple-counting
    those fills into position and cash_pnl, while marketMaker.py's live
    trading logic was unaffected because its OWN compute_pnl_from_fills
    already deduped. Must dedupe before parse_mixed_tz/caching, not after,
    so the cached dataframe used by every consumer (cumulative_fill_state,
    compute_avgcost_series, display) is already correct.
    """
    if not os.path.exists(path):
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    cached = cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    df = pd.read_csv(path)
    if dedupe_fills and not df.empty:
        if "activity_id" in df.columns:
            key = df["activity_id"].astype(str)
            fallback = key.isin(["", "nan", "None"])
            if fallback.any():
                composite = (df["order_id"].astype(str) + "|" + df["side"].astype(str) + "|"
                             + df["qty"].astype(str) + "|" + df["filled_at"].astype(str))
                key = key.where(~fallback, composite)
            df = df[~key.duplicated(keep="first")]
    if not df.empty:
        df["timestamp"] = parse_mixed_tz(df["timestamp"])
        df = df.dropna(subset=["timestamp"])
    cache[path] = (mtime, df)
    return df


def load_cycles(symbol, window_minutes):
    df = _load_parsed_csv_cached(cycle_log_path(symbol), _cycle_cache)
    if df.empty:
        return df
    if window_minutes:
        cutoff = df["timestamp"].max() - pd.Timedelta(minutes=window_minutes)
        df = df[df["timestamp"] >= cutoff]
    return df


def load_fills_csv(symbol, window_minutes, since_ts=None):
    df = _load_parsed_csv_cached(fills_log_path(symbol), _fills_cache, dedupe_fills=True)
    if df.empty:
        return df
    if since_ts is not None:
        df = df[df["timestamp"] > since_ts]
    elif window_minutes:
        cutoff = df["timestamp"].max() - pd.Timedelta(minutes=window_minutes)
        df = df[df["timestamp"] >= cutoff]
    return df


def load_full_fills(symbol):
    """Full fills history for a symbol (not window-limited) from the CSV log,
    sorted ascending by time. Used to compute cumulative MM-only P&L from
    inception - this account also runs a separate SPY directional bot
    (tradeSpy.py), so account-level equity is NOT usable for "how is the MM
    bot doing" - it mixes in that bot's P&L. Fills are per-symbol and
    SOXL/TQQQ are exclusively the MM bot's instruments (confirmed: tradeSpy.py
    only ever trades SPY), so summing fills for just these two symbols
    isolates the MM bot's contribution exactly."""
    df = _load_parsed_csv_cached(fills_log_path(symbol), _fills_cache, dedupe_fills=True)
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "side", "qty", "price"])
    df = df.sort_values("timestamp").rename(columns={"filled_avg_price": "price"})
    return df[["timestamp", "side", "qty", "price"]].reset_index(drop=True)


def cumulative_fill_state(fills_df):
    """Running (position, cash_pnl) immediately after each fill, starting
    from true zero (both symbols' logs start at position_shares=0.0,
    confirmed). cash_pnl is NOT account cash - it's pure cumulative trading
    cash flow (buys subtract, sells add) for this symbol only, which nets
    out to total realized+unrealized strategy profit once combined with
    position*mid - exactly mirrors the cash/position bootstrap technique
    used for the live tail, just driven by real historical fills instead."""
    if fills_df.empty:
        return pd.DataFrame(columns=["timestamp", "position", "cash_pnl"])
    qty = fills_df["qty"].astype(float)
    price = fills_df["price"].astype(float)
    # "not buy" (not just literal "sell") is treated as a sell, matching the
    # buy/else convention used everywhere else in this file (e.g.
    # apply_fill_avgcost, state_at_global_anchor) - mm_fills_log_SOXL.csv has
    # some historical rows logged as side="sell_short" (still an
    # Alpaca-reported sell fill) which this used to .map() to NaN, silently
    # corrupting position/cash_pnl with NaN from that row forward. Confirmed
    # via SOXL's deduped fills summing to a different buy/sell split when
    # side=="sell" was required literally vs. side!="buy".
    sign = pd.Series(1.0, index=fills_df.index)
    sign[fills_df["side"] != "buy"] = -1.0
    position = (sign * qty).cumsum()
    cash_pnl = (-sign * qty * price).cumsum()
    out = fills_df[["timestamp"]].copy()
    out["position"] = position.values
    out["cash_pnl"] = cash_pnl.values
    return out


def per_symbol_mtm_series(symbol):
    """Per-symbol mark-to-market P&L at each of that symbol's own cycle-log
    sample times: cumulative cash_pnl (as-of that time, ffilled from the
    fills log) + position * mid (mid also from that same cycle row, ffilled
    across halted/no-quote rows so a halt doesn't zero out P&L)."""
    cycles = load_cycles(symbol, window_minutes=None)
    if cycles.empty:
        return pd.DataFrame(columns=["timestamp", "position", "cash_pnl", "mid"])
    cyc = cycles[["timestamp", "mid"]].dropna(subset=["timestamp"]).sort_values("timestamp")
    fill_state = cumulative_fill_state(load_full_fills(symbol))
    if not fill_state.empty:
        merged = pd.merge_asof(cyc, fill_state, on="timestamp", direction="backward")
    else:
        merged = cyc.copy()
        merged["position"] = 0.0
        merged["cash_pnl"] = 0.0
    merged["position"] = merged["position"].fillna(0.0)
    merged["cash_pnl"] = merged["cash_pnl"].fillna(0.0)
    merged["mid"] = merged["mid"].ffill()  # halted cycles log no NBBO - carry last known mid for correct MTM
    return merged[["timestamp", "position", "cash_pnl", "mid"]]


def apply_fill_avgcost(position, avg_cost, realized, side, qty, price):
    """Apply one fill to (position, avg_cost, realized_pnl) using standard
    average-cost-basis accounting, returning the updated triple.

    Identity this relies on: realized_pnl (via avg-cost) + position*(mid -
    avg_cost) (unrealized) == cash_pnl + position*mid (the existing,
    independently-verified cumulative-cash-flow total P&L) at every instant -
    a standard accounting identity, spot-checked numerically here (buy 10@10,
    sell 5@12, mid=11: realized=5*(12-10)=10, unrealized=5*(11-10)=5, total=15;
    cash_pnl=-100+60=-40, cash_pnl+position*mid=-40+5*11=15 - matches). This
    lets the realized/unrealized split be ADDED alongside the already-working
    cash_pnl-based total P&L computation without touching or re-deriving it -
    existing total P&L code path (build_mm_pnl_curve's cash_pnl/position
    tracking) is left completely untouched; this is a parallel computation.
    """
    signed_qty = qty if side == "buy" else -qty
    if position == 0 or (position > 0) == (signed_qty > 0):
        # same direction (or opening from flat) - accumulate, update avg cost
        new_position = position + signed_qty
        if new_position != 0:
            avg_cost = (position * avg_cost + signed_qty * price) / new_position
        position = new_position
    else:
        # opposite direction - closes some/all of the existing position,
        # possibly flips sign if the fill is bigger than the open position
        if abs(signed_qty) <= abs(position):
            closed_qty = abs(signed_qty)
            if position > 0:
                realized += closed_qty * (price - avg_cost)
            else:
                realized += closed_qty * (avg_cost - price)
            position = position + signed_qty
            if position == 0:
                avg_cost = 0.0
            # else: avg_cost unchanged - remaining shares keep their original cost basis
        else:
            closed_qty = abs(position)
            if position > 0:
                realized += closed_qty * (price - avg_cost)
            else:
                realized += closed_qty * (avg_cost - price)
            position = signed_qty + position  # remaining qty flips the position
            avg_cost = price  # new position's cost basis is this fill's price
    return position, avg_cost, realized


_avgcost_cache = {}  # fills_log_path(symbol) -> (mtime, cumulative per-fill avgcost df) - same mtime-gating pattern as _fills_cache


def compute_avgcost_series(symbol):
    """Cumulative (position, avg_cost, realized_pnl) after each historical
    fill, in order, using average-cost-basis accounting (apply_fill_avgcost).
    Cached by the fills CSV's mtime (same pattern as _load_parsed_csv_cached)
    so this sequential per-fill loop - not vectorizable due to the avg-cost
    branching - only re-runs when the file actually changes (~once/minute),
    not on every dashboard poll."""
    path = fills_log_path(symbol)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["timestamp", "position", "avg_cost", "realized_pnl"])
    mtime = os.path.getmtime(path)
    cached = _avgcost_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    fills = load_full_fills(symbol)
    position, avg_cost, realized = 0.0, 0.0, 0.0
    rows = []
    for row in fills.itertuples(index=False):
        position, avg_cost, realized = apply_fill_avgcost(
            position, avg_cost, realized, row.side, float(row.qty), float(row.price))
        rows.append({"timestamp": row.timestamp, "position": position,
                     "avg_cost": avg_cost, "realized_pnl": realized})
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["timestamp", "position", "avg_cost", "realized_pnl"])
    _avgcost_cache[path] = (mtime, df)
    return df


def avgcost_state_at_global_anchor(symbol, avgcost_df, global_anchor_time, global_anchor_epoch):
    """(position, avg_cost, realized_pnl, mid) for one symbol AT the global
    anchor instant - mirrors state_at_global_anchor's cross-symbol-gap fix
    (see its docstring) but tracks avg-cost/realized instead of cash_pnl."""
    if avgcost_df.empty:
        own_last_time = None
        position, avg_cost, realized = 0.0, 0.0, 0.0
    else:
        own_last_time = avgcost_df["timestamp"].iloc[-1]
        row = avgcost_df.iloc[-1]
        position, avg_cost, realized = float(row["position"]), float(row["avg_cost"]), float(row["realized_pnl"])
    if own_last_time is not None and own_last_time < global_anchor_time:
        for f in fills_after(symbol, own_last_time.timestamp(), epoch_upto=global_anchor_epoch):
            position, avg_cost, realized = apply_fill_avgcost(
                position, avg_cost, realized, f["side"], f["qty"], f["price"])
    mid = find_mid_at_or_before(symbol, global_anchor_epoch)
    if mid is None:
        last_row = latest_cycle_row_before(symbol, global_anchor_epoch)
        mid = float(last_row["mid"]) if last_row is not None and pd.notna(last_row.get("mid")) else avg_cost
    return position, avg_cost, realized, mid


def latest_cycle_row_before(symbol, epoch_cutoff):
    """Last cycle-log row at/before a given epoch, or None. Used as a mid
    fallback when the live tick buffer doesn't reach back far enough."""
    cyc = load_cycles(symbol, window_minutes=None)
    if cyc.empty:
        return None
    cyc_valid = cyc[cyc["timestamp"].apply(lambda t: t.timestamp()) <= epoch_cutoff]
    cyc_valid = cyc_valid.dropna(subset=["mid"])
    return cyc_valid.iloc[-1] if not cyc_valid.empty else None


def symbol_live_stats(symbol):
    """Current (right now) position/avg_cost/realized_pnl/unrealized_pnl for
    one symbol - used for the per-ticker realized/unrealized readouts in the
    live dashboard view. Same roll-forward-from-cached-history technique as
    avgcost_state_at_global_anchor, just anchored to wall-clock 'now' instead
    of a shared cross-symbol timestamp."""
    now_epoch = time.time()
    now_ts = pd.Timestamp.now(tz="UTC")
    avgcost_df = compute_avgcost_series(symbol)
    position, avg_cost, realized, mid = avgcost_state_at_global_anchor(symbol, avgcost_df, now_ts, now_epoch)
    unrealized = position * (mid - avg_cost) if mid is not None else 0.0
    return {
        "position": position, "avg_cost": avg_cost,
        "realized_pnl": realized, "unrealized_pnl": unrealized, "mid": mid,
    }


def state_at(per_symbol_df, at_time):
    """(position, cash_pnl, mid) as-of at_time for one symbol's series,
    via asof lookup (backward) - 0/0/0 if at_time predates any data."""
    if per_symbol_df.empty:
        return 0.0, 0.0, 0.0
    anchor_df = pd.DataFrame({"timestamp": [at_time]})
    merged = pd.merge_asof(anchor_df, per_symbol_df, on="timestamp", direction="backward")
    row = merged.iloc[0]
    pos = row.get("position")
    cash = row.get("cash_pnl")
    mid = row.get("mid")
    return (float(pos) if pd.notna(pos) else 0.0,
            float(cash) if pd.notna(cash) else 0.0,
            float(mid) if pd.notna(mid) else 0.0)


def state_at_global_anchor(symbol, per_symbol_df, global_anchor_time, global_anchor_epoch):
    """(position, cash_pnl, mid) for one symbol AT the global anchor instant,
    correctly handling symbols whose own cycle log samples at different
    times than the other symbol's (SOXL/TQQQ cron cycles fire independently,
    not in lockstep).

    Bug this fixes: naively calling state_at(per_symbol_df, global_anchor_time)
    does a backward asof lookup against that symbol's OWN sample times, which
    silently stops at the symbol's last cycle sample BEFORE the global
    anchor - any fills that happened in the gap between this symbol's last
    cycle and the (later-firing) other symbol's cycle are counted in
    NEITHER the historical state NOR the live forward-replay (which only
    starts polling fills strictly after the global anchor). Confirmed via a
    live apples-to-apples diff: build_mm_pnl_curve()'s "now" P&L differed
    from a from-scratch recompute (full CSV fills + live mid) by a
    persistent ~$10, not explained by price-tick timing noise alone.
    Fix: after the normal backward asof lookup, roll forward through any
    fills strictly between this symbol's own last known timestamp and the
    global anchor using the live-poller fills buffer (which holds a much
    longer rolling history than just "after the global anchor")."""
    if per_symbol_df.empty:
        own_last_time = None
        pos, cash, mid = 0.0, 0.0, 0.0
    else:
        own_last_time = per_symbol_df["timestamp"].iloc[-1]
        pos, cash, mid = state_at(per_symbol_df, global_anchor_time)
        # mid may be stale if this symbol's last sample predates the anchor
        # by a while - prefer a live tick at/before the anchor if available.
        live_mid = find_mid_at_or_before(symbol, global_anchor_epoch)
        if live_mid is not None:
            mid = live_mid
    if own_last_time is not None and own_last_time < global_anchor_time:
        for f in fills_after(symbol, own_last_time.timestamp(), epoch_upto=global_anchor_epoch):
            sign = 1.0 if f["side"] == "buy" else -1.0
            pos += sign * f["qty"]
            cash -= sign * f["qty"] * f["price"]
    return pos, cash, mid


_live_json_cache = {}  # path -> (mtime, parsed_doc) - avoids re-reading+re-parsing live_poller.py's JSON files on every poll


def load_json_cached(path):
    """mtime-gated cache for live_poller.py's JSON files (ticks/orders/fills).
    These can be large (tick buffers) and are re-read on every dashboard
    poll (every 1-5s) - without caching, find_mid_at_or_before alone was
    costing ~0.15-0.25s per call just in json.load() (confirmed via
    cProfile), and symbol_live_stats calls it per-symbol per-poll. Same
    mtime-gating pattern as _load_parsed_csv_cached for the CSV logs."""
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    cached = _live_json_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception:
        return None
    _live_json_cache[path] = (mtime, doc)
    return doc


def find_mid_at_or_before(symbol, epoch_cutoff):
    """Last known quote mid at/before a given unix epoch, from the live tick
    buffer. Returns None if the buffer doesn't reach back that far.
    Scans BACKWARD (ticks are appended in chronological order) and returns
    on the first match - for the common case of epoch_cutoff='now' this is
    O(1) instead of the full O(n) forward scan the first version did, which
    showed up as the dominant cost (~0.15-0.25s/call) in symbol_live_stats
    profiling once called per-symbol on every /api/live poll."""
    doc = load_json_cached(live_ticks_path(symbol)) or {"ticks": []}
    for t in reversed(doc.get("ticks", [])):
        if t.get("type") == "quote" and t.get("epoch", 0) <= epoch_cutoff:
            return t["mid"]
    return None


def datetime_from_epoch(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()


_pnl_curve_cache = {}  # window_minutes -> (computed_at, result) - short TTL
_PNL_CURVE_TTL_SEC = 1.0


def build_mm_pnl_curve(window_minutes):
    """Thin TTL-cache wrapper around _build_mm_pnl_curve_uncached. The
    dashboard polls /api/equity roughly once per second and can have
    multiple tabs/clients open simultaneously; without this, concurrent
    requests for the same window would each redo the full ~0.3-0.4s
    computation. A short TTL keeps data fresh (same cadence as the poll
    itself) while collapsing near-simultaneous duplicate requests."""
    now = time.time()
    cached = _pnl_curve_cache.get(window_minutes)
    if cached is not None and (now - cached[0]) < _PNL_CURVE_TTL_SEC:
        return cached[1]
    result = _build_mm_pnl_curve_uncached(window_minutes)
    _pnl_curve_cache[window_minutes] = (now, result)
    return result


def _build_mm_pnl_curve_uncached(window_minutes):
    """MM-bot-only cumulative P&L curve (SOXL + TQQQ combined), isolated from
    the account's other bot (tradeSpy.py / SPY). Historical portion sampled
    at each cycle-log timestamp (either symbol); live tail replays ticks +
    fills forward from the most recent cycle sample using live_poller.py's
    websocket buffers, same bootstrap technique as the old account-equity
    version but anchored to pure strategy P&L instead of broker equity.

    Also tracks combined REALIZED P&L (avg-cost-basis) alongside the existing
    total (cash_pnl+mark-to-market) P&L - realized only changes at fill
    events, never at price ticks, so its tail only needs to replay fills
    (cheaper than the total-P&L tail, which must react to every tick too).
    See apply_fill_avgcost's docstring for the accounting identity connecting
    the two computations - this is a parallel, independently-computed series,
    not derived from the total P&L numbers."""
    per_symbol = {s: per_symbol_mtm_series(s) for s in SYMBOLS}
    avgcost_per_symbol = {s: compute_avgcost_series(s) for s in SYMBOLS}
    all_times = sorted(pd.concat(
        [per_symbol[s]["timestamp"] for s in SYMBOLS if not per_symbol[s].empty]
    ).unique()) if any(not per_symbol[s].empty for s in SYMBOLS) else []
    if not all_times:
        return {"history": [], "tail": [], "day_start_pnl": None,
                "realized_history": [], "realized_tail": []}

    axis = pd.DataFrame({"timestamp": all_times})
    total_pnl = pd.Series(0.0, index=axis.index)
    realized_pnl = pd.Series(0.0, index=axis.index)
    for s in SYMBOLS:
        ser = per_symbol[s]
        if not ser.empty:
            m = pd.merge_asof(axis, ser, on="timestamp", direction="backward")
            contrib = m["cash_pnl"].fillna(0.0) + m["position"].fillna(0.0) * m["mid"].fillna(0.0)
            total_pnl += contrib
        aser = avgcost_per_symbol[s]
        if not aser.empty:
            ma = pd.merge_asof(axis, aser, on="timestamp", direction="backward")
            realized_pnl += ma["realized_pnl"].fillna(0.0)
    axis["pnl"] = total_pnl
    axis["realized_pnl"] = realized_pnl

    anchor_time = axis["timestamp"].iloc[-1]
    anchor_epoch = anchor_time.timestamp()

    pos_state, cash_state, mid_state = {}, {}, {}
    for s in SYMBOLS:
        pos_state[s], cash_state[s], mid_state[s] = state_at_global_anchor(
            s, per_symbol[s], anchor_time, anchor_epoch)

    # recompute anchor_pnl from this authoritative per-symbol state (the
    # earlier "axis"-based sampling undercounts cross-symbol gap fills - see
    # state_at_global_anchor's docstring) instead of trusting axis["pnl"].iloc[-1]
    anchor_pnl = sum(cash_state[s] + pos_state[s] * mid_state[s] for s in SYMBOLS)

    # same cross-symbol-gap fix for realized P&L, via avgcost_state_at_global_anchor
    avgcost_pos, avgcost_cost, avgcost_realized = {}, {}, {}
    for s in SYMBOLS:
        p, c, r, _mid = avgcost_state_at_global_anchor(s, avgcost_per_symbol[s], anchor_time, anchor_epoch)
        avgcost_pos[s], avgcost_cost[s], avgcost_realized[s] = p, c, r
    anchor_realized = sum(avgcost_realized.values())

    # forward events after the anchor: every live tick + live fill, both symbols
    events = []
    for s in SYMBOLS:
        doc = load_json_cached(live_ticks_path(s)) or {"ticks": []}
        for t in doc.get("ticks", []):
            if t.get("type") == "quote" and t.get("epoch", 0) > anchor_epoch:
                events.append((t["epoch"], "tick", s, t["mid"], None))
        for f in fills_after(s, anchor_epoch):
            events.append((f["epoch"], "fill", s, None, f))
    events.sort(key=lambda e: e[0])

    tail = [{"t": anchor_time.isoformat(), "pnl": anchor_pnl}]
    realized_tail = [{"t": anchor_time.isoformat(), "realized_pnl": anchor_realized}]
    for epoch, kind, s, mid, extra in events:
        if kind == "tick":
            mid_state[s] = mid
        else:
            f = extra
            sign = 1.0 if f["side"] == "buy" else -1.0
            pos_state[s] += sign * f["qty"]
            cash_state[s] -= sign * f["qty"] * f["price"]
            avgcost_pos[s], avgcost_cost[s], avgcost_realized[s] = apply_fill_avgcost(
                avgcost_pos[s], avgcost_cost[s], avgcost_realized[s], f["side"], f["qty"], f["price"])
            realized_tail.append({"t": datetime_from_epoch(epoch), "realized_pnl": sum(avgcost_realized.values())})
        pnl_now = sum(cash_state[s] + pos_state[s] * mid_state[s] for s in SYMBOLS)
        tail.append({"t": datetime_from_epoch(epoch), "pnl": pnl_now})

    # hist_points excludes the final axis row - tail[0] (built from the
    # authoritative state_at_global_anchor state, not axis["pnl"]) already
    # represents that exact instant correctly; keeping both would plot two
    # different P&L values at the same timestamp (a visible vertical jump).
    # Vectorized (no .iterrows()) - iterrows() over a multi-thousand-row axis
    # was the single biggest remaining cost in this function (confirmed via
    # cProfile: ~1.9s/call cumulative), and this function runs on every
    # /api/equity poll (every 1-5s from the dashboard).
    trimmed = axis.iloc[:-1]
    if window_minutes:
        cutoff = anchor_time - pd.Timedelta(minutes=window_minutes)
        trimmed = trimmed[trimmed["timestamp"] >= cutoff]
    # strftime once and reuse for both series - calling .dt.strftime() twice
    # on the same index cost ~0.38s each (measured via cProfile), the single
    # biggest cost in this whole function once per-symbol series got cached.
    ts_strs = trimmed["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    hist_points = [{"t": t, "pnl": p} for t, p in zip(ts_strs, trimmed["pnl"])]
    realized_hist_points = [{"t": t, "realized_pnl": p} for t, p in zip(ts_strs, trimmed["realized_pnl"])]

    MAX_TAIL_POINTS = 600
    if len(tail) > MAX_TAIL_POINTS:
        step = len(tail) / MAX_TAIL_POINTS
        sampled = [tail[int(i * step)] for i in range(MAX_TAIL_POINTS)]
        if sampled[-1] is not tail[-1]:
            sampled.append(tail[-1])
        tail = sampled
    # realized_tail is naturally far smaller (only grows on fills, not every
    # tick), but cap it the same way for consistency/safety on a busy day.
    if len(realized_tail) > MAX_TAIL_POINTS:
        step = len(realized_tail) / MAX_TAIL_POINTS
        sampled = [realized_tail[int(i * step)] for i in range(MAX_TAIL_POINTS)]
        if sampled[-1] is not realized_tail[-1]:
            sampled.append(realized_tail[-1])
        realized_tail = sampled

    day = anchor_time.date()
    day_rows = axis[axis["timestamp"].dt.date == day]
    day_start_pnl = float(day_rows.iloc[0]["pnl"]) if not day_rows.empty else None

    return {"history": hist_points, "tail": tail, "day_start_pnl": day_start_pnl,
            "realized_history": realized_hist_points, "realized_tail": realized_tail}


_symbol_pnl_curve_cache = {}  # (symbol, window_minutes) -> (computed_at, result) - same TTL pattern as _pnl_curve_cache
_SYMBOL_PNL_CURVE_TTL_SEC = 1.0


def build_symbol_pnl_curve(symbol, window_minutes):
    """Thin TTL-cache wrapper around _build_symbol_pnl_curve_uncached, same
    pattern as build_mm_pnl_curve - this is polled per-symbol per-tick by
    the dashboard's new per-ticker P&L panel."""
    now = time.time()
    key = (symbol, window_minutes)
    cached = _symbol_pnl_curve_cache.get(key)
    if cached is not None and (now - cached[0]) < _SYMBOL_PNL_CURVE_TTL_SEC:
        return cached[1]
    result = _build_symbol_pnl_curve_uncached(symbol, window_minutes)
    _symbol_pnl_curve_cache[key] = (now, result)
    return result


def _build_symbol_pnl_curve_uncached(symbol, window_minutes):
    """Single-symbol realized/unrealized P&L curve, for the per-ticker P&L
    panel under each symbol's price chart. Mirrors _build_mm_pnl_curve_uncached
    but for exactly one symbol instead of combining SOXL+TQQQ+GME, and splits
    out unrealized (position*(mid-avg_cost)) alongside realized instead of
    only tracking the cash_pnl-based total - unrealized = total - realized,
    per the accounting identity documented in apply_fill_avgcost's docstring.

    Reuses state_at_global_anchor / avgcost_state_at_global_anchor exactly as
    the combined curve does (both already take a single symbol), so the same
    cross-symbol-gap-timing fixes apply here for free."""
    per_sym = per_symbol_mtm_series(symbol)
    avgcost_df = compute_avgcost_series(symbol)
    if per_sym.empty:
        return {"history": [], "tail": []}

    axis = per_sym[["timestamp"]].copy()
    total_pnl = per_sym["cash_pnl"] + per_sym["position"] * per_sym["mid"]
    if not avgcost_df.empty:
        merged = pd.merge_asof(axis, avgcost_df[["timestamp", "realized_pnl"]], on="timestamp", direction="backward")
        realized_pnl = merged["realized_pnl"].fillna(0.0)
    else:
        realized_pnl = pd.Series(0.0, index=axis.index)
    axis["realized_pnl"] = realized_pnl
    axis["unrealized_pnl"] = total_pnl - realized_pnl

    anchor_time = axis["timestamp"].iloc[-1]
    anchor_epoch = anchor_time.timestamp()

    pos_state, cash_state, mid_state = state_at_global_anchor(symbol, per_sym, anchor_time, anchor_epoch)
    anchor_total = cash_state + pos_state * mid_state
    avgcost_pos, avgcost_cost, avgcost_realized, _mid = avgcost_state_at_global_anchor(
        symbol, avgcost_df, anchor_time, anchor_epoch)
    anchor_realized = avgcost_realized
    anchor_unrealized = anchor_total - anchor_realized

    # forward events after the anchor: this symbol's live ticks + live fills only
    events = []
    doc = load_json_cached(live_ticks_path(symbol)) or {"ticks": []}
    for t in doc.get("ticks", []):
        if t.get("type") == "quote" and t.get("epoch", 0) > anchor_epoch:
            events.append((t["epoch"], "tick", t["mid"], None))
    for f in fills_after(symbol, anchor_epoch):
        events.append((f["epoch"], "fill", None, f))
    events.sort(key=lambda e: e[0])

    tail = [{"t": anchor_time.isoformat(), "realized_pnl": anchor_realized, "unrealized_pnl": anchor_unrealized}]
    mid_now = mid_state
    for epoch, kind, mid, extra in events:
        if kind == "tick":
            mid_now = mid
        else:
            f = extra
            sign = 1.0 if f["side"] == "buy" else -1.0
            pos_state += sign * f["qty"]
            cash_state -= sign * f["qty"] * f["price"]
            avgcost_pos, avgcost_cost, avgcost_realized = apply_fill_avgcost(
                avgcost_pos, avgcost_cost, avgcost_realized, f["side"], f["qty"], f["price"])
        total_now = cash_state + pos_state * mid_now
        realized_now = avgcost_realized
        unrealized_now = total_now - realized_now
        tail.append({"t": datetime_from_epoch(epoch), "realized_pnl": realized_now, "unrealized_pnl": unrealized_now})

    trimmed = axis.iloc[:-1]
    if window_minutes:
        cutoff = anchor_time - pd.Timedelta(minutes=window_minutes)
        trimmed = trimmed[trimmed["timestamp"] >= cutoff]
    ts_strs = trimmed["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    hist_points = [{"t": t, "realized_pnl": r, "unrealized_pnl": u}
                   for t, r, u in zip(ts_strs, trimmed["realized_pnl"], trimmed["unrealized_pnl"])]

    MAX_TAIL_POINTS = 600
    if len(tail) > MAX_TAIL_POINTS:
        step = len(tail) / MAX_TAIL_POINTS
        sampled = [tail[int(i * step)] for i in range(MAX_TAIL_POINTS)]
        if sampled[-1] is not tail[-1]:
            sampled.append(tail[-1])
        tail = sampled

    return {"history": hist_points, "tail": tail}


@app.route("/api/symbol_equity")
def api_symbol_equity():
    """Per-ticker realized/unrealized P&L history, for the panel under each
    symbol's price chart."""
    symbol = request.args.get("symbol", "SOXL").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"unknown symbol {symbol}"}), 400
    window_minutes_raw = request.args.get("window", "480")
    window_minutes = None if window_minutes_raw == "all" else int(window_minutes_raw)
    curve = build_symbol_pnl_curve(symbol, window_minutes)
    return jsonify({
        "symbol": symbol,
        "history": curve["history"],
        "tail": curve["tail"],
        "server_time": pd.Timestamp.utcnow().isoformat(),
    })


def fills_after(symbol, epoch_after, epoch_upto=None):
    """Live fills for a symbol strictly after epoch_after (optionally capped
    at epoch_upto), sorted ascending by time. Reads the full live-fills
    buffer (up to FILLS_BUFFER_MINUTES in live_poller.py), not window-limited."""
    doc = load_json_cached(live_fills_path(symbol)) or {"fills": []}
    out = [f for f in doc.get("fills", []) if f.get("epoch", 0) > epoch_after
           and (epoch_upto is None or f.get("epoch", 0) <= epoch_upto)]
    out.sort(key=lambda f: f["epoch"])
    return out


@app.route("/api/equity")
def api_equity():
    window_minutes_raw = request.args.get("window", "480")
    window_minutes = None if window_minutes_raw == "all" else int(window_minutes_raw)
    curve = build_mm_pnl_curve(window_minutes)
    return jsonify({
        "history": curve["history"],
        "tail": curve["tail"],
        "day_start_pnl": curve["day_start_pnl"],
        "realized_history": curve["realized_history"],
        "realized_tail": curve["realized_tail"],
        "live_poller_alive": live_poller_alive(),
        "server_time": pd.Timestamp.utcnow().isoformat(),
    })


@app.route("/api/live")
def api_live():
    """Tick-level feed for the fast/zoomed-in view (default: last N minutes
    of real quote/trade ticks, current resting order prices, instant fills)."""
    symbol = request.args.get("symbol", "SOXL").upper()
    window_seconds = int(request.args.get("window_seconds", 120))
    if symbol not in SYMBOLS:
        return jsonify({"error": f"unknown symbol {symbol}"}), 400

    alive = live_poller_alive()
    ticks_doc = load_json_cached(live_ticks_path(symbol)) or {"ticks": []}
    orders_doc = load_json_cached(live_orders_path(symbol)) or {}
    fills_doc = load_json_cached(live_fills_path(symbol)) or {"fills": []}

    cutoff = time.time() - window_seconds
    ticks = [t for t in ticks_doc.get("ticks", []) if t.get("epoch", 0) >= cutoff]
    fills = [f for f in fills_doc.get("fills", []) if f.get("epoch", 0) >= cutoff]

    # Separate quote ticks (bid/ask/mid line) from trade prints (actual executed trades elsewhere in market).
    # Exchange + size passed through for the dashboard's new point encoding:
    # point size = quoted/traded size, point color = exchange code.
    quote_points = [{"t": t["t"], "mid": t["mid"], "bid": t["bid"], "ask": t["ask"],
                      "bx": t.get("bx"), "ax": t.get("ax"), "bs": t.get("bs"), "as": t.get("as")}
                     for t in ticks if t.get("type") == "quote"]
    trade_points = [{"t": t["t"], "price": t["price"], "size": t.get("size"), "exchange": t.get("exchange")}
                     for t in ticks if t.get("type") == "trade"]

    # fall back to the cron state file for order prices if the live stream
    # hasn't seen an order event yet (e.g. poller just restarted before the
    # next requote) - better than showing nothing.
    cron_state = load_state(symbol)
    buy_order = orders_doc.get("buy")
    sell_order = orders_doc.get("sell")
    if not buy_order and cron_state.get("open_buy_price"):
        buy_order = {"price": cron_state["open_buy_price"], "order_id": cron_state.get("open_buy_order_id"), "updated_at": "fallback_cron_state"}
    if not sell_order and cron_state.get("open_sell_price"):
        sell_order = {"price": cron_state["open_sell_price"], "order_id": cron_state.get("open_sell_order_id"), "updated_at": "fallback_cron_state"}

    pnl_stats = symbol_live_stats(symbol)

    return jsonify({
        "symbol": symbol,
        "live_poller_alive": alive,
        "quote_ticks": quote_points,
        "trade_ticks": trade_points,
        "buy_order": buy_order,
        "sell_order": sell_order,
        "fills": fills,
        "cron_state": cron_state,
        "realized_pnl": pnl_stats["realized_pnl"],
        "unrealized_pnl": pnl_stats["unrealized_pnl"],
        "position": pnl_stats["position"],
        "avg_cost": pnl_stats["avg_cost"],
        "server_time": pd.Timestamp.utcnow().isoformat(),
    })


@app.route("/api/data")
def api_data():
    """Slower/longer-window view sourced from marketMaker.py's own cron-cycle
    CSV logs - used for the 1h+ windows where tick granularity isn't needed."""
    symbol = request.args.get("symbol", "SOXL").upper()
    window_minutes = int(request.args.get("window", 120))
    if symbol not in SYMBOLS:
        return jsonify({"error": f"unknown symbol {symbol}"}), 400

    cycles = load_cycles(symbol, window_minutes)
    fills = load_fills_csv(symbol, window_minutes)
    state = load_state(symbol)

    live = cycles[(cycles.get("halted") != True) & cycles["mid"].notna()] if not cycles.empty else cycles  # noqa: E712

    price_series = []
    buy_steps = []
    sell_steps = []
    if not live.empty:
        for _, row in live.iterrows():
            ts = row["timestamp"].isoformat()
            price_series.append({"t": ts, "mid": row["mid"], "bid": row["bid"], "ask": row["ask"]})
            if row.get("buy_action") == "PLACED" and pd.notna(row.get("buy_price")):
                buy_steps.append({"t": ts, "price": row["buy_price"]})
            if row.get("sell_action") == "PLACED" and pd.notna(row.get("sell_price")):
                sell_steps.append({"t": ts, "price": row["sell_price"]})

    fill_points = []
    if not fills.empty:
        for _, row in fills.iterrows():
            fill_points.append({
                "t": row["timestamp"].isoformat(),
                "price": row["filled_avg_price"],
                "side": row["side"],
                "qty": row["qty"],
            })

    latest = cycles.iloc[-1].to_dict() if not cycles.empty else {}

    def clean(v):
        if isinstance(v, float) and pd.isna(v):
            return None
        return v

    latest_clean = {k: clean(v) for k, v in latest.items()}
    if "timestamp" in latest_clean and latest_clean["timestamp"] is not None:
        latest_clean["timestamp"] = pd.Timestamp(latest["timestamp"]).isoformat()

    return jsonify({
        "symbol": symbol,
        "price_series": price_series,
        "buy_steps": buy_steps,
        "sell_steps": sell_steps,
        "fills": fill_points,
        "latest_cycle": latest_clean,
        "state": state,
        "server_time": pd.Timestamp.utcnow().isoformat(),
    })


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Market Maker Live Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  :root { --bg:#02233F; --grid:#274066; --txt:#ffffff; --buy:#2E9E4E; --sell:#D9453D; --mid:#4C9BE0; --trade:#C9A6FF; }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--txt); font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:16px; }
  h1 { font-size: 20px; margin: 0 0 4px 0; }
  .sub { color: var(--txt); opacity: 0.75; font-size: 12px; margin-bottom: 14px; }
  .live-badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:bold; margin-left:8px; }
  .live-on { background:#1f4d2e; color:#6FE08A; }
  .live-off { background:#4d1f1f; color:#FF8A7A; }
  .controls { display:flex; gap:16px; align-items:center; margin-bottom:14px; flex-wrap:wrap; }
  .controls label { font-size:13px; }
  select { background:#0a2d4e; color:var(--txt); border:1px solid var(--grid); border-radius:4px; padding:4px 8px; }
  .grid { display:flex; gap:20px; flex-wrap:wrap; }
  .panel { flex:1 1 560px; min-width:360px; background:#031b33; border:1px solid var(--grid); border-radius:8px; padding:14px; }
  .panel h2 { margin:0 0 8px 0; font-size:16px; }
  .stats { display:flex; gap:18px; flex-wrap:wrap; margin-bottom:10px; font-size:13px; }
  .stat { background:#0a2d4e; border-radius:6px; padding:6px 10px; }
  .stat b { display:block; font-size:15px; }
  .pos-pos { color:#6FE08A; }
  .pos-neg { color:#FF8A7A; }
  .halted { color:#FFCF5C; font-weight:bold; }
  .legend { font-size:12px; opacity:0.85; margin-top:6px; }
  .legend span { margin-right:14px; }
  canvas { max-height: 440px; }
  canvas.pnl-canvas { max-height: 160px; margin-top: 10px; }
  .pnl-hero { display:flex; gap:16px; font-size:12px; margin-top:8px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  .flash-buy { animation: flashBuy 0.6s ease-out; }
  .flash-sell { animation: flashSell 0.6s ease-out; }
  @keyframes flashBuy { 0% { background: #2E9E4E55; } 100% { background: transparent; } }
  @keyframes flashSell { 0% { background: #D9453D55; } 100% { background: transparent; } }
  .equity-panel { background:#031b33; border:1px solid var(--grid); border-radius:8px; padding:14px; margin-bottom:20px; }
  .equity-hero { display:flex; gap:28px; align-items:baseline; flex-wrap:wrap; margin-bottom:10px; }
  .equity-hero .big { font-size:34px; font-weight:bold; }
  .equity-hero .pnl-line { font-size:18px; }
  canvas#chart-equity { max-height: 220px; }
</style>
</head>
<body>
  <h1>Market Maker Live Dashboard <span id="liveBadge" class="live-badge live-off">connecting</span></h1>
  <div class="sub" id="serverTime">connecting...</div>
  <div class="equity-panel">
    <h2 style="margin:0 0 8px 0; font-size:16px;">Market Maker Bot P&amp;L (SOXL + TQQQ + GME only, mark-to-market)</h2>
    <div class="equity-hero">
      <div><div style="font-size:12px;opacity:0.75;">Cumulative P&amp;L</div><div class="big" id="equityNow">-</div></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Realized</div><span id="equityRealized">-</span></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Today</div><span id="equityDayPnl">-</span></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Window</div><span id="equityWindowPnl">-</span></div>
    </div>
    <canvas id="chart-equity"></canvas>
    <div class="legend"><span><span class="dot" style="background:#4C9BE0"></span>total P&amp;L (mark-to-market)</span><span><span class="dot" style="background:#ffffff"></span>realized P&amp;L</span></div>
  </div>
  <div class="controls">
    <label>View:
      <select id="modeSel">
        <option value="live" selected>Live tick (last 2 min)</option>
        <option value="cycle">Cycle history (6-min snapshots)</option>
      </select>
    </label>
    <label id="liveWindowWrap">Window:
      <select id="liveWindowSel">
        <option value="60">Last 1 min</option>
        <option value="120" selected>Last 2 min</option>
        <option value="300">Last 5 min</option>
        <option value="600">Last 10 min</option>
      </select>
    </label>
    <label id="cycleWindowWrap" style="display:none">Window:
      <select id="cycleWindowSel">
        <option value="60">Last 1 hour</option>
        <option value="240" selected>Last 4 hours</option>
        <option value="480">Full session (8h)</option>
      </select>
    </label>
    <label>Refresh:
      <select id="refreshSel">
        <option value="500">0.5s</option>
        <option value="1000" selected>1s</option>
        <option value="2000">2s</option>
      </select>
    </label>
  </div>
  <div class="grid">
    <div class="panel">
      <h2>SOXL</h2>
      <div class="stats" id="stats-SOXL"></div>
      <canvas id="chart-SOXL"></canvas>
      <div class="legend" id="legend-SOXL"></div>
      <canvas class="pnl-canvas" id="pnlchart-SOXL"></canvas>
      <div class="legend"><span><span class="dot" style="background:#ffffff"></span>realized P&amp;L</span><span><span class="dot" style="background:#C9A6FF"></span>unrealized P&amp;L</span></div>
    </div>
    <div class="panel">
      <h2>TQQQ</h2>
      <div class="stats" id="stats-TQQQ"></div>
      <canvas id="chart-TQQQ"></canvas>
      <div class="legend" id="legend-TQQQ"></div>
      <canvas class="pnl-canvas" id="pnlchart-TQQQ"></canvas>
      <div class="legend"><span><span class="dot" style="background:#ffffff"></span>realized P&amp;L</span><span><span class="dot" style="background:#C9A6FF"></span>unrealized P&amp;L</span></div>
    </div>
    <div class="panel">
      <h2>GME</h2>
      <div class="stats" id="stats-GME"></div>
      <canvas id="chart-GME"></canvas>
      <div class="legend" id="legend-GME"></div>
      <canvas class="pnl-canvas" id="pnlchart-GME"></canvas>
      <div class="legend"><span><span class="dot" style="background:#ffffff"></span>realized P&amp;L</span><span><span class="dot" style="background:#C9A6FF"></span>unrealized P&amp;L</span></div>
    </div>
  </div>

<script>
const GRID = '#274066', TXT = '#ffffff', BUY = '#2E9E4E', SELL = '#D9453D', MID = '#4C9BE0', TRADE = '#C9A6FF';
const charts = {};
const pnlCharts = {};
const lastFillCount = {SOXL: 0, TQQQ: 0, GME: 0};

// Alpaca exchange code -> name + a stable color per exchange (so the same
// venue always renders the same color across symbols/sessions). IEX free
// feed effectively only ever reports 'V', but this covers the full code
// set in case of a SIP/paid-feed upgrade later.
const EXCHANGE_NAMES = {
  V: 'IEX', Q: 'Nasdaq', N: 'NYSE', P: 'NYSE Arca', A: 'NYSE American',
  B: 'Nasdaq BX', X: 'Nasdaq PSX', Y: 'Cboe BYX', K: 'Cboe EDGX', J: 'Cboe EDGA',
  Z: 'Cboe BZ', M: 'Chicago', C: 'National', U: 'MEMX', L: 'LTSE', H: 'MIAX',
  I: 'ISE', S: 'Nasdaq Small Cap', T: 'Nasdaq Int', D: 'FINRA ADF', E: 'Market Indep.',
};
const EXCHANGE_COLORS = {
  V: '#4C9BE0', Q: '#E08A3C', N: '#E04C8A', P: '#8A4CE0', A: '#4CE0C9',
  B: '#E0D24C', X: '#9BE04C', Y: '#E04C4C', K: '#4CE06B', J: '#C94CE0',
};
function exchangeColor(code) { return EXCHANGE_COLORS[code] || '#9AA6B8'; }
function exchangeName(code) { return EXCHANGE_NAMES[code] || code || 'unknown'; }
function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0,2), 16), g = parseInt(h.substring(2,4), 16), b = parseInt(h.substring(4,6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
const TRADE_POINT_ALPHA = 0.65; // trade prints rendered semi-transparent (0.5-0.75 band) so overlapping points stay readable

const LEGEND_LIVE = `
  <span>&#9679; trade print (size = trade size, color = exchange)</span>
  <span><span class="dot" style="background:${BUY}"></span>your resting buy</span>
  <span><span class="dot" style="background:${SELL}"></span>your resting sell</span>
  <span>&#9632; your fill (size = qty)</span>`;
const LEGEND_CYCLE = `
  <span><span class="dot" style="background:${MID}"></span>mid price (6-min cycles)</span>
  <span><span class="dot" style="background:${BUY}"></span>resting buy</span>
  <span><span class="dot" style="background:${SELL}"></span>resting sell</span>
  <span>&#9632; fill (size = qty)</span>`;

function makeChart(symbol) {
  const ctx = document.getElementById('chart-' + symbol).getContext('2d');
  charts[symbol] = new Chart(ctx, {
    type: 'scatter',
    data: { datasets: [
      { label: 'mid', data: [], showLine: false, pointBackgroundColor: [], pointBorderColor: [],
        pointBorderWidth: 0.5, pointRadius: [], parsing: {xAxisKey:'t', yAxisKey:'price'}, order: 4 },
      { label: 'trades', data: [], showLine: false, pointBackgroundColor: [], pointBorderColor: '#ffffff',
        pointBorderWidth: 0.5, pointRadius: [], parsing: {xAxisKey:'t', yAxisKey:'price'}, order: 3 },
      { label: 'resting buy', data: [], borderColor: BUY, borderDash: [5,3], borderWidth: 2,
        stepped: true, pointRadius: 0, parsing: {xAxisKey:'t', yAxisKey:'price'}, order: 2, showLine: true },
      { label: 'resting sell', data: [], borderColor: SELL, borderDash: [5,3], borderWidth: 2,
        stepped: true, pointRadius: 0, parsing: {xAxisKey:'t', yAxisKey:'price'}, order: 2, showLine: true },
      { label: 'fills', data: [], parsing: {xAxisKey:'t', yAxisKey:'price'},
        pointBackgroundColor: [], pointBorderColor: '#ffffff', pointBorderWidth: 1,
        pointStyle: 'rect', pointRadius: [], order: 1, showLine: false },
    ]},
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { type: 'time', time: { unit: 'second', displayFormats: { second: 'HH:mm:ss' } },
             ticks: { color: TXT, maxTicksLimit: 8 }, grid: { color: GRID } },
        y: { ticks: { color: TXT }, grid: { color: GRID } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function(c) {
              const d = c.raw;
              if (c.dataset.label === 'fills') return `${d.side.toUpperCase()} filled ${d.qty}sh @ ${d.price}`;
              if (c.dataset.label === 'trades') return `trade ${d.price} x${d.size||''} @ ${exchangeName(d.exchange)}`;
              if (c.dataset.label === 'mid') return `NBBO ${d.price} (depth ${d.depth||''} @ ${exchangeName(d.exchange)})`;
              return `${c.dataset.label}: ${d.price}`;
            }
          }
        }
      }
    }
  });
}

function fmtNum(v, dp=2) { return (v === null || v === undefined || isNaN(v)) ? '-' : Number(v).toFixed(dp); }

function makePnlChart(symbol) {
  const ctx = document.getElementById('pnlchart-' + symbol).getContext('2d');
  pnlCharts[symbol] = new Chart(ctx, {
    type: 'line',
    data: { datasets: [
      { label: 'realized', data: [], borderColor: '#ffffff', backgroundColor: 'transparent', fill: false,
        borderWidth: 1.5, pointRadius: 0, stepped: true, tension: 0,
        parsing: {xAxisKey:'t', yAxisKey:'realized_pnl'}, order: 1 },
      { label: 'unrealized', data: [], borderColor: TRADE, backgroundColor: TRADE + '22', fill: false,
        borderWidth: 1.5, pointRadius: 0, tension: 0.08,
        parsing: {xAxisKey:'t', yAxisKey:'unrealized_pnl'}, order: 2 },
    ]},
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { type: 'time', time: { unit: 'second', displayFormats: { second: 'HH:mm:ss' } },
             ticks: { color: TXT, maxTicksLimit: 6 }, grid: { color: GRID } },
        y: { ticks: { color: TXT, callback: v => '$' + v.toLocaleString() }, grid: { color: GRID } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function(c) {
          const label = c.dataset.label === 'realized' ? 'Realized' : 'Unrealized';
          const val = c.dataset.label === 'realized' ? c.raw.realized_pnl : c.raw.unrealized_pnl;
          return `${label}: $${Number(val).toLocaleString(undefined, {minimumFractionDigits:2})}`;
        } } }
      }
    }
  });
}

async function refreshSymbolPnl(symbol, windowMin) {
  try {
    const res = await fetch(`/api/symbol_equity?symbol=${symbol}&window=${windowMin}`);
    const d = await res.json();
    const chart = pnlCharts[symbol];
    const points = (d.history || []).concat(d.tail || []);
    if (!points.length) return;
    chart.data.datasets[0].data = points.map(p => ({ t: p.t, realized_pnl: p.realized_pnl }));
    chart.data.datasets[1].data = points.map(p => ({ t: p.t, unrealized_pnl: p.unrealized_pnl }));
    chart.update('none');
  } catch (e) { console.error('symbol pnl', symbol, e); }
}

function flashPanel(symbol, side) {
  const panel = document.getElementById('chart-' + symbol).closest('.panel');
  panel.classList.remove('flash-buy', 'flash-sell');
  void panel.offsetWidth; // restart animation
  panel.classList.add(side === 'buy' ? 'flash-buy' : 'flash-sell');
}

async function refreshLive(symbol, windowSec) {
  try {
    const res = await fetch(`/api/live?symbol=${symbol}&window_seconds=${windowSec}`);
    const d = await res.json();
    const chart = charts[symbol];

    // NBBO quote points removed from the live view per James's request -
    // dataset[0] (mid/NBBO) stays empty in live mode, only trade prints show.
    chart.data.datasets[0].data = [];

    // trade prints: size = trade size, color = exchange it printed on, semi-transparent
    const tradeData = d.trade_ticks.map(p => ({ t: p.t, price: p.price, size: p.size, exchange: p.exchange }));
    chart.data.datasets[1].data = tradeData;
    chart.data.datasets[1].pointBackgroundColor = tradeData.map(p => hexToRgba(exchangeColor(p.exchange), TRADE_POINT_ALPHA));
    chart.data.datasets[1].pointRadius = tradeData.map(p => Math.min(3 + (p.size || 1) * 0.25, 14));

    // resting order lines: draw as a flat segment spanning the visible window
    const now = new Date().toISOString();
    const windowStart = new Date(Date.now() - windowSec * 1000).toISOString();
    chart.data.datasets[2].data = d.buy_order ? [{t: windowStart, price: d.buy_order.price}, {t: now, price: d.buy_order.price}] : [];
    chart.data.datasets[3].data = d.sell_order ? [{t: windowStart, price: d.sell_order.price}, {t: now, price: d.sell_order.price}] : [];

    const fillData = d.fills.map(f => ({t: f.t, price: f.price, side: f.side, qty: f.qty}));
    chart.data.datasets[4].data = fillData;
    chart.data.datasets[4].pointBackgroundColor = fillData.map(f => f.side === 'buy' ? BUY : SELL);
    chart.data.datasets[4].pointRadius = fillData.map(f => Math.min(5 + f.qty * 0.6, 16));
    chart.update('none');

    if (d.fills.length > lastFillCount[symbol] && lastFillCount[symbol] !== 0) {
      flashPanel(symbol, d.fills[d.fills.length - 1].side);
    }
    lastFillCount[symbol] = d.fills.length;

    const cs = d.cron_state || {};
    const statsEl = document.getElementById('stats-' + symbol);
    const realCls = d.realized_pnl >= 0 ? 'pos-pos' : 'pos-neg';
    const unrealCls = d.unrealized_pnl >= 0 ? 'pos-pos' : 'pos-neg';
    statsEl.innerHTML = `
      <div class="stat">Buy order<b style="color:${BUY}">${d.buy_order ? fmtNum(d.buy_order.price) : '-'}</b></div>
      <div class="stat">Sell order<b style="color:${SELL}">${d.sell_order ? fmtNum(d.sell_order.price) : '-'}</b></div>
      <div class="stat">Cum fills<b>${cs.cum_fills !== undefined ? cs.cum_fills : '-'}</b></div>
      <div class="stat">Trades in window<b>${d.trade_ticks.length}</b></div>
      <div class="stat">Realized<b class="${realCls}">$${fmtNum(d.realized_pnl)}</b></div>
      <div class="stat">Unrealized<b class="${unrealCls}">$${fmtNum(d.unrealized_pnl)}</b></div>
    `;
    document.getElementById('liveBadge').className = 'live-badge ' + (d.live_poller_alive ? 'live-on' : 'live-off');
    document.getElementById('liveBadge').innerText = d.live_poller_alive ? 'LIVE' : 'poller down (fallback)';
    document.getElementById('serverTime').innerText = 'server time: ' + d.server_time;
  } catch (e) { console.error(symbol, e); }
}

async function refreshCycle(symbol, windowMin) {
  try {
    const res = await fetch(`/api/data?symbol=${symbol}&window=${windowMin}`);
    const d = await res.json();
    const chart = charts[symbol];
    const midData = d.price_series.map(p => ({t: p.t, price: p.mid}));
    chart.data.datasets[0].data = midData;
    chart.data.datasets[0].pointBackgroundColor = midData.map(() => MID);
    chart.data.datasets[0].pointBorderColor = midData.map(() => MID);
    chart.data.datasets[0].pointRadius = midData.map(() => 2);
    chart.data.datasets[1].data = [];
    chart.data.datasets[2].data = d.buy_steps.map(p => ({t: p.t, price: p.price}));
    chart.data.datasets[3].data = d.sell_steps.map(p => ({t: p.t, price: p.price}));
    const fillData = d.fills.map(f => ({t: f.t, price: f.price, side: f.side, qty: f.qty}));
    chart.data.datasets[4].data = fillData;
    chart.data.datasets[4].pointBackgroundColor = fillData.map(f => f.side === 'buy' ? BUY : SELL);
    chart.data.datasets[4].pointRadius = fillData.map(f => Math.min(4 + f.qty * 0.6, 14));
    chart.update('none');

    const latest = d.latest_cycle || {};
    const pnlClass = (latest.daily_pnl_pct >= 0) ? 'pos-pos' : 'pos-neg';
    const uplClass = (latest.unrealized_pl >= 0) ? 'pos-pos' : 'pos-neg';
    const haltedHtml = latest.halted ? '<div class="stat halted">HALTED<br><small>' + (latest.actions||'') + '</small></div>' : '';
    document.getElementById('stats-' + symbol).innerHTML = `
      <div class="stat">Mid<b>${fmtNum(latest.mid)}</b></div>
      <div class="stat">Position<b>${fmtNum(latest.position_shares,0)} sh</b></div>
      <div class="stat">Unrealized P/L<b class="${uplClass}">$${fmtNum(latest.unrealized_pl)}</b></div>
      <div class="stat">Daily P/L<b class="${pnlClass}">${fmtNum(latest.daily_pnl_pct)}%</b></div>
      <div class="stat">Equity<b>$${fmtNum(latest.equity,0)}</b></div>
      <div class="stat">Cum fills<b>${d.state.cum_fills !== undefined ? d.state.cum_fills : '-'}</b></div>
      ${haltedHtml}
    `;
    document.getElementById('liveBadge').className = 'live-badge live-off';
    document.getElementById('liveBadge').innerText = 'cycle history';
    document.getElementById('serverTime').innerText = 'server time: ' + d.server_time + '  |  last cycle: ' + (latest.timestamp || '-');
  } catch (e) { console.error(symbol, e); }
}

const SYMBOLS = ['SOXL', 'TQQQ', 'GME'];
SYMBOLS.forEach(s => { makeChart(s); makePnlChart(s); document.getElementById('legend-' + s).innerHTML = LEGEND_LIVE; });

const equityChart = new Chart(document.getElementById('chart-equity').getContext('2d'), {
  type: 'line',
  data: { datasets: [
    { label: 'pnl', data: [], borderColor: MID, backgroundColor: MID + '22', fill: true,
      borderWidth: 2, pointRadius: 0, tension: 0.08, parsing: {xAxisKey:'t', yAxisKey:'pnl'}, order: 2 },
    { label: 'realized', data: [], borderColor: '#ffffff', backgroundColor: 'transparent', fill: false,
      borderWidth: 1.5, pointRadius: 0, stepped: true, tension: 0, parsing: {xAxisKey:'t', yAxisKey:'realized_pnl'}, order: 1 },
  ]},
  options: {
    responsive: true,
    animation: false,
    interaction: { mode: 'nearest', intersect: false },
    scales: {
      x: { type: 'time', time: { unit: 'minute' }, ticks: { color: TXT, maxTicksLimit: 8 }, grid: { color: GRID } },
      y: { ticks: { color: TXT, callback: v => '$' + v.toLocaleString() }, grid: { color: GRID } },
    },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: c => {
        const label = c.dataset.label === 'realized' ? 'Realized' : 'Total P&L';
        const val = c.dataset.label === 'realized' ? c.raw.realized_pnl : c.raw.pnl;
        return `${label}: $${val.toLocaleString(undefined, {minimumFractionDigits:2})}`;
      } } }
    }
  }
});

function fmtPnl(delta) {
  const cls = delta >= 0 ? 'pos-pos' : 'pos-neg';
  const sign = delta >= 0 ? '+' : '';
  return `<span class="${cls}">${sign}$${delta.toFixed(2)}</span>`;
}

async function refreshEquity(windowMin) {
  try {
    const res = await fetch(`/api/equity?window=${windowMin}`);
    const d = await res.json();
    const points = d.history.concat(d.tail);
    if (!points.length) return;
    equityChart.data.datasets[0].data = points.map(p => ({t: p.t, pnl: p.pnl}));
    const realizedPoints = (d.realized_history || []).concat(d.realized_tail || []);
    equityChart.data.datasets[1].data = realizedPoints.map(p => ({t: p.t, realized_pnl: p.realized_pnl}));
    equityChart.update('none');

    const nowPnl = points[points.length - 1].pnl;
    const nowCls = nowPnl >= 0 ? 'pos-pos' : 'pos-neg';
    document.getElementById('equityNow').className = 'big ' + nowCls;
    document.getElementById('equityNow').innerText = (nowPnl >= 0 ? '+' : '') + '$' + nowPnl.toLocaleString(undefined, {minimumFractionDigits:2});
    if (realizedPoints.length) {
      const nowRealized = realizedPoints[realizedPoints.length - 1].realized_pnl;
      document.getElementById('equityRealized').innerHTML = fmtPnl(nowRealized);
    }
    if (d.day_start_pnl !== null && d.day_start_pnl !== undefined) {
      document.getElementById('equityDayPnl').innerHTML = fmtPnl(nowPnl - d.day_start_pnl);
    }
    document.getElementById('equityWindowPnl').innerHTML = fmtPnl(nowPnl - points[0].pnl);
  } catch (e) { console.error('equity', e); }
}

let timer = null;
function tick() {
  const mode = document.getElementById('modeSel').value;
  if (mode === 'live') {
    const w = document.getElementById('liveWindowSel').value;
    SYMBOLS.forEach(s => refreshLive(s, w));
    SYMBOLS.forEach(s => refreshSymbolPnl(s, Math.max(5, Math.ceil(Number(w) / 60))));
  } else {
    const w = document.getElementById('cycleWindowSel').value;
    SYMBOLS.forEach(s => refreshCycle(s, w));
    SYMBOLS.forEach(s => refreshSymbolPnl(s, w));
  }
  refreshEquity(480);
}
function restartTimer() {
  if (timer) clearInterval(timer);
  tick();
  timer = setInterval(tick, Number(document.getElementById('refreshSel').value));
}
document.getElementById('modeSel').addEventListener('change', () => {
  const isLive = document.getElementById('modeSel').value === 'live';
  document.getElementById('liveWindowWrap').style.display = isLive ? '' : 'none';
  document.getElementById('cycleWindowWrap').style.display = isLive ? 'none' : '';
  ['SOXL', 'TQQQ', 'GME'].forEach(s => document.getElementById('legend-' + s).innerHTML = isLive ? LEGEND_LIVE : LEGEND_CYCLE);
  restartTimer();
});
document.getElementById('liveWindowSel').addEventListener('change', tick);
document.getElementById('cycleWindowSel').addEventListener('change', tick);
document.getElementById('refreshSel').addEventListener('change', restartTimer);
restartTimer();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
