#!/usr/bin/env python3
"""
gridBot.py
==========
Grid trading bot for crypto (paper trading via Alpaca). Built as the
crypto-session counterpart to marketMaker.py once MM was judged unfeasible
in crypto (no queue priority / no maker rebates on retail paper trading
means spread-capture MM can't clear real taker fees). Grid trading doesn't
have that problem: it harvests realized volatility by resting limit orders
at fixed price levels and profiting whenever price oscillates THROUGH them,
with no race against other resting orders required - a limit order just
fills when price crosses it, same mechanics in paper as live.

Strategy:
  1. On first run (or --reset) for a symbol, compute a price range around
     current mid using recent daily ATR% (see --atr-mult), split it into
     --levels equal-price-step levels, and place a GTC buy limit at every
     level below current price (sized in fixed COIN qty, not USD notional,
     so every completed buy->sell round trip earns the same $ amount).
  2. Every cycle: reconcile fills since last watermark via the FILL
     activities feed (not order status - same reasoning as marketMaker.py,
     a filled level order is terminal so this is simpler than MM's
     cancel/replace churn, but we still use the per-fill feed defensively).
       - On a BUY fill at level i: place a SELL limit at level i+1 for the
         same qty just bought (that's the coin inventory to sell with).
       - On a SELL fill at level i: place a BUY limit back at level i-1
         (re-arm the level that funded this sell) for the fixed qty.
  3. Breakout kill switch (the actual risk in grid trading - a hard trend
     instead of chop means you buy all the way down with nothing to sell
     into, or miss all the upside because you sold out early climbing up):
     if mid trades beyond [lower_bound, upper_bound] by more than
     --breakout-atr-mult * ATR, CANCEL every open grid order, market-flatten
     any resting coin position, and halt. Needs --reset to restart from a
     freshly recomputed range - this is intentionally not self-healing.
  4. No EOD flatten - crypto is 24/7, the loop just keeps running (or is
     cron-fired every N minutes) until you --flatten manually or breakout
     halts it.

Usage:
  venv/bin/python3 python/gridBot.py --symbol BTC/USD --budget 2000           # dry run, shows planned grid
  venv/bin/python3 python/gridBot.py --symbol BTC/USD --budget 2000 --execute --reset   # first execute: builds grid + places orders
  venv/bin/python3 python/gridBot.py --symbol BTC/USD --execute              # subsequent cycles: reconcile fills, replace triggered levels
  venv/bin/python3 python/gridBot.py --symbol BTC/USD --execute --flatten    # cancel everything + market-close position

Output JSON printed to stdout (cron-friendly, one line per cycle).
"""

import os
import sys
import json
import math
import datetime
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marketMaker import alpaca_get, alpaca_post, alpaca_delete, load_env, append_csv  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────
OUT_DIR    = os.path.expanduser("~/discordBot/outputs/markets")
PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE  = "https://data.alpaca.markets"

MIN_NOTIONAL_USD = 10.05  # Alpaca crypto orders reject below $10 cost basis
                           # ("cost basis must be >= minimal amount of order 10",
                           # confirmed live Oct 2026 - a stuck 0.0923 SOL re-arm
                           # at $103.81 (~$9.58) 403'd every retry cycle forever
                           # since it can never clear $10 at that qty/price).
                           # Small buffer above $10 avoids float-rounding rejections.

DEFAULT_LEVELS            = 14    # grid steps each side of center (levels+1 price points)
DEFAULT_ATR_MULT          = 0.6   # half-range = ATR_MULT * daily ATR%. Tightened from 1.5
                                   # on Oct 2026 after launch night: at 1.5x ATR the nearest
                                   # level sat 1.5-2.3% from mid - too wide to realistically
                                   # fill in a single evening of normal chop (confirmed: zero
                                   # fills in ~1hr on BTC/ETH/SOL). 0.6x puts the nearest level
                                   # within ~0-0.25% of mid (verified against live quotes),
                                   # while min_spacing_pct still keeps real levels spaced wider
                                   # than the ~0.40% round-trip crypto fee.
