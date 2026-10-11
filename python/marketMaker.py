#!/usr/bin/env python3
"""
marketMaker.py
==============
Two-sided quoting / liquidity-provision test bot - paper trading via Alpaca.

IMPORTANT REALITY CHECK (read before trusting any P&L this produces):
  - This is NOT a real market maker. No exchange rebates, no queue priority,
    no colocated feed. It is a "quote at the touch and manage inventory"
    strategy test, which is the closest thing retail paper trading can
    approximate.
  - Alpaca paper fills a resting limit order only when it becomes marketable
    against the real-time SIP NBBO (buy fills when ask <= limit, sell fills
    when bid >= limit). There is NO queue-position simulation - in real life
    you'd often lose the fill to someone who was resting at the touch before
    you. Paper fill rates are therefore optimistic vs. reality.
  - Paper trading does not model market impact, information leakage, or
    price improvement. Treat all P&L here as an upper bound / rough signal,
    not a forecast of live performance.

Strategy (simple, defensible, inventory-aware):
  1. Pull live NBBO for the symbol.
  2. If flat/low inventory: rest a buy limit at the bid and a sell limit at
     the ask (join the touch), same size both sides.
  3. If inventory has built up on one side beyond a threshold, skip/skew the
     order that would add to it (classic inventory control) so the bot works
     to flatten itself rather than run a directional position.
  4. Re-quote only when the touch has moved (avoid needless cancel/replace).
  5. Log every cycle (quotes, position, account) and every detected fill to
     CSV for later offline P&L / spread-capture analysis.
  6. --flatten market-closes any open position and cancels resting orders
     (meant to run once near the close - no overnight inventory).

Usage:
  venv/bin/python3 python/marketMaker.py --symbol SOXL              # dry run
  venv/bin/python3 python/marketMaker.py --symbol SOXL --execute    # live paper orders
  venv/bin/python3 python/marketMaker.py --symbol SOXL --execute --flatten  # EOD flatten

Output JSON printed to stdout (used by Discord /mm command and cron).
"""

import os
import sys
import json
import math
import datetime
import argparse
import requests

# ── Config ──────────────────────────────────────────────────────────────────
ENV_PATH        = os.path.expanduser("~/discordBot/.env")
OUT_DIR         = os.path.expanduser("~/discordBot/outputs/markets")
PAPER_BASE      = "https://paper-api.alpaca.markets"
DATA_BASE       = "https://data.alpaca.markets"

DEFAULT_SYMBOL       = "SOXL"
DEFAULT_QTY           = 10      # shares per side per quote
INVENTORY_MULT        = 3       # stop adding to a side once |position| > QTY * this
MAX_POSITION_NOTIONAL_PCT = 0.05   # hard cap: resting inventory notional vs equity
MAX_DAILY_LOSS_PCT    = 0.02    # halt new quotes if daily P&L < -2% of equity
MIN_REQUOTE_MOVE      = 0.0     # re-quote whenever touch changes at all (ticks are cheap here)


def state_path(symbol):
    return os.path.join(OUT_DIR, f"mm_state_{symbol}.json")


def cycle_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_cycle_log_{symbol}.csv")


def fills_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_fills_log_{symbol}.csv")


def load_env():
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def load_state(symbol):
    p = state_path(symbol)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {
        "open_buy_order_id": None,
        "open_buy_price": None,
        "open_sell_order_id": None,
        "open_sell_price": None,
        "last_fill_activity_id": None,  # watermark into /v2/account/activities/FILL
        "cum_fills": 0,
        "cum_buy_shares": 0,
        "cum_sell_shares": 0,
    }


def save_state(symbol, state):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(state_path(symbol), "w") as f:
        json.dump(state, f, indent=2)


