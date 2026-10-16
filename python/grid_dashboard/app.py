#!/usr/bin/env python3
"""
grid_dashboard/app.py
======================
Live browser dashboard for gridBot.py's crypto grid bots (BTC/ETH/SOL) -
deliberately built as the grid-trading counterpart to mm_dashboard/app.py,
same navy theme/panel layout/live-vs-cycle toggle, adapted for grid
mechanics (multiple resting levels per side instead of one join-the-touch
quote each side).

Why this is simpler than mm_dashboard: gridBot.py already computes and
persists the authoritative numbers per cycle (realized_pl,
completed_round_trips, halted) directly into its state JSON and cycle-log
CSV - there's no need to reconstruct P&L from raw fills via avg-cost
accounting the way mm_dashboard does for marketMaker.py (which only logs
raw fills, not running P&L). Position/avg-cost/unrealized P&L are read
live from Alpaca's own position endpoint instead of being replayed from a
websocket tick buffer - crypto reacts on gridBot's 1-min cron cadence, not
sub-second, so there's no persistent live_poller.py counterpart here: this
app polls Alpaca's REST crypto-quote endpoint itself (short TTL cache) and
keeps a small in-process tick ring buffer per symbol, which is enough
resolution for a strategy that only acts once a minute.

Two views, same as mm_dashboard:
  1. Live (default) - last N minutes of server-polled price ticks, current
     grid levels (all of them, colored by current buy/sell/idle state),
     recent fills, live position/P&L stats straight from Alpaca + gridBot
     state.
  2. Cycle history - gridBot's own grid_cycle_log_{symbol}.csv, snapshotted
     once per ~1min cron cycle, for longer windows.

Usage:
  venv/bin/python3 python/grid_dashboard/app.py              # 0.0.0.0:8900
  venv/bin/python3 python/grid_dashboard/app.py --port 9001
"""
import os
import sys
import json
import time
import argparse
import threading
import collections
import datetime
import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template_string

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from gridBot import (  # noqa: E402
    symbol_safe, state_path, cycle_log_path, fills_log_path,
    load_env, get_crypto_quote, get_crypto_position, PAPER_BASE, DATA_BASE,
)

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
QUOTE_CACHE_TTL_SEC = 1.5     # floor on how often we actually hit Alpaca's REST quote endpoint
TICK_BUFFER_MINUTES = 30      # how much live tick history to keep per symbol in memory
POSITION_CACHE_TTL_SEC = 3.0  # position/account calls are heavier - cache a bit longer than quotes

app = Flask(__name__)

_headers_cache = {}


def get_headers():
    if "h" in _headers_cache and "d" in _headers_cache:
        return _headers_cache["h"], _headers_cache["d"]
    env = load_env()
    key = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    sec = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "Content-Type": "application/json"}
    data_headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    _headers_cache["h"], _headers_cache["d"] = headers, data_headers
    return headers, data_headers


# ── live tick buffer (server-side poll, no separate websocket process -
#    see module docstring for why that's a reasonable simplification here) ──
_tick_lock = threading.Lock()
_tick_buffers = {s: collections.deque() for s in SYMBOLS}
_quote_cache = {}  # symbol -> (fetched_at, quote)


def poll_live_quote(symbol):
    """Fetch (and cache briefly) the current quote, appending a new tick to
    the ring buffer only when the mid actually changed - same dedup
    rationale as mm_dashboard's live_poller.py (a liquid book can re-report
    an unchanged NBBO far more often than the price actually moves)."""
    now = time.time()
    cached = _quote_cache.get(symbol)
    if cached is not None and (now - cached[0]) < QUOTE_CACHE_TTL_SEC:
        return cached[1]
    _, data_headers = get_headers()
    try:
        quote = get_crypto_quote(symbol, data_headers)
    except Exception:
        return cached[1] if cached else None
    _quote_cache[symbol] = (now, quote)
    with _tick_lock:
        buf = _tick_buffers[symbol]
        if not buf or buf[-1]["mid"] != quote["mid"]:
            buf.append({"t": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "epoch": now, "mid": quote["mid"], "bid": quote["bid"], "ask": quote["ask"]})
        cutoff = now - TICK_BUFFER_MINUTES * 60
        while buf and buf[0]["epoch"] < cutoff:
            buf.popleft()
    return quote