DEFAULT_MIN_SPACING_PCT   = 0.35  # floor on level spacing - must clear round-trip fees.
                                   # Tightened from 0.45% (Oct 2026, "tighten the spreads"):
                                   # worst-case round trip is maker+maker = 0.15%+0.15% = 0.30%
                                   # (every grid order rests as a non-marketable limit, so maker
                                   # fee applies on both legs, not taker's 0.25%) - 0.35% leaves
                                   # ~5bps margin per completed round trip. This is THINNER than
                                   # the old 15bps margin - more fill opportunities per night but
                                   # less edge per fill. Do not go below ~0.30% without re-checking
                                   # the live maker fee tier (it's volume-based and could rise).
DEFAULT_BREAKOUT_ATR_MULT = 1.5   # halt if mid moves this many extra ATRs past the range.
                                   # Raised from 1.0 alongside the ATR_MULT tightening above -
                                   # a tighter grid range means the same breakout_atr_mult
                                   # multiplier now triggers on smaller absolute moves, so this
                                   # compensates to avoid halting on routine noise.
MIN_LEVELS                = 4

FILL_FIELDS  = ["timestamp", "symbol", "order_id", "activity_id", "side", "qty",
                "filled_avg_price", "filled_at", "level_price"]
CYCLE_FIELDS = ["timestamp", "symbol", "mid", "lower_bound", "upper_bound",
                "position_qty", "open_buy_levels", "open_sell_levels",
                "realized_pl", "completed_round_trips", "halted", "actions", "errors"]


def symbol_safe(symbol):
    return symbol.replace("/", "")


def state_path(symbol):
    return os.path.join(OUT_DIR, f"grid_state_{symbol_safe(symbol)}.json")


def cycle_log_path(symbol):
    return os.path.join(OUT_DIR, f"grid_cycle_log_{symbol_safe(symbol)}.csv")


def fills_log_path(symbol):
    return os.path.join(OUT_DIR, f"grid_fills_log_{symbol_safe(symbol)}.csv")


def load_state(symbol):
    p = state_path(symbol)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {
        "initialized": False,
        "symbol": symbol,
        "center_price": None,
        "lower_bound": None,
        "upper_bound": None,
        "atr_abs": None,
        "spacing_pct": None,
        "qty_per_level": None,
        "levels": [],          # sorted ascending price list
        "level_orders": {},    # str(price) -> {"side": "buy"|"sell", "order_id": str, "qty": float}
        "pending_reorders": [],  # re-arms that failed to place and need retry next cycle
        "last_fill_activity_id": None,
        "realized_pl": 0.0,
        "completed_round_trips": 0,
        "halted": False,
        "halt_reason": None,
        "created_at": None,
    }


def save_state(symbol, state):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(state_path(symbol), "w") as f:
        json.dump(state, f, indent=2)


def get_crypto_quote(symbol, headers):
    """Mid price + bid/ask from Alpaca's crypto latest-quotes endpoint."""
    data = alpaca_get(f"/v1beta3/crypto/us/latest/quotes?symbols={symbol}", headers, base=DATA_BASE)
    q = (data.get("quotes") or {}).get(symbol)
    if not q:
        raise RuntimeError(f"no crypto quote returned for {symbol}: {data}")
    bid, ask = float(q.get("bp", 0) or 0), float(q.get("ap", 0) or 0)
    if bid <= 0 or ask <= 0 or bid >= ask:
        raise RuntimeError(f"bad/crossed crypto quote for {symbol}: bid={bid} ask={ask}")
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}