def alpaca_get(endpoint, headers, base=PAPER_BASE):
    resp = requests.get(f"{base}{endpoint}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def alpaca_post(endpoint, payload, headers, base=PAPER_BASE):
    resp = requests.post(f"{base}{endpoint}", json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def alpaca_delete(endpoint, headers, base=PAPER_BASE):
    resp = requests.delete(f"{base}{endpoint}", headers=headers, timeout=15)
    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()
    return True


def get_account(headers):
    data = alpaca_get("/v2/account", headers)
    return {
        "equity": float(data.get("equity", 0)),
        "cash": float(data.get("cash", 0)),
        "buying_power": float(data.get("buying_power", 0)),
        "last_equity": float(data.get("last_equity", 0)),
        "status": data.get("status", "unknown"),
    }


def get_position(symbol, headers):
    try:
        data = alpaca_get(f"/v2/positions/{symbol}", headers)
        return {
            "shares": float(data.get("qty", 0)),
            "market_value": float(data.get("market_value", 0)),
            "avg_entry": float(data.get("avg_entry_price", 0)),
            "unrealized_pl": float(data.get("unrealized_pl", 0)),
        }
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"shares": 0.0, "market_value": 0.0, "avg_entry": 0.0, "unrealized_pl": 0.0}
        raise


def get_quote(symbol, data_headers):
    data = alpaca_get(f"/v2/stocks/{symbol}/snapshot", data_headers, base=DATA_BASE)
    q = data.get("latestQuote", {})
    bid, ask = float(q.get("bp", 0) or 0), float(q.get("ap", 0) or 0)
    if bid <= 0 or ask <= 0:
        raise RuntimeError(f"no valid NBBO for {symbol}: {q}")
    return {"bid": bid, "ask": ask, "bid_size": q.get("bs"), "ask_size": q.get("as")}


def get_open_orders(symbol, headers):
    orders = alpaca_get(f"/v2/orders?status=open&symbols={symbol}&limit=50", headers)
    return orders if isinstance(orders, list) else []


def get_new_fill_activities(headers, since_id=None, max_pages=20):
    """Pull FILL activities (per-fill, not per-order) newer than since_id.

    Why this instead of GET /v2/orders?status=all: an order's status only
    ever reads "filled" if it fills completely. Because this bot cancels and
    re-places resting limit orders almost every cycle (requoting to follow
    the touch), it's common for an order to partially fill and then get
    cancelled - that order's terminal status is "canceled", never "filled",
    so a status-based check silently drops the partial fill that actually
    happened. /v2/account/activities/FILL records every individual fill
    regardless of what happens to the parent order afterward, so it's the
    only reliable source for "what actually executed".

    Also note: Alpaca's activities endpoint ?symbol= filter has been
    observed to leak other symbols through (seen SPY rows return on a
    SOXL-filtered request) - so we deliberately don't filter server-side and
    instead filter client-side in the caller.

    since_id: exclusive lower bound (an activity "id", which sorts
    lexicographically by time since its prefix is a zero-padded timestamp).
    None means "this is the first run" - caller should set a conservative
    policy (e.g. single page) rather than silently backfilling full history.
    Returns activities ascending by id (oldest-new first).
    """
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
    return collected


def cancel_order(order_id, headers):
    return alpaca_delete(f"/v2/orders/{order_id}", headers)


def place_limit(symbol, side, qty, price, headers, dry_run=True):
    if dry_run:
        return {"id": f"DRY-{side.upper()}-{price}", "status": "simulated_new",
                "side": side, "qty": str(qty), "limit_price": str(round(price, 2)), "symbol": symbol}
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(round(price, 2)),
    }
    return alpaca_post("/v2/orders", payload, headers)


def market_close_position(symbol, shares, headers, dry_run=True):
    """shares > 0 means long (sell to flatten); shares < 0 means short (buy to flatten)."""
    side = "sell" if shares > 0 else "buy"
    qty = abs(shares)
    if dry_run:
        return {"id": "DRY-FLATTEN", "status": "simulated_new", "side": side, "qty": str(qty), "type": "market"}
    payload = {"symbol": symbol, "qty": str(qty), "side": side, "type": "market", "time_in_force": "day"}
    return alpaca_post("/v2/orders", payload, headers)