_position_cache = {}  # symbol -> (fetched_at, position)


def cached_position(symbol):
    now = time.time()
    cached = _position_cache.get(symbol)
    if cached is not None and (now - cached[0]) < POSITION_CACHE_TTL_SEC:
        return cached[1]
    headers, _ = get_headers()
    try:
        pos = get_crypto_position(symbol, headers)
    except Exception:
        return cached[1] if cached else {"qty": 0.0, "qty_available": 0.0, "avg_entry": 0.0,
                                          "market_value": 0.0, "unrealized_pl": 0.0}
    _position_cache[symbol] = (now, pos)
    return pos


# ── 1-min OHLC candles, straight from Alpaca's own crypto bars endpoint -
#    real exchange OHLC, not reconstructed from our own poll-tick buffer.
#    Replaced the original mid-price-line chart per James's request (the
#    poll-tick line hid a lot of real intra-minute movement a true candle
#    shows). Fetches a generous trailing window once per TTL and serves all
#    window sizes (both live and cycle-history modes) from that single
#    cached fetch, sliced client-side-of-the-cache (i.e. here) - avoids
#    hitting Alpaca's bars endpoint on every dashboard poll. ────────────────
CANDLE_CACHE_TTL_SEC = 15
CANDLE_FETCH_MINUTES = 1440  # 24h - covers every window option in both modes
_candle_cache = {}  # symbol -> (fetched_at, bars)


def fetch_1m_bars(symbol, minutes=CANDLE_FETCH_MINUTES):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(minutes=minutes)
    _, data_headers = get_headers()
    params = {
        "symbols": symbol, "timeframe": "1Min",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 10000,
    }
    resp = requests.get(f"{DATA_BASE}/v1beta3/crypto/us/bars", headers=data_headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("bars") or {}).get(symbol, [])


def cached_1m_bars(symbol):
    now = time.time()
    cached = _candle_cache.get(symbol)
    if cached is not None and (now - cached[0]) < CANDLE_CACHE_TTL_SEC:
        return cached[1]
    try:
        bars = fetch_1m_bars(symbol)
    except Exception:
        return cached[1] if cached else []
    _candle_cache[symbol] = (now, bars)
    return bars