def get_daily_atr_pct(symbol, headers, lookback_days=30, atr_window=14):
    """Daily ATR as a percent of last close, computed from Alpaca crypto daily bars.
    Returns (atr_pct, atr_abs_at_last_close, last_close)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=lookback_days)
    data = alpaca_get(
        f"/v1beta3/crypto/us/bars?symbols={symbol}&timeframe=1Day"
        f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}&end={now.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=500",
        headers, base=DATA_BASE,
    )
    bars = (data.get("bars") or {}).get(symbol, [])
    if len(bars) < atr_window + 1:
        raise RuntimeError(f"not enough daily bars for {symbol} ATR calc ({len(bars)} bars)")
    trs = []
    prev_close = bars[0]["c"]
    for b in bars[1:]:
        tr = max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
        trs.append(tr)
        prev_close = b["c"]
    atr_abs = sum(trs[-atr_window:]) / atr_window
    last_close = bars[-1]["c"]
    return atr_abs / last_close * 100.0, atr_abs, last_close


def get_crypto_position(symbol, headers):
    """Crypto positions report back WITHOUT the slash (e.g. BTC/USD order ->
    BTCUSD position symbol) - confirmed against the live API, not documented
    clearly anywhere. Must look up with the no-slash form or you get a
    false-negative 404 and the bot thinks it's flat when it isn't."""
    lookup_symbol = symbol_safe(symbol)
    try:
        data = alpaca_get(f"/v2/positions/{lookup_symbol}", headers)
        return {"qty": float(data.get("qty", 0)), "qty_available": float(data.get("qty_available", data.get("qty", 0))),
                "avg_entry": float(data.get("avg_entry_price", 0)),
                "market_value": float(data.get("market_value", 0)), "unrealized_pl": float(data.get("unrealized_pl", 0))}
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"qty": 0.0, "qty_available": 0.0, "avg_entry": 0.0, "market_value": 0.0, "unrealized_pl": 0.0}
        raise


def get_open_grid_orders(symbol, headers):
    orders = alpaca_get(f"/v2/orders?status=open&symbols={symbol}&limit=100", headers)
    return orders if isinstance(orders, list) else []


def get_new_fill_activities(symbol, headers, since_id=None, max_pages=20):
    """Same per-fill reconciliation pattern as marketMaker.py's proven
    get_new_fill_activities: walk DESC pages, stop once we reach since_id
    (activity ids sort lexicographically by time - a zero-padded timestamp
    prefix), return ascending. since_id=None means first run - caller is
    responsible for not blindly backfilling full account history (see
    get_latest_fill_activity_id, used to seed init's watermark instead of
    leaving it None). Client-side symbol filter because the activities
    endpoint's server-side filter has been unreliable on this account
    (per marketMaker.py's own notes - other symbols leak through)."""
    collected = []
    page_token = None
    for _ in range(max_pages):
        params = {"direction": "desc", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(f"{PAPER_BASE}/v2/account/activities/FILL",
                             headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        stop = False
        for a in batch:
            if since_id and a["id"] <= since_id:
                stop = True
                break
            collected.append(a)
        if stop or len(batch) < 100:
            break
        page_token = batch[-1]["id"]
    collected.sort(key=lambda a: a["id"])
    return [a for a in collected if a.get("symbol") == symbol]


def get_latest_fill_activity_id(symbol, headers):
    """Most recent existing FILL activity id for this symbol account-wide,
    used ONLY to seed a fresh grid's watermark so init doesn't replay years
    of unrelated historical fills (other symbols, prior manual test trades)
    as if they were grid fills. Returns None if there is no fill history at
    all for this symbol yet (fine - since_id=None just means "from the start",
    which is correct when there IS no prior history)."""
    resp = requests.get(f"{PAPER_BASE}/v2/account/activities/FILL",
                         headers=headers, params={"direction": "desc", "page_size": 100}, timeout=15)
    resp.raise_for_status()
    batch = resp.json()
    for a in batch:
        if a.get("symbol") == symbol:
            return a.get("id")
    return None


def cancel_all_open_orders(symbol, headers):
    orders = get_open_grid_orders(symbol, headers)
    for o in orders:
        alpaca_delete(f"/v2/orders/{o['id']}", headers)
    return len(orders)


def market_flatten_position(symbol, headers):
    """Market-close any resting position. Uses DELETE /v2/positions/{symbol}
    (no-slash form per get_crypto_position's note)."""
    lookup_symbol = symbol_safe(symbol)
    resp = requests.delete(f"{PAPER_BASE}/v2/positions/{lookup_symbol}",
                            headers=headers, timeout=15)
    if resp.status_code == 404:
        return None  # already flat
    resp.raise_for_status()
    return resp.json()


def build_grid(symbol, mid, atr_pct, atr_abs, budget, levels, atr_mult, min_spacing_pct):
    half_range_pct = atr_mult * atr_pct
    # enforce the fee-floor on spacing by shrinking level count if needed,
    # never by shrinking the range (the range comes from realized vol and
    # shouldn't be squeezed just to fit more levels in)
    total_span_pct = 2 * half_range_pct
    spacing_pct = total_span_pct / levels
    if spacing_pct < min_spacing_pct:
        levels = max(MIN_LEVELS, int(total_span_pct / min_spacing_pct))
        spacing_pct = total_span_pct / levels

    lower = mid * (1 - half_range_pct / 100.0)
    upper = mid * (1 + half_range_pct / 100.0)
    step = (upper - lower) / levels
    grid_levels = [round(lower + i * step, 8) for i in range(levels + 1)]

    qty_per_level = (budget / (levels + 1)) / mid

    return {
        "levels": grid_levels,
        "lower_bound": lower,
        "upper_bound": upper,
        "spacing_pct": spacing_pct,
        "qty_per_level": qty_per_level,
        "atr_abs": atr_abs,
        "half_range_pct": half_range_pct,
    }


def place_limit(symbol, side, qty, price, headers):
    payload = {
        "symbol": symbol, "qty": f"{qty:.9f}".rstrip("0").rstrip("."),
        "side": side, "type": "limit", "time_in_force": "gtc",
        "limit_price": f"{price:.8f}".rstrip("0").rstrip("."),
    }
    return alpaca_post("/v2/orders", payload, headers)


def bump_to_min_notional(qty, price, min_notional=MIN_NOTIONAL_USD):
    """Scale qty up to clear Alpaca's minimum crypto order notional if the
    raw qty*price falls short (happens to odd-lot remainders left over from
    partial fills). Returns (qty, bumped_bool). Caller is responsible for
    capping the result to whatever's actually available for sell orders -
    this just computes what's needed to clear the floor, it doesn't know
    about position balance."""
    if price <= 0:
        return qty, False
    if qty * price >= min_notional:
        return qty, False
    return min_notional / price, True


def init_grid(symbol, budget, levels, atr_mult, min_spacing_pct, breakout_atr_mult,
               headers, data_headers, execute, actions, errors):
    quote = get_crypto_quote(symbol, data_headers)
    mid = quote["mid"]
    atr_pct, atr_abs, _ = get_daily_atr_pct(symbol, data_headers)
    grid = build_grid(symbol, mid, atr_pct, atr_abs, budget, levels, atr_mult, min_spacing_pct)

    state = load_state(symbol)
    state.update({
        "initialized": True,
        "symbol": symbol,
        "center_price": mid,
        "lower_bound": grid["lower_bound"],
        "upper_bound": grid["upper_bound"],
        "atr_abs": grid["atr_abs"],
        "spacing_pct": grid["spacing_pct"],
        "qty_per_level": grid["qty_per_level"],
        "levels": grid["levels"],
        "level_orders": {},
        "last_fill_activity_id": get_latest_fill_activity_id(symbol, headers),
        "realized_pl": 0.0,
        "completed_round_trips": 0,
        "halted": False,
        "halt_reason": None,
        "breakout_atr_mult": breakout_atr_mult,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })

    placed = 0
    for lvl in grid["levels"]:
        if lvl >= mid:
            continue  # sell levels only get populated once a buy below them fills
        if execute:
            try:
                order = place_limit(symbol, "buy", grid["qty_per_level"], lvl, headers)
                state["level_orders"][str(lvl)] = {"side": "buy", "order_id": order["id"], "qty": grid["qty_per_level"]}
                placed += 1
            except Exception as e:
                errors.append(f"init buy @ {lvl}: {e}")
        else:
            state["level_orders"][str(lvl)] = {"side": "buy", "order_id": None, "qty": grid["qty_per_level"]}
            placed += 1

    actions.append(f"grid initialized: {len(grid['levels'])} levels, spacing {grid['spacing_pct']:.3f}%, "
                    f"range [{grid['lower_bound']:.4f}, {grid['upper_bound']:.4f}], "
                    f"qty/level {grid['qty_per_level']:.8f}, placed {placed} buy orders "
                    f"({'EXECUTED' if execute else 'DRY-RUN preview only'})")
    return state


def find_level_index(levels, price, tol=1e-6):
    for i, lvl in enumerate(levels):
        if abs(lvl - price) < max(tol, lvl * 1e-6):
            return i
    return None


def run(symbol, budget, levels, atr_mult, min_spacing_pct, breakout_atr_mult,
        execute, flatten, reset):
    errors, actions = [], []
    result = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol, "mode": "EXECUTE" if execute else "DRY-RUN",
        "quote": None, "grid": None, "position": None,
        "halted": False, "flattened": False, "new_fills": [],
        "actions": actions, "errors": errors,
    }

    env = load_env()
    key = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    sec = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
    if not key or not sec:
        errors.append("APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not set in .env")
        return result
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "Content-Type": "application/json"}
    data_headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}

    # ── --flatten: cancel everything, market-close position, halt ──────────
    if flatten:
        try:
            n_cancelled = cancel_all_open_orders(symbol, headers) if execute else 0
            flat_order = market_flatten_position(symbol, headers) if execute else None
            state = load_state(symbol)
            state["halted"] = True
            state["halt_reason"] = "manual --flatten"
            save_state(symbol, state)
            actions.append(f"flatten: cancelled {n_cancelled} open orders, "
                            f"{'market-closed position' if flat_order else 'no position to close'} "
                            f"({'EXECUTED' if execute else 'DRY-RUN preview only'})")
            result["flattened"] = True
            result["halted"] = True
        except Exception as e:
            errors.append(f"flatten failed: {e}")
        print(json.dumps(result, indent=2))
        return result

    state = load_state(symbol)

    # ── (re)initialize grid ──────────────────────────────────────────────
    if reset or not state.get("initialized"):
        if reset and state.get("initialized") and execute:
            try:
                cancel_all_open_orders(symbol, headers)
                actions.append("reset: cancelled existing open grid orders before rebuilding")
            except Exception as e:
                errors.append(f"reset cancel failed: {e}")
        try:
            state = init_grid(symbol, budget, levels, atr_mult, min_spacing_pct, breakout_atr_mult,
                               headers, data_headers, execute, actions, errors)
            save_state(symbol, state)
        except Exception as e:
            errors.append(f"grid init failed: {e}")
            print(json.dumps(result, indent=2))
            return result

    if state.get("halted"):
        result["halted"] = True
        actions.append(f"grid halted ({state.get('halt_reason')}) - run with --reset to rebuild and restart")
        try:
            quote = get_crypto_quote(symbol, data_headers)
            result["quote"] = quote
        except Exception:
            pass
        print(json.dumps(result, indent=2))
        return result

    # ── live quote + breakout check ─────────────────────────────────────
    try:
        quote = get_crypto_quote(symbol, data_headers)
        result["quote"] = quote
    except Exception as e:
        errors.append(f"quote fetch failed: {e}")
        print(json.dumps(result, indent=2))
        return result

    mid = quote["mid"]
    lower, upper = state["lower_bound"], state["upper_bound"]
    atr_abs = state["atr_abs"]
    breakout_buffer = breakout_atr_mult * atr_abs
    if mid < lower - breakout_buffer or mid > upper + breakout_buffer:
        direction = "below" if mid < lower else "above"
        reason = (f"breakout: mid {mid:.4f} is {direction} range [{lower:.4f}, {upper:.4f}] "
                  f"by more than {breakout_atr_mult}x ATR ({breakout_buffer:.4f})")
        try:
            n_cancelled = cancel_all_open_orders(symbol, headers) if execute else 0
            flat_order = market_flatten_position(symbol, headers) if execute else None
            state["halted"] = True
            state["halt_reason"] = reason
            save_state(symbol, state)
            actions.append(f"BREAKOUT HALT - {reason}. Cancelled {n_cancelled} orders, "
                            f"{'market-flattened position' if flat_order else 'no position to flatten'} "
                            f"({'EXECUTED' if execute else 'DRY-RUN preview only'})")
            result["halted"] = True
        except Exception as e:
            errors.append(f"breakout halt handling failed: {e}")
        print(json.dumps(result, indent=2))
        return result

    # ── position + fill reconciliation ──────────────────────────────────
    try:
        result["position"] = get_crypto_position(symbol, headers)
    except Exception as e:
        errors.append(f"position fetch failed: {e}")

    try:
        new_fills = get_new_fill_activities(symbol, headers, since_id=state.get("last_fill_activity_id"))
    except Exception as e:
        errors.append(f"fill activity fetch failed: {e}")
        new_fills = []

    # ── flush any pending re-arms left over from a prior cycle's failed order
    #    placement (e.g. the qty_available 403 hit during testing) - retried
    #    BEFORE processing new fills so a stuck re-arm doesn't block forever ──
    pending = state.get("pending_reorders", [])
    still_pending = []
    for p in pending:
        qty = p["qty"]
        qty, bumped = bump_to_min_notional(qty, p["price"])
        if p["side"] == "sell" and bumped:
            # don't bump a sell past what we actually hold - that would just
            # trade a $10-min-notional 403 for an insufficient-balance one
            try:
                pos = get_crypto_position(symbol, headers)
                qty = min(qty, pos.get("qty_available", qty))
                bumped = qty * p["price"] >= MIN_NOTIONAL_USD
            except Exception as e:
                errors.append(f"qty_available lookup for pending sell bump failed: {e}")
                qty, bumped = p["qty"], False
        try:
            order = place_limit(symbol, p["side"], qty, p["price"], headers)
            state["level_orders"][str(p["price"])] = {"side": p["side"], "order_id": order["id"], "qty": qty}
            note = f" (bumped {p['qty']:.8f} -> {qty:.8f} to clear ${MIN_NOTIONAL_USD:.2f} min notional)" if bumped else ""
            actions.append(f"retried pending {p['side']} @ {p['price']:.4f} qty {qty:.8f} - placed successfully{note}")
        except Exception as e:
            errors.append(f"pending {p['side']} @ {p['price']} retry failed again: {e}")
            still_pending.append(p)
    state["pending_reorders"] = still_pending

    levels = state["levels"]

    # Group fills by order_id BEFORE processing: a single limit order can
    # fill in multiple partial-fill activity events under the same order_id
    # (confirmed live during testing - a 0.0027 BTC buy filled as two
    # separate FILL rows). Processing them one at a time deletes the level
    # tracking after the first partial, so the second partial's qty would
    # find no tracked level and get silently dropped - a real inventory
    # leak. Summing per order_id first and re-arming ONCE with the total
    # filled qty (volume-weighted avg price) avoids that.
    fills_by_order = {}
    for a in new_fills:
        oid = a.get("order_id")
        fills_by_order.setdefault(oid, []).append(a)

    for oid, fill_group in fills_by_order.items():
        total_qty = sum(float(a.get("qty") or 0) for a in fill_group)
        vwap = sum(float(a.get("qty") or 0) * float(a.get("price") or 0) for a in fill_group) / total_qty if total_qty else 0
        side = fill_group[0].get("side")

        level_price, level_key = None, None
        for k, v in state["level_orders"].items():
            if v.get("order_id") == oid:
                level_price, level_key = float(k), k
                break

        for a in fill_group:
            fill_row = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "symbol": symbol, "order_id": oid, "activity_id": a.get("id"),
                "side": a.get("side"), "qty": a.get("qty"), "filled_avg_price": a.get("price"),
                "filled_at": a.get("transaction_time"), "level_price": level_price,
            }
            result["new_fills"].append(fill_row)
            append_csv(fills_log_path(symbol), FILL_FIELDS, fill_row)
            if a.get("id") and (state.get("last_fill_activity_id") is None or a["id"] > state["last_fill_activity_id"]):
                state["last_fill_activity_id"] = a["id"]

        qty, price = total_qty, vwap

        if level_price is None:
            actions.append(f"fill {side} {qty:.8f}@{price:.4f} (order {oid}) matched no tracked grid "
                            f"level order_id - manual order or stale state, skipping re-arm")
            continue

        idx = find_level_index(levels, level_price)
        if side == "buy":
            # place a sell one level up using the coin we just bought. Alpaca
            # deducts the crypto taker fee (up to 0.25%) from the asset
            # received, so actual held qty is slightly less than the raw
            # fill qty - selling the raw fill qty gets a 403 "insufficient
            # balance" (confirmed live during testing). Cap to whatever's
            # actually available.
            #
            # IMPORTANT: the level is freed (del) regardless of whether the
            # re-arm succeeds - we must never lose track of a held position
            # because its re-arm order failed. If placement fails, the
            # re-arm is queued in pending_reorders and retried every future
            # cycle until it succeeds (see flush above), rather than
            # silently forgetting the inventory.
            del state["level_orders"][level_key]
            if idx is not None and idx + 1 < len(levels):
                sell_price = levels[idx + 1]
                sell_qty = qty
                qty_available = None
                try:
                    pos = get_crypto_position(symbol, headers)
                    qty_available = pos.get("qty_available", qty)
                    sell_qty = min(qty, qty_available)
                except Exception as e:
                    errors.append(f"qty_available lookup failed, using raw fill qty (may 403): {e}")
                bumped_sell, sell_bumped_flag = bump_to_min_notional(sell_qty, sell_price)
                if sell_bumped_flag and qty_available is not None:
                    # only bump up to whatever coin we actually hold - never
                    # claim more than the position has just to clear $10
                    sell_qty = min(bumped_sell, qty_available)
                if execute:
                    try:
                        order = place_limit(symbol, "sell", sell_qty, sell_price, headers)
                        state["level_orders"][str(sell_price)] = {"side": "sell", "order_id": order["id"], "qty": sell_qty}
                        actions.append(f"buy filled @ {price:.4f} (level {level_price:.4f}) -> "
                                       f"placed sell @ {sell_price:.4f} qty {sell_qty:.8f}")
                    except Exception as e:
                        errors.append(f"re-arm sell @ {sell_price} after buy fill failed: {e} - queued for retry")
                        state.setdefault("pending_reorders", []).append(
                            {"side": "sell", "price": sell_price, "qty": sell_qty})
                else:
                    actions.append(f"[DRY-RUN] would place sell @ {sell_price:.4f} qty {sell_qty:.8f} "
                                   f"after buy fill @ {price:.4f}")
        elif side == "sell":
            buy_cost = levels[idx - 1] if idx is not None and idx > 0 else level_price
            realized = (price - buy_cost) * qty
            state["realized_pl"] = round(state.get("realized_pl", 0.0) + realized, 2)
            state["completed_round_trips"] = state.get("completed_round_trips", 0) + 1
            # re-arm the level this sell vacated with a fresh buy, one level
            # down. Same "free the level first, queue-retry on failure"
            # discipline as the buy-fill branch above.
            del state["level_orders"][level_key]
            if idx is not None and idx - 1 >= 0:
                buy_price = levels[idx - 1]
                buy_qty, buy_bumped = bump_to_min_notional(qty, buy_price)
                if execute:
                    try:
                        order = place_limit(symbol, "buy", buy_qty, buy_price, headers)
                        state["level_orders"][str(buy_price)] = {"side": "buy", "order_id": order["id"], "qty": buy_qty}
                        note = f" (bumped {qty:.8f} -> {buy_qty:.8f} to clear ${MIN_NOTIONAL_USD:.2f} min notional)" if buy_bumped else ""
                        actions.append(f"sell filled @ {price:.4f} (level {level_price:.4f}, "
                                       f"+${realized:.2f}) -> re-armed buy @ {buy_price:.4f} qty {buy_qty:.8f}{note}")
                    except Exception as e:
                        errors.append(f"re-arm buy @ {buy_price} after sell fill failed: {e} - queued for retry")
                        state.setdefault("pending_reorders", []).append(
                            {"side": "buy", "price": buy_price, "qty": buy_qty})
                else:
                    actions.append(f"[DRY-RUN] would re-arm buy @ {buy_price:.4f} qty {buy_qty:.8f} "
                                   f"after sell fill @ {price:.4f} (+${realized:.2f})")

    save_state(symbol, state)

    open_buy = sum(1 for v in state["level_orders"].values() if v["side"] == "buy")
    open_sell = sum(1 for v in state["level_orders"].values() if v["side"] == "sell")
    # Broker fees (Alpaca paper charges crypto pair fees) from the daily
    # pnlLedger run - realized_pl above is gross round-trip P&L.
    try:
        from alpacaFees import fees_for_symbol
        fees_usd = fees_for_symbol(symbol)
    except Exception:
        fees_usd = 0.0
    result["grid"] = {
        "lower_bound": state["lower_bound"], "upper_bound": state["upper_bound"],
        "spacing_pct": state["spacing_pct"], "qty_per_level": state["qty_per_level"],
        "open_buy_levels": open_buy, "open_sell_levels": open_sell,
        "realized_pl": state.get("realized_pl", 0.0),
        "fees_usd": round(fees_usd, 2),
        "realized_pl_net": round(state.get("realized_pl", 0.0) + fees_usd, 2),
        "completed_round_trips": state.get("completed_round_trips", 0),
    }

    cycle_row = {
        "timestamp": result["timestamp"], "symbol": symbol, "mid": mid,
        "lower_bound": state["lower_bound"], "upper_bound": state["upper_bound"],
        "position_qty": (result["position"] or {}).get("qty"),
        "open_buy_levels": open_buy, "open_sell_levels": open_sell,
        "realized_pl": state.get("realized_pl", 0.0),
        "completed_round_trips": state.get("completed_round_trips", 0),
        "halted": state.get("halted", False),
        "actions": " | ".join(actions), "errors": " | ".join(errors),
    }
    append_csv(cycle_log_path(symbol), CYCLE_FIELDS, cycle_row)

    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USD", help="e.g. BTC/USD, ETH/USD, SOL/USD")
    parser.add_argument("--budget", type=float, required=False, default=2000.0,
                         help="total USD notional allocated across all grid levels (default 2000)")
    parser.add_argument("--levels", type=int, default=DEFAULT_LEVELS,
                         help="grid steps each side of center at init (default 8)")
    parser.add_argument("--atr-mult", type=float, default=DEFAULT_ATR_MULT,
                         help="half-range = this x daily ATR%% (default 1.5)")
    parser.add_argument("--min-spacing-pct", type=float, default=DEFAULT_MIN_SPACING_PCT,
                         help="floor on level spacing %% - shrinks level count if range too tight "
                              "to clear fees at requested --levels (default 0.6)")
    parser.add_argument("--breakout-atr-mult", type=float, default=DEFAULT_BREAKOUT_ATR_MULT,
                         help="halt+flatten if price moves this many extra ATRs past the grid range (default 1.0)")
    parser.add_argument("--execute", action="store_true", help="place real paper orders (default: dry run)")
    parser.add_argument("--flatten", action="store_true", help="cancel all open grid orders + market-close position, halt")
    parser.add_argument("--reset", action="store_true", help="cancel existing grid (if any) and rebuild range/levels from current price+ATR")
    args = parser.parse_args()

    run(symbol=args.symbol.upper(), budget=args.budget, levels=args.levels,
        atr_mult=args.atr_mult, min_spacing_pct=args.min_spacing_pct,
        breakout_atr_mult=args.breakout_atr_mult,
        execute=args.execute, flatten=args.flatten, reset=args.reset)