def compute_pnl_from_fills(symbol):
    """Realized P&L via average-cost method over the LOCAL fills log.

    Only correct once the fills log itself is complete/accurate - this bot
    now sources that log from /v2/account/activities/FILL (see
    get_new_fill_activities), which captures every partial fill even when
    the parent order is later cancelled/replaced. Dedupes on activity_id
    (fallback: order_id+side+qty+filled_at) so re-running after a crash
    mid-write can't double count.
    """
    path = fills_log_path(symbol)
    if not os.path.exists(path):
        return {"realized_pl": 0.0, "fill_count": 0, "net_shares": 0.0}

    import csv
    with open(path) as f:
        rows = list(csv.DictReader(f))

    seen = set()
    dedup = []
    for r in rows:
        key = r.get("activity_id") or f"{r.get('order_id')}|{r.get('side')}|{r.get('qty')}|{r.get('filled_at')}"
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    dedup.sort(key=lambda r: r.get("filled_at") or r.get("timestamp") or "")

    position = 0.0
    avg_cost = 0.0
    realized_pl = 0.0
    for r in dedup:
        qty = float(r["qty"])
        px = float(r["filled_avg_price"])
        if r["side"] == "buy":
            if position >= 0:
                new_pos = position + qty
                avg_cost = (avg_cost * position + px * qty) / new_pos if new_pos != 0 else 0
                position = new_pos
            else:
                cover_qty = min(qty, -position)
                realized_pl += (avg_cost - px) * cover_qty
                position += qty
                if position > 0:
                    avg_cost = px
        else:
            if position <= 0:
                new_pos = position - qty
                avg_cost = (avg_cost * (-position) + px * qty) / (-new_pos) if new_pos != 0 else 0
                position = new_pos
            else:
                sell_qty = min(qty, position)
                realized_pl += (px - avg_cost) * sell_qty
                position -= qty
                if position < 0:
                    avg_cost = px

    return {
        "realized_pl": round(realized_pl, 2),
        "fill_count": len(dedup),
        "net_shares_from_fills": position,
        "avg_cost_from_fills": round(avg_cost, 4),
    }


def is_market_hours():
    now_et = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
    if now_et.weekday() >= 5:
        return False
    open_ = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_ = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_ <= now_et <= close_


def append_csv(path, fields, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path)
    with open(path, "a") as f:
        if write_header:
            f.write(",".join(fields) + "\n")
        vals = [str(row.get(fld, "")).replace(",", ";").replace("\n", " ") for fld in fields]
        f.write(",".join(vals) + "\n")


CYCLE_FIELDS = [
    "timestamp", "symbol", "bid", "ask", "mid", "spread_bps",
    "position_shares", "avg_entry", "unrealized_pl",
    "buy_action", "buy_price", "sell_action", "sell_price",
    "equity", "daily_pnl_pct", "halted", "actions", "errors",
]

FILL_FIELDS = ["timestamp", "symbol", "order_id", "activity_id", "side", "qty", "filled_avg_price", "filled_at"]