def candles_payload(symbol, window_minutes):
    bars = cached_1m_bars(symbol)
    if window_minutes:
        cutoff_iso = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(minutes=window_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars = [b for b in bars if b.get("t", "") >= cutoff_iso]
    return [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in bars]


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def load_state(symbol):
    return load_json(state_path(symbol)) or {}


_cycle_cache = {}
_fills_cache = {}


def _load_parsed_csv_cached(path, cache):
    """mtime-gated cache, same pattern as mm_dashboard - gridBot's cron
    cycle only appends every ~1min, no need to re-parse on every poll."""
    if not os.path.exists(path):
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    cached = cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    df = pd.read_csv(path)
    if not df.empty and "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        df = df.dropna(subset=["timestamp"])
    cache[path] = (mtime, df)
    return df


def load_cycles(symbol, window_minutes):
    df = _load_parsed_csv_cached(cycle_log_path(symbol), _cycle_cache)
    if df.empty or window_minutes is None:
        return df
    cutoff = df["timestamp"].max() - pd.Timedelta(minutes=window_minutes)
    return df[df["timestamp"] >= cutoff]


def load_fills(symbol, window_minutes=None, since_epoch=None):
    df = _load_parsed_csv_cached(fills_log_path(symbol), _fills_cache)
    if df.empty:
        return df
    if since_epoch is not None:
        return df[df["timestamp"] >= pd.Timestamp(since_epoch, unit="s", tz="UTC")]
    if window_minutes:
        cutoff = df["timestamp"].max() - pd.Timedelta(minutes=window_minutes)
        return df[df["timestamp"] >= cutoff]
    return df


def symbol_live_payload(symbol, window_seconds):
    """Everything the live-view panel for one symbol needs: 1-min OHLC
    candles, all grid levels (colored by current state), recent fills, live
    position/P&L. Position/unrealized come straight from Alpaca (ground
    truth); realized_pl/round_trips/halted come from gridBot's own state
    file (already the authoritative source gridBot's fill-reconciliation
    loop maintains - no need to re-derive via a parallel accounting path).

    Candles replaced the original mid-price poll-tick line per James's
    request - real exchange OHLC from Alpaca's bars endpoint shows actual
    intra-minute range/direction that a single polled mid value hides.
    The tick buffer (poll_live_quote) is kept only for the live "current
    quote" stat readout, not for charting."""
    quote = poll_live_quote(symbol)
    state = load_state(symbol)
    pos = cached_position(symbol)

    window_minutes = max(1, window_seconds // 60)
    candles = candles_payload(symbol, window_minutes)

    now_epoch = time.time()
    cutoff = now_epoch - window_seconds

    levels = state.get("levels", [])
    level_orders = state.get("level_orders", {})
    level_rows = []
    for lvl in levels:
        key = next((k for k in level_orders if abs(float(k) - lvl) < max(1e-6, lvl * 1e-6)), None)
        side = level_orders[key]["side"] if key else None
        level_rows.append({"price": lvl, "side": side})

    fills_df = load_fills(symbol, since_epoch=cutoff)
    fills = []
    if not fills_df.empty:
        for _, row in fills_df.iterrows():
            fills.append({"t": row["timestamp"].isoformat(), "price": float(row["filled_avg_price"]),
                          "side": row["side"], "qty": float(row["qty"])})

    unrealized = pos.get("unrealized_pl", 0.0)
    realized = state.get("realized_pl", 0.0)

    return {
        "symbol": symbol,
        "quote": quote,
        "candles": candles,
        "levels": level_rows,
        "lower_bound": state.get("lower_bound"),
        "upper_bound": state.get("upper_bound"),
        "spacing_pct": state.get("spacing_pct"),
        "fills": fills,
        "position_qty": pos.get("qty", 0.0),
        "avg_entry": pos.get("avg_entry", 0.0),
        "market_value": pos.get("market_value", 0.0),
        "unrealized_pl": unrealized,
        "realized_pl": realized,
        "total_pl": round(realized + unrealized, 2),
        "completed_round_trips": state.get("completed_round_trips", 0),
        "halted": state.get("halted", False),
        "halt_reason": state.get("halt_reason"),
        "open_buy_levels": sum(1 for v in level_orders.values() if v["side"] == "buy"),
        "open_sell_levels": sum(1 for v in level_orders.values() if v["side"] == "sell"),
        "pending_reorders": len(state.get("pending_reorders", [])),
    }


@app.route("/api/live")
def api_live():
    symbol = request.args.get("symbol", "BTC/USD").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"unknown symbol {symbol}"}), 400
    window_seconds = int(request.args.get("window_seconds", 300))
    payload = symbol_live_payload(symbol, window_seconds)
    payload["server_time"] = pd.Timestamp.utcnow().isoformat()
    return jsonify(payload)


@app.route("/api/data")
def api_data():
    """Cycle-history view, sourced from gridBot's own cron-cycle CSV for
    stats/fills, but real 1-min OHLC candles (same source as /api/live) for
    the price chart - mirrors mm_dashboard's /api/data for the
    longer-window mode, with candles replacing the old cycle-log mid-price
    line per James's request."""
    symbol = request.args.get("symbol", "BTC/USD").upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"unknown symbol {symbol}"}), 400
    window_minutes = int(request.args.get("window", 240))

    cycles = load_cycles(symbol, window_minutes)
    fills_df = load_fills(symbol, window_minutes)
    state = load_state(symbol)
    candles = candles_payload(symbol, window_minutes)

    fills = []
    if not fills_df.empty:
        for _, row in fills_df.iterrows():
            fills.append({"t": row["timestamp"].isoformat(), "price": float(row["filled_avg_price"]),
                          "side": row["side"], "qty": float(row["qty"])})

    levels = state.get("levels", [])
    level_orders = state.get("level_orders", {})
    level_rows = []
    for lvl in levels:
        key = next((k for k in level_orders if abs(float(k) - lvl) < max(1e-6, lvl * 1e-6)), None)
        level_rows.append({"price": lvl, "side": level_orders[key]["side"] if key else None})

    latest = cycles.iloc[-1].to_dict() if not cycles.empty else {}

    def clean(v):
        if isinstance(v, float) and pd.isna(v):
            return None
        return v
    latest_clean = {k: clean(v) for k, v in latest.items()}
    if latest_clean.get("timestamp") is not None:
        latest_clean["timestamp"] = pd.Timestamp(latest["timestamp"]).isoformat()

    return jsonify({
        "symbol": symbol, "candles": candles, "fills": fills,
        "levels": level_rows, "lower_bound": state.get("lower_bound"),
        "upper_bound": state.get("upper_bound"), "latest_cycle": latest_clean,
        "state": state, "server_time": pd.Timestamp.utcnow().isoformat(),
    })