def run(symbol=DEFAULT_SYMBOL, qty=DEFAULT_QTY, execute=False, flatten=False):
    errors = []
    actions = []
    result = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol,
        "mode": "EXECUTE" if execute else "DRY-RUN",
        "market_hours": is_market_hours(),
        "quote": None,
        "account": None,
        "position": None,
        "open_orders": {"buy": None, "sell": None},
        "actions": actions,
        "halted": False,
        "flattened": False,
        "new_fills": [],
        "errors": errors,
    }

    env = load_env()
    key = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    sec = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
    if not key or not sec:
        errors.append("APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not set in .env")
        return result
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "Content-Type": "application/json"}
    data_headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}

    state = load_state(symbol)

    # ── account + position ──────────────────────────────────────────────────
    try:
        account = get_account(headers)
        result["account"] = account
    except Exception as e:
        errors.append(f"account fetch failed: {e}")
        return result

    try:
        position = get_position(symbol, headers)
        result["position"] = position
    except Exception as e:
        errors.append(f"position fetch failed: {e}")
        position = {"shares": 0.0, "market_value": 0.0, "avg_entry": 0.0, "unrealized_pl": 0.0}
        result["position"] = position

    daily_pnl_pct = 0.0
    if account["last_equity"] > 0:
        daily_pnl_pct = (account["equity"] - account["last_equity"]) / account["last_equity"]
    result["daily_pnl_pct"] = round(daily_pnl_pct * 100, 3)

    # ── detect fills since last cycle via account activities (per-fill, not
    #    per-order, so partial-fills-then-cancelled are never dropped) ──────
    try:
        new_fills = get_new_fill_activities(headers, since_id=state.get("last_fill_activity_id"))
        # client-side symbol filter - Alpaca's activities endpoint has no
        # reliable server-side symbol param and has been seen to leak other
        # symbols through on this account (shared with the SPY bot)
        new_fills = [a for a in new_fills if a.get("symbol") == symbol]
        for a in new_fills:
            fill_row = {
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "order_id": a.get("order_id"),
                "activity_id": a.get("id"),
                "side": a.get("side"),
                "qty": a.get("qty"),
                "filled_avg_price": a.get("price"),
                "filled_at": a.get("transaction_time"),
            }
            result["new_fills"].append(fill_row)
            append_csv(fills_log_path(symbol), FILL_FIELDS, fill_row)
            state["cum_fills"] = state.get("cum_fills", 0) + 1
            if a.get("side") == "buy":
                state["cum_buy_shares"] = state.get("cum_buy_shares", 0) + float(a.get("qty") or 0)
            else:
                state["cum_sell_shares"] = state.get("cum_sell_shares", 0) + float(a.get("qty") or 0)
        if new_fills:
            state["last_fill_activity_id"] = new_fills[-1]["id"]
    except Exception as e:
        errors.append(f"fill detection failed: {e}")

    # ── FLATTEN mode: cancel resting orders + market close position, then exit ──
    if flatten:
        try:
            open_orders = get_open_orders(symbol, headers)
            for o in open_orders:
                if not execute:
                    actions.append(f"[dry-run] would cancel order {o.get('id')}")
                else:
                    cancel_order(o.get("id"), headers)
                    actions.append(f"cancelled order {o.get('id')}")
        except Exception as e:
            errors.append(f"cancel during flatten failed: {e}")

        shares = position["shares"]
        if abs(shares) > 0:
            try:
                resp = market_close_position(symbol, shares, headers, dry_run=not execute)
                actions.append(f"flatten order: {resp.get('side')} {resp.get('qty')} shares (id={resp.get('id')})")
                result["flattened"] = execute
            except Exception as e:
                errors.append(f"flatten order failed: {e}")
        else:
            actions.append("already flat, nothing to close")

        state["open_buy_order_id"] = None
        state["open_sell_order_id"] = None
        save_state(symbol, state)
        return result

    # ── halt checks ──────────────────────────────────────────────────────────
    halted_reasons = []
    if account["status"] != "ACTIVE":
        halted_reasons.append(f"account not ACTIVE (status={account['status']})")
    if daily_pnl_pct < -MAX_DAILY_LOSS_PCT:
        halted_reasons.append(f"daily loss {daily_pnl_pct*100:.2f}% exceeds -{MAX_DAILY_LOSS_PCT*100:.0f}% halt limit")
    if not result["market_hours"]:
        halted_reasons.append("outside market hours")

    if halted_reasons:
        result["halted"] = True
        actions.append("HALTED: " + "; ".join(halted_reasons))
        # cancel any resting orders while halted (don't leave stale quotes overnight/on halt)
        try:
            open_orders = get_open_orders(symbol, headers)
            for o in open_orders:
                if execute:
                    cancel_order(o.get("id"), headers)
                    actions.append(f"cancelled order {o.get('id')} (halted)")
        except Exception as e:
            errors.append(f"cancel-on-halt failed: {e}")
        save_state(symbol, state)
        _log_cycle(result, state, errors, actions)
        return result

    # ── quote ────────────────────────────────────────────────────────────────
    try:
        quote = get_quote(symbol, data_headers)
        result["quote"] = quote
    except Exception as e:
        errors.append(f"quote fetch failed: {e}")
        save_state(symbol, state)
        return result

    bid, ask = quote["bid"], quote["ask"]
    mid = (bid + ask) / 2
    spread_bps = (ask - bid) / mid * 10000
    result["quote"]["mid"] = round(mid, 4)
    result["quote"]["spread_bps"] = round(spread_bps, 2)

    shares = position["shares"]
    max_notional = account["equity"] * MAX_POSITION_NOTIONAL_PCT
    max_shares_by_notional = int(max_notional / mid) if mid > 0 else 0
    inventory_cap = min(qty * INVENTORY_MULT, max_shares_by_notional) if max_shares_by_notional else qty * INVENTORY_MULT

    want_buy = shares < inventory_cap    # don't add to long beyond cap
    want_sell = shares > -inventory_cap  # don't add to short beyond cap

    try:
        open_orders = get_open_orders(symbol, headers)
    except Exception as e:
        errors.append(f"open orders fetch failed: {e}")
        open_orders = []

    open_buy = next((o for o in open_orders if o.get("side") == "buy"), None)
    open_sell = next((o for o in open_orders if o.get("side") == "sell"), None)

    # BUY side
    if not want_buy:
        if open_buy:
            if execute:
                cancel_order(open_buy["id"], headers)
            actions.append(f"cancelled buy (inventory cap: {shares} >= {inventory_cap})")
        result["open_orders"]["buy"] = None
        buy_action, buy_price = "SKIP (inventory cap)", None
    else:
        need_requote = (not open_buy) or (float(open_buy.get("limit_price", 0)) != round(bid, 2))
        if need_requote:
            if open_buy and execute:
                cancel_order(open_buy["id"], headers)
                actions.append(f"cancelled stale buy @ {open_buy.get('limit_price')}")
            resp = place_limit(symbol, "buy", qty, bid, headers, dry_run=not execute)
            state["open_buy_order_id"] = resp.get("id")
            state["open_buy_price"] = round(bid, 2)
            actions.append(f"placed buy {qty}sh @ {round(bid,2)} (id={resp.get('id')})")
            buy_action, buy_price = "PLACED", round(bid, 2)
        else:
            buy_action, buy_price = "HOLD (already at touch)", round(bid, 2)
        result["open_orders"]["buy"] = {"price": round(bid, 2), "qty": qty}

    # SELL side
    if not want_sell:
        if open_sell:
            if execute:
                cancel_order(open_sell["id"], headers)
            actions.append(f"cancelled sell (inventory cap: {shares} <= {-inventory_cap})")
        result["open_orders"]["sell"] = None
        sell_action, sell_price = "SKIP (inventory cap)", None
    else:
        need_requote = (not open_sell) or (float(open_sell.get("limit_price", 0)) != round(ask, 2))
        if need_requote:
            if open_sell and execute:
                cancel_order(open_sell["id"], headers)
                actions.append(f"cancelled stale sell @ {open_sell.get('limit_price')}")
            resp = place_limit(symbol, "sell", qty, ask, headers, dry_run=not execute)
            state["open_sell_order_id"] = resp.get("id")
            state["open_sell_price"] = round(ask, 2)
            actions.append(f"placed sell {qty}sh @ {round(ask,2)} (id={resp.get('id')})")
            sell_action, sell_price = "PLACED", round(ask, 2)
        else:
            sell_action, sell_price = "HOLD (already at touch)", round(ask, 2)
        result["open_orders"]["sell"] = {"price": round(ask, 2), "qty": qty}

    save_state(symbol, state)
    _log_cycle(result, state, errors, actions, buy_action, buy_price, sell_action, sell_price)
    return result


def _log_cycle(result, state, errors, actions, buy_action="N/A", buy_price=None, sell_action="N/A", sell_price=None):
    q = result.get("quote") or {}
    pos = result.get("position") or {}
    acct = result.get("account") or {}
    row = {
        "timestamp": result["timestamp"],
        "symbol": result["symbol"],
        "bid": q.get("bid", ""),
        "ask": q.get("ask", ""),
        "mid": q.get("mid", ""),
        "spread_bps": q.get("spread_bps", ""),
        "position_shares": pos.get("shares", ""),
        "avg_entry": pos.get("avg_entry", ""),
        "unrealized_pl": pos.get("unrealized_pl", ""),
        "buy_action": buy_action,
        "buy_price": buy_price if buy_price is not None else "",
        "sell_action": sell_action,
        "sell_price": sell_price if sell_price is not None else "",
        "equity": acct.get("equity", ""),
        "daily_pnl_pct": result.get("daily_pnl_pct", ""),
        "halted": result.get("halted", False),
        "actions": " | ".join(actions),
        "errors": " | ".join(errors),
    }
    try:
        append_csv(cycle_log_path(result["symbol"]), CYCLE_FIELDS, row)
    except Exception as e:
        errors.append(f"cycle log write failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--qty", type=int, default=DEFAULT_QTY)
    parser.add_argument("--execute", action="store_true", help="place real paper orders (default: dry run)")
    parser.add_argument("--flatten", action="store_true", help="cancel resting orders + market-close position")
    parser.add_argument("--pnl", action="store_true", help="read-only: print realized/unrealized P&L from the fills log, no quoting")
    args = parser.parse_args()

    if args.pnl:
        symbol = args.symbol.upper()
        pnl = compute_pnl_from_fills(symbol)
        env = load_env()
        key = env.get("APCA_PAPER_API_KEY_ID", "").strip()
        sec = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
        out = {"symbol": symbol, **pnl}
        if key and sec:
            try:
                headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
                pos = get_position(symbol, headers)
                out["broker_position_shares"] = pos["shares"]
                out["broker_avg_entry"] = pos["avg_entry"]
                out["broker_unrealized_pl"] = pos["unrealized_pl"]
                out["total_pl"] = round(pnl["realized_pl"] + pos["unrealized_pl"], 2)
            except Exception as e:
                out["broker_fetch_error"] = str(e)
        print(json.dumps(out, indent=2))
    else:
        out = run(symbol=args.symbol.upper(), qty=args.qty, execute=args.execute, flatten=args.flatten)
        print(json.dumps(out, indent=2))