@app.route("/api/equity")
def api_equity():
    """Combined grid-bot P&L across BTC/ETH/SOL. History line is REALIZED
    P&L only (gridBot's state already tracks this authoritatively per
    cycle - see module docstring for why this avoids reconstructing an
    avg-cost P&L curve the way mm_dashboard does). Unrealized/total are
    live "right now" numbers from Alpaca positions, not charted over time -
    unrealized is just the current inventory mark and swings with price
    noise, it isn't the grid strategy's performance signal the way
    completed round trips / realized P&L is."""
    window_minutes_raw = request.args.get("window", "480")
    window_minutes = None if window_minutes_raw == "all" else int(window_minutes_raw)

    per_symbol_realized = {}
    for s in SYMBOLS:
        df = load_cycles(s, window_minutes)
        if not df.empty:
            per_symbol_realized[s] = df[["timestamp", "realized_pl"]].dropna()

    all_times = sorted(pd.concat([per_symbol_realized[s]["timestamp"] for s in per_symbol_realized]).unique()) \
        if per_symbol_realized else []

    history = []
    if all_times:
        axis = pd.DataFrame({"timestamp": all_times})
        total = pd.Series(0.0, index=axis.index)
        for s in SYMBOLS:
            df = per_symbol_realized.get(s)
            if df is not None and not df.empty:
                m = pd.merge_asof(axis, df, on="timestamp", direction="backward")
                total += m["realized_pl"].fillna(0.0)
        axis["realized_pl"] = total
        ts_strs = axis["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        history = [{"t": t, "realized_pl": p} for t, p in zip(ts_strs, axis["realized_pl"])]

    realized_now, unrealized_now, round_trips_now = 0.0, 0.0, 0
    any_halted = []
    for s in SYMBOLS:
        state = load_state(s)
        pos = cached_position(s)
        realized_now += state.get("realized_pl", 0.0)
        unrealized_now += pos.get("unrealized_pl", 0.0)
        round_trips_now += state.get("completed_round_trips", 0)
        if state.get("halted"):
            any_halted.append({"symbol": s, "reason": state.get("halt_reason")})

    day = pd.Timestamp.utcnow().date()
    day_start_realized = None
    if all_times:
        axis_day = axis[axis["timestamp"].dt.date == day]
        if not axis_day.empty:
            day_start_realized = float(axis_day.iloc[0]["realized_pl"])

    return jsonify({
        "history": history,
        "realized_now": round(realized_now, 2),
        "unrealized_now": round(unrealized_now, 2),
        "total_now": round(realized_now + unrealized_now, 2),
        "round_trips_now": round_trips_now,
        "day_start_realized": day_start_realized,
        "halted": any_halted,
        "server_time": pd.Timestamp.utcnow().isoformat(),
    })


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Grid Bot Live Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1/dist/chartjs-chart-financial.min.js"></script>
<style>
  :root { --bg:#02233F; --grid:#274066; --txt:#ffffff; --buy:#2E9E4E; --sell:#D9453D; --mid:#4C9BE0; --idle:#5A7290; }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--txt); font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:16px; }
  h1 { font-size: 20px; margin: 0 0 4px 0; }
  .sub { color: var(--txt); opacity: 0.75; font-size: 12px; margin-bottom: 14px; }
  .live-badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:bold; margin-left:8px; background:#1f4d2e; color:#6FE08A; }
  .controls { display:flex; gap:16px; align-items:center; margin-bottom:14px; flex-wrap:wrap; }
  .controls label { font-size:13px; }
  select { background:#0a2d4e; color:var(--txt); border:1px solid var(--grid); border-radius:4px; padding:4px 8px; }
  .grid { display:flex; gap:20px; flex-wrap:wrap; }
  .panel { flex:1 1 560px; min-width:360px; background:#031b33; border:1px solid var(--grid); border-radius:8px; padding:14px; }
  .panel h2 { margin:0 0 8px 0; font-size:16px; }
  .stats { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:10px; font-size:13px; }
  .stat { background:#0a2d4e; border-radius:6px; padding:6px 10px; }
  .stat b { display:block; font-size:15px; }
  .pos-pos { color:#6FE08A; }
  .pos-neg { color:#FF8A7A; }
  .halted { color:#FFCF5C; font-weight:bold; }
  .legend { font-size:12px; opacity:0.85; margin-top:6px; }
  .legend span { margin-right:14px; }
  canvas { max-height: 420px; }
  .equity-panel { background:#031b33; border:1px solid var(--grid); border-radius:8px; padding:14px; margin-bottom:20px; }
  .equity-hero { display:flex; gap:28px; align-items:baseline; flex-wrap:wrap; margin-bottom:10px; }
  .equity-hero .big { font-size:34px; font-weight:bold; }
  .equity-hero .pnl-line { font-size:18px; }
  canvas#chart-equity { max-height: 200px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  .flash-buy { animation: flashBuy 0.6s ease-out; }
  .flash-sell { animation: flashSell 0.6s ease-out; }
  @keyframes flashBuy { 0% { background: #2E9E4E55; } 100% { background: transparent; } }
  @keyframes flashSell { 0% { background: #D9453D55; } 100% { background: transparent; } }
</style>
</head>
<body>
  <h1>Grid Bot Live Dashboard <span id="liveBadge" class="live-badge">crypto 24/7</span></h1>
  <div class="sub" id="serverTime">connecting...</div>
  <div class="equity-panel">
    <h2 style="margin:0 0 8px 0; font-size:16px;">Grid Bot P&amp;L (BTC + ETH + SOL combined)</h2>
    <div class="equity-hero">
      <div><div style="font-size:12px;opacity:0.75;">Realized P&amp;L</div><div class="big" id="equityRealized">-</div></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Unrealized (now)</div><span id="equityUnrealized">-</span></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Total (now)</div><span id="equityTotal">-</span></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Today</div><span id="equityDayPnl">-</span></div>
      <div class="pnl-line"><div style="font-size:12px;opacity:0.75;">Round trips</div><span id="equityRoundTrips">-</span></div>
    </div>
    <canvas id="chart-equity"></canvas>
    <div class="legend"><span><span class="dot" style="background:#4C9BE0"></span>combined realized P&amp;L</span></div>
    <div id="haltedBanner"></div>
  </div>
  <div class="controls">
    <label>View:
      <select id="modeSel">
        <option value="live" selected>Live tick</option>
        <option value="cycle">Cycle history (1-min snapshots)</option>
      </select>
    </label>
    <label id="liveWindowWrap">Window:
      <select id="liveWindowSel">
        <option value="300" selected>Last 5 min</option>
        <option value="900">Last 15 min</option>
        <option value="1800">Last 30 min</option>
      </select>
    </label>
    <label id="cycleWindowWrap" style="display:none">Window:
      <select id="cycleWindowSel">
        <option value="60">Last 1 hour</option>
        <option value="240" selected>Last 4 hours</option>
        <option value="1440">Last 24 hours</option>
      </select>
    </label>
    <label>Refresh:
      <select id="refreshSel">
        <option value="1000">1s</option>
        <option value="2000" selected>2s</option>
        <option value="5000">5s</option>
      </select>
    </label>
  </div>
  <div class="grid">
    <div class="panel">
      <h2>BTC/USD</h2>
      <div class="stats" id="stats-BTCUSD"></div>
      <canvas id="chart-BTCUSD"></canvas>
      <div class="legend" id="legend-BTCUSD"></div>
    </div>
    <div class="panel">
      <h2>ETH/USD</h2>
      <div class="stats" id="stats-ETHUSD"></div>
      <canvas id="chart-ETHUSD"></canvas>
      <div class="legend" id="legend-ETHUSD"></div>
    </div>
    <div class="panel">
      <h2>SOL/USD</h2>
      <div class="stats" id="stats-SOLUSD"></div>
      <canvas id="chart-SOLUSD"></canvas>
      <div class="legend" id="legend-SOLUSD"></div>
    </div>
  </div>

<script>
const GRID = '#274066', TXT = '#ffffff', BUY = '#2E9E4E', SELL = '#D9453D', MID = '#4C9BE0', IDLE = '#5A7290';
const SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD'];
const SAFE = s => s.replace('/', '');
const charts = {};
const lastFillCount = {};
SYMBOLS.forEach(s => lastFillCount[s] = 0);

const LEGEND_LIVE = `
  <span><span class="dot" style="background:${BUY}"></span>up candle</span>
  <span><span class="dot" style="background:${SELL}"></span>down candle</span>
  <span><span class="dot" style="background:${BUY}"></span>resting buy level</span>
  <span><span class="dot" style="background:${SELL}"></span>resting sell level</span>
  <span><span class="dot" style="background:${IDLE}"></span>idle grid level</span>
  <span>&#9632; fill (size = qty)</span>`;
const LEGEND_CYCLE = `
  <span><span class="dot" style="background:${BUY}"></span>up candle</span>
  <span><span class="dot" style="background:${SELL}"></span>down candle</span>
  <span><span class="dot" style="background:${BUY}"></span>resting buy level</span>
  <span><span class="dot" style="background:${SELL}"></span>resting sell level</span>
  <span>&#9632; fill (size = qty)</span>`;

function fmtNum(v, dp=2) { return (v === null || v === undefined || isNaN(v)) ? '-' : Number(v).toFixed(dp); }
function fmtPnl(delta) {
  const cls = delta >= 0 ? 'pos-pos' : 'pos-neg';
  const sign = delta >= 0 ? '+' : '';
  return `<span class="${cls}">${sign}$${delta.toFixed(2)}</span>`;
}

function makeChart(symbol) {
  const ctx = document.getElementById('chart-' + SAFE(symbol)).getContext('2d');
  charts[symbol] = new Chart(ctx, {
    type: 'candlestick',
    data: { datasets: [
      { label: 'candles', data: [],
        color: { up: BUY, down: SELL, unchanged: IDLE },
        borderColor: { up: BUY, down: SELL, unchanged: IDLE },
        order: 4 },
      { type: 'scatter', label: 'fills', data: [], parsing: {xAxisKey:'t', yAxisKey:'price'},
        pointBackgroundColor: [], pointBorderColor: '#ffffff', pointBorderWidth: 1,
        pointStyle: 'rect', pointRadius: [], order: 1, showLine: false },
      // grid-level datasets are appended/removed dynamically after these two
      // fixed datasets by drawLevels() - the level count can change (grid
      // reset, levels consumed by fills) so it's not a fixed-size array.
    ]},
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { type: 'time', time: { unit: 'minute', displayFormats: { minute: 'HH:mm' } },
             ticks: { color: TXT, maxTicksLimit: 8 }, grid: { color: GRID } },
        y: { ticks: { color: TXT }, grid: { color: GRID } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function(c) {
          const d = c.raw;
          if (c.dataset.label === 'fills') return `${d.side.toUpperCase()} filled ${d.qty}@${d.price}`;
          if (c.dataset.label === 'candles') return `O ${fmtNum(d.o)} H ${fmtNum(d.h)} L ${fmtNum(d.l)} C ${fmtNum(d.c)}`;
          if (c.dataset.label && c.dataset.label.startsWith('level-')) return `level ${d.price} (${d.side || 'idle'})`;
          return `${c.dataset.label}: ${d.price}`;
        } } }
      }
    }
  });
}

function drawLevels(chart, levels, windowStartIso, nowIso) {
  // One real Chart.js line dataset PER grid level, spanning the full visible
  // window as a flat horizontal dashed line - this is what actually renders
  // visibly (the earlier version tried to fake this with pointRadius:0,
  // showLine:false point markers, which drew nothing at all - confirmed as
  // the root cause of "I can't see the orders on the dash" bug report).
  // Fixed datasets are [candles, fills] (indices 0-1); level datasets start
  // at index 2 and are rebuilt to match the current level count every
  // refresh (levels get consumed/re-armed as fills happen, not fixed-count).
  const FIXED = 2;
  while (chart.data.datasets.length > FIXED) chart.data.datasets.pop();
  levels.forEach(l => {
    const color = l.side === 'buy' ? BUY : (l.side === 'sell' ? SELL : IDLE);
    chart.data.datasets.push({
      type: 'line', label: 'level-' + l.price, showLine: true,
      data: [{t: windowStartIso, price: l.price, side: l.side}, {t: nowIso, price: l.price, side: l.side}],
      parsing: {xAxisKey:'t', yAxisKey:'price'},
      borderColor: color, borderDash: l.side ? [] : [4, 4], borderWidth: l.side ? 2 : 1,
      pointRadius: 0, order: 3,
    });
  });
}

function flashPanel(symbol, side) {
  const panel = document.getElementById('chart-' + SAFE(symbol)).closest('.panel');
  panel.classList.remove('flash-buy', 'flash-sell');
  void panel.offsetWidth;
  panel.classList.add(side === 'buy' ? 'flash-buy' : 'flash-sell');
}

async function refreshLive(symbol, windowSec) {
  try {
    const res = await fetch(`/api/live?symbol=${symbol}&window_seconds=${windowSec}`);
    const d = await res.json();
    const chart = charts[symbol];

    const candleData = d.candles.map(c => ({ x: new Date(c.t).getTime(), o: c.o, h: c.h, l: c.l, c: c.c }));
    chart.data.datasets[0].data = candleData;

    const now = new Date().toISOString();
    const windowStart = new Date(Date.now() - windowSec * 1000).toISOString();
    drawLevels(chart, d.levels, windowStart, now);

    const fillData = d.fills.map(f => ({t: f.t, price: f.price, side: f.side, qty: f.qty}));
    chart.data.datasets[1].data = fillData;
    chart.data.datasets[1].pointBackgroundColor = fillData.map(f => f.side === 'buy' ? BUY : SELL);
    chart.data.datasets[1].pointRadius = fillData.map(f => Math.min(5 + f.qty * 1000, 16));
    chart.update('none');

    if (d.fills.length > lastFillCount[symbol] && lastFillCount[symbol] !== 0) {
      flashPanel(symbol, d.fills[d.fills.length - 1].side);
    }
    lastFillCount[symbol] = d.fills.length;

    const realCls = d.realized_pl >= 0 ? 'pos-pos' : 'pos-neg';
    const unrealCls = d.unrealized_pl >= 0 ? 'pos-pos' : 'pos-neg';
    const haltedHtml = d.halted ? `<div class="stat halted">HALTED<br><small>${d.halt_reason||''}</small></div>` : '';
    document.getElementById('stats-' + SAFE(symbol)).innerHTML = `
      <div class="stat">Mid<b>${fmtNum(d.quote ? d.quote.mid : null, symbol==='SOL/USD'?3:2)}</b></div>
      <div class="stat">Position<b>${fmtNum(d.position_qty, 6)}</b></div>
      <div class="stat">Avg cost<b>${fmtNum(d.avg_entry)}</b></div>
      <div class="stat">Open buy/sell<b>${d.open_buy_levels} / ${d.open_sell_levels}</b></div>
      <div class="stat">Realized<b class="${realCls}">$${fmtNum(d.realized_pl)}</b></div>
      <div class="stat">Unrealized<b class="${unrealCls}">$${fmtNum(d.unrealized_pl)}</b></div>
      <div class="stat">Round trips<b>${d.completed_round_trips}</b></div>
      ${d.pending_reorders ? `<div class="stat halted">Pending retry: ${d.pending_reorders}</div>` : ''}
      ${haltedHtml}
    `;
    document.getElementById('serverTime').innerText = 'server time: ' + d.server_time;
  } catch (e) { console.error(symbol, e); }
}

async function refreshCycle(symbol, windowMin) {
  try {
    const res = await fetch(`/api/data?symbol=${symbol}&window=${windowMin}`);
    const d = await res.json();
    const chart = charts[symbol];
    const candleData = d.candles.map(c => ({ x: new Date(c.t).getTime(), o: c.o, h: c.h, l: c.l, c: c.c }));
    chart.data.datasets[0].data = candleData;

    const first = d.candles[0] ? d.candles[0].t : new Date().toISOString();
    const last = d.candles.length ? d.candles[d.candles.length-1].t : new Date().toISOString();
    drawLevels(chart, d.levels, first, last);

    const fillData = d.fills.map(f => ({t: f.t, price: f.price, side: f.side, qty: f.qty}));
    chart.data.datasets[1].data = fillData;
    chart.data.datasets[1].pointBackgroundColor = fillData.map(f => f.side === 'buy' ? BUY : SELL);
    chart.data.datasets[1].pointRadius = fillData.map(f => Math.min(5 + f.qty * 1000, 16));
    chart.update('none');

    const latest = d.latest_cycle || {};
    const haltedHtml = latest.halted ? `<div class="stat halted">HALTED<br><small>${latest.errors||''}</small></div>` : '';
    document.getElementById('stats-' + SAFE(symbol)).innerHTML = `
      <div class="stat">Mid<b>${fmtNum(latest.mid)}</b></div>
      <div class="stat">Position<b>${fmtNum(latest.position_qty, 6)}</b></div>
      <div class="stat">Open buy/sell<b>${latest.open_buy_levels ?? '-'} / ${latest.open_sell_levels ?? '-'}</b></div>
      <div class="stat">Realized<b>$${fmtNum(latest.realized_pl)}</b></div>
      <div class="stat">Round trips<b>${latest.completed_round_trips ?? '-'}</b></div>
      ${haltedHtml}
    `;
    document.getElementById('serverTime').innerText = 'server time: ' + d.server_time + '  |  last cycle: ' + (latest.timestamp || '-');
  } catch (e) { console.error(symbol, e); }
}

SYMBOLS.forEach(s => { makeChart(s); document.getElementById('legend-' + SAFE(s)).innerHTML = LEGEND_LIVE; });

const equityChart = new Chart(document.getElementById('chart-equity').getContext('2d'), {
  type: 'line',
  data: { datasets: [
    { label: 'realized_pl', data: [], borderColor: MID, backgroundColor: MID + '22', fill: true,
      borderWidth: 2, pointRadius: 0, stepped: true, tension: 0, parsing: {xAxisKey:'t', yAxisKey:'realized_pl'} },
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
      tooltip: { callbacks: { label: c => `Realized: $${Number(c.raw.realized_pl).toLocaleString(undefined,{minimumFractionDigits:2})}` } }
    }
  }
});

async function refreshEquity() {
  try {
    const res = await fetch(`/api/equity?window=480`);
    const d = await res.json();
    equityChart.data.datasets[0].data = d.history.map(p => ({t: p.t, realized_pl: p.realized_pl}));
    equityChart.update('none');

    document.getElementById('equityRealized').className = 'big ' + (d.realized_now >= 0 ? 'pos-pos' : 'pos-neg');
    document.getElementById('equityRealized').innerText = (d.realized_now >= 0 ? '+' : '') + '$' + d.realized_now.toLocaleString(undefined, {minimumFractionDigits:2});
    document.getElementById('equityUnrealized').innerHTML = fmtPnl(d.unrealized_now);
    document.getElementById('equityTotal').innerHTML = fmtPnl(d.total_now);
    document.getElementById('equityRoundTrips').innerText = d.round_trips_now;
    if (d.day_start_realized !== null && d.day_start_realized !== undefined) {
      document.getElementById('equityDayPnl').innerHTML = fmtPnl(d.realized_now - d.day_start_realized);
    }
    const banner = document.getElementById('haltedBanner');
    banner.innerHTML = (d.halted && d.halted.length)
      ? d.halted.map(h => `<div class="stat halted" style="margin-top:8px;">${h.symbol} HALTED: ${h.reason}</div>`).join('')
      : '';
  } catch (e) { console.error('equity', e); }
}

let timer = null;
function tick() {
  const mode = document.getElementById('modeSel').value;
  if (mode === 'live') {
    const w = document.getElementById('liveWindowSel').value;
    SYMBOLS.forEach(s => refreshLive(s, w));
  } else {
    const w = document.getElementById('cycleWindowSel').value;
    SYMBOLS.forEach(s => refreshCycle(s, w));
  }
  refreshEquity();
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
  SYMBOLS.forEach(s => document.getElementById('legend-' + SAFE(s)).innerHTML = isLive ? LEGEND_LIVE : LEGEND_CYCLE);
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
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
