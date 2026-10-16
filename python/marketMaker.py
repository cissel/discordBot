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
DEFAULT_SPREAD_WIDEN_BPS = 0.0  # extra bps pushed onto EACH side beyond the raw
                                 # touch (or beyond the RL policy's spread) - see
                                 # --spread-widen-bps. Added Oct 2026 after the
                                 # SOXL bot was found to be overtrading (median
                                 # ~3.8bps NBBO spread, quoting dead at touch means
                                 # almost every tick move triggers a requote/fill).
                                 # Widening is a pure PRICE change, not a cadence
                                 # change - the 15s loop / fill-triggered early-wake
                                 # added earlier today is untouched, so reaction
                                 # speed to real opportunities is unchanged; it just
                                 # takes a bigger move to actually earn a fill.
MAX_DAILY_LOSS_PCT    = 0.02    # halt new quotes if daily P&L < -2% of equity
MIN_REQUOTE_MOVE      = 0.0     # re-quote whenever touch changes at all (ticks are cheap here)

# ── RL policy integration (Oct 2026) ─────────────────────────────────────────
# Optional: --rl-policy <path to a trained PPO .zip> replaces the baseline
# "quote exactly at touch, zero skew" rule with a learned (half_spread_bps,
# skew_bps) policy trained in python/rl/mm_env.py / train_mm_rl.py. See that
# module's docstring for the full validation history - the canonical
# validated model as of Oct 2026 is python/rl/models/ppo_mm_mixed_1000000.zip
# (61-68% win rate vs. this exact baseline rule on real historical SOXL data).
#
# These two constants MUST match MMEnvConfig's inventory_cap/equity_ref used
# during training (see mm_env.py) - they're the normalization the policy's
# observation space was trained against, NOT live account numbers. Using
# live equity/cap here instead would silently feed the policy an
# out-of-distribution observation.
RL_INVENTORY_CAP_NORM = 30.0   # must match mm_env.MMEnvConfig.inventory_cap
RL_EQUITY_REF         = 83000.0  # must match mm_env.MMEnvConfig.equity_ref
RL_HALF_SPREAD_RANGE  = (0.5, 15.0)   # must match mm_env.py action space bound [0]
RL_SKEW_RANGE         = (-8.0, 8.0)   # must match mm_env.py action space bound [1]


def state_path(symbol):
    return os.path.join(OUT_DIR, f"mm_state_{symbol}.json")


def cycle_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_cycle_log_{symbol}.csv")


def fills_log_path(symbol):
    return os.path.join(OUT_DIR, f"mm_fills_log_{symbol}.csv")


def live_fills_path(symbol):
    """Path to the tick-level fills file maintained by the ALREADY-RUNNING
    mm_dashboard/live_poller.py (persistent websocket to Alpaca's
    trade_updates stream - sub-second latency, no polling delay). Used only
    as a fast "did anything just fill" signal to wake run_loop early for an
    immediate reprice; the authoritative fill record for P&L is still
    get_new_fill_activities()/the fills log. If the poller isn't running
    this file just won't update and run_loop degrades gracefully to its
    fixed time interval.
    """
    return os.path.join(OUT_DIR, f"mm_live_fills_{symbol}.json")


def get_latest_live_fill_epoch(symbol):
    """Returns the max 'epoch' timestamp across all fills in the live-poller
    fills file, or None if the file is missing/empty/unparseable (e.g. the
    poller process isn't running, or we raced its write mid-flush - either
    way, treat as "nothing new to report" rather than erroring the loop)."""
    path = live_fills_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        fills = data.get("fills") or []
        if not fills:
            return None
        return max(f.get("epoch", 0) for f in fills)
    except Exception:
        return None


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
    if bid >= ask:
        # Locked/crossed NBBO straight from the feed - a known (if rare) IEX
        # SIP artifact, not unique to this account. Added Oct 2026 after a
        # bad snapshot like this fed straight into baseline pricing (target
        # buy = bid, target sell = ask) and produced a SELL ORDER PRICED
        # BELOW THE BUY ORDER before either was even submitted. Treat this
        # exactly like "no valid NBBO" - raise and let the caller skip the
        # cycle, never quote off a crossed snapshot.
        raise RuntimeError(f"crossed/locked NBBO for {symbol}: bid {bid} >= ask {ask} (feed artifact, skipping cycle)")
    return {"bid": bid, "ask": ask, "bid_size": q.get("bs"), "ask_size": q.get("as")}


def enforce_no_self_cross(symbol, buy_price, sell_price):
    """Hard block - added Oct 2026 after live sell orders were seen resting
    BELOW live buy orders (the two sides of our own quote crossing each
    other), which is a strictly worse failure mode than either side merely
    crossing the external market: it is an arbitrage against ourselves and a
    broker could legally match the two directly.

    This is a SEPARATE check from enforce_no_cross (which re-validates each
    side independently against a freshly re-fetched external NBBO). Two
    sides can each individually pass enforce_no_cross - which re-fetches the
    NBBO separately for the buy call and, moments later, the sell call - and
    still end up crossed relative to EACH OTHER if, e.g., a crossed/locked
    NBBO snapshot briefly priced both sides off a bad bid/ask (see
    get_quote's bid>=ask guard) or the two independent fresh-quote fetches
    returned different touches a few hundred ms apart. This check compares
    the two computed target prices directly, with no dependency on any
    external quote fetch, so it cannot be fooled by a bad/inconsistent NBBO
    the same way the other two guards individually could be.

    Returns (ok: bool, reason: str|None). ok=False means BOTH sides should
    be skipped this cycle - submitting just one of them would still leave a
    lopsided, probably-still-wrong quote, and the underlying price computation
    is already known bad.
    """
    if buy_price >= sell_price:
        return False, (f"SELF-CROSS blocked: computed buy price {buy_price:.4f} >= computed sell price "
                        f"{sell_price:.4f} - both orders skipped this cycle (would cross our own book)")
    return True, None


def enforce_no_cross(symbol, side, price, data_headers):
    """Hard safety gate - added Oct 2026 after resting limit orders were
    observed going out priced THROUGH the touch (buys above the live ask,
    sells below the live bid) and filling almost instantly with price
    improvement - exactly what a marketable limit order looks like when it's
    supposed to be a passive quote.

    Root cause: the cycle fetches ONE NBBO snapshot up top, then does a chain
    of other Alpaca calls (fill activities, account, position, open orders,
    cancel) before actually POSTing the new order. On a volatile 3x-leveraged
    ETF the touch can move enough in that window for a price computed off the
    now-stale quote to cross the NEW market. This is also a backstop against
    any bad RL policy output.

    Re-fetches the NBBO as close to submission as possible and refuses to
    submit if the price would be immediately marketable:
      - buy  blocked if price > current ask
      - sell blocked if price < current bid
    Deliberately does NOT reprice/clamp to "fix" it - a crossed price means
    something upstream is already wrong, so we skip the order for this cycle
    and surface it loudly rather than guess a new number and let the bug hide.

    Returns (ok: bool, bid: float|None, ask: float|None, reason: str|None).
    Runs in both dry-run and --execute so the block is visible in previews too.
    """
    try:
        fresh = get_quote(symbol, data_headers)
    except Exception as e:
        return False, None, None, f"{side} blocked: could not refresh quote for pre-submit safety check ({e})"
    bid, ask = fresh["bid"], fresh["ask"]
    if side == "buy" and price > ask:
        return False, bid, ask, (f"BUY blocked: computed price {price:.4f} > live ask {ask:.4f} "
                                  f"(would cross market) - order skipped this cycle")
    if side == "sell" and price < bid:
        return False, bid, ask, (f"SELL blocked: computed price {price:.4f} < live bid {bid:.4f} "
                                  f"(would cross market) - order skipped this cycle")
    return True, bid, ask, None


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


def cap_qty_for_flip(side, qty, position_shares):
    """Alpaca hard-rejects (403 'insufficient qty available') any single
    order that would flip a position's sign in one shot - e.g. selling 10
    shares while holding only 8 long (would need to close 8 AND open a
    2-share short in the same order). It does NOT auto-split this; you must
    close to flat first, then a SEPARATE later order can open the other
    side. Added Oct 2026 after this crashed both live MM bots in production
    (place_limit's raise_for_status propagated an uncaught HTTPError out of
    run_loop, killing the whole ~55s loop budget on a single rejected order).

    Caps the requested qty down to what CAN legally be submitted in one
    order without crossing zero:
      - SELL with position > 0: cap to min(qty, position) (close only, no flip)
      - BUY  with position < 0: cap to min(qty, -position) (cover only, no flip)
      - otherwise (already flat, or already positioned the same direction
        as this order, or position is 0): no cap needed, full qty is legal.

    This means a flip to the opposite side happens gradually over cycles
    (close to flat first, then a later cycle opens the new side from a flat
    base) rather than in one order - slower, but never produces this 403.
    Returns the (possibly reduced) qty; 0 means skip this side entirely this
    cycle (nothing left to legally trade at this qty).
    """
    if side == "sell" and position_shares > 0:
        return max(0, min(qty, int(position_shares)))
    if side == "buy" and position_shares < 0:
        return max(0, min(qty, int(-position_shares)))
    return qty


def place_limit_safe(symbol, side, qty, price, position_shares, headers, dry_run=True):
    """Wraps place_limit with (1) the long/short-flip qty cap (see
    cap_qty_for_flip) and (2) a catch-all around the actual HTTP call so ANY
    broker-side rejection (bad qty, insufficient buying power, asset halted,
    network blip, whatever) is reported back as a normal (ok=False, reason)
    result instead of an unhandled exception. An uncaught exception here
    previously propagated all the way out of run_loop and killed the entire
    remaining loop budget (up to 55s with NO requoting on either side) over
    a single rejected order - strictly worse than just skipping that one
    side for one cycle and trying again next cycle.

    Returns (ok: bool, resp: dict|None, reason: str|None, capped_qty: int).
    """
    capped_qty = cap_qty_for_flip(side, qty, position_shares)
    if capped_qty <= 0:
        return False, None, (f"{side} skipped: qty capped to 0 (would require flipping position "
                              f"{position_shares} across zero in a single order - Alpaca disallows this, "
                              f"closing to flat first)"), capped_qty
    try:
        resp = place_limit(symbol, side, capped_qty, price, headers, dry_run=dry_run)
        return True, resp, None, capped_qty
    except Exception as e:
        return False, None, f"{side} order REJECTED by broker: {e}", capped_qty


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


# ── RL policy loading + inference (Oct 2026) ─────────────────────────────────
_rl_model_cache = {}  # path -> loaded PPO model, so cron fires don't reload every 5 min... actually
                      # each cron fire is a fresh process, so this only helps within a single run()
                      # call tree (e.g. tests) - documented so nobody "fixes" it into a bug later.


def load_rl_model(model_path):
    """Lazily import torch/stable_baselines3 and load the PPO model. Import is
    deferred (not top-of-file) so marketMaker.py's baseline path has zero new
    dependency weight and --rl-policy is fully opt-in.

    CRITICAL Pi gotcha (see memory/skill notes): the Pi's venv torch 2.12
    aarch64 build SIGILLs on multi-threaded BLAS above ~25x25 matmul size.
    MUST set OMP_NUM_THREADS=1 / MKL_NUM_THREADS=1 BEFORE `import torch`,
    and call torch.set_num_threads(1) after. This is cheap (PPO inference on
    a tiny MLP) so single-threaded has no real perf cost here anyway.
    """
    if model_path in _rl_model_cache:
        return _rl_model_cache[model_path]
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    import torch
    torch.set_num_threads(1)
    from stable_baselines3 import PPO
    model = PPO.load(model_path)
    _rl_model_cache[model_path] = model
    return model


def build_rl_observation(shares, avg_entry, mid, market_spread_bps):
    """Build the 4-dim observation vector matching mm_env.py's _obs(), using
    LIVE values but normalized against the TRAINING-time constants (see
    RL_INVENTORY_CAP_NORM / RL_EQUITY_REF comment above) - not live
    account equity or the live inventory_cap, which can differ from what
    the policy was trained against.

    time_remaining_frac is set to a fixed mid-session placeholder (0.5)
    since marketMaker.py's cron doesn't track intraday step position the
    way the training env does - a documented simplification, not a bug.
    Revisit if EOD behavior near the close looks off in practice.
    """
    import numpy as np
    inv_norm = shares / RL_INVENTORY_CAP_NORM
    time_remaining = 0.5  # placeholder - see docstring
    spread_norm = market_spread_bps / 10.0
    unrl_pnl_frac = (shares * (mid - avg_entry)) / RL_EQUITY_REF if avg_entry else 0.0
    obs = np.array([inv_norm, time_remaining, spread_norm, unrl_pnl_frac * 100.0], dtype=np.float32)
    return np.clip(obs, -5.0, 5.0)


def get_rl_quote_params(model_path, shares, avg_entry, mid, market_spread_bps):
    """Returns (half_spread_bps, skew_bps) from the trained policy, clipped
    to the same bounds the policy was trained under. Any exception here
    should be treated as a hard stop by the caller (fall back to baseline
    quoting, never silently quote with garbage params from a half-failed
    model call)."""
    model = load_rl_model(model_path)
    obs = build_rl_observation(shares, avg_entry, mid, market_spread_bps)
    action, _ = model.predict(obs, deterministic=True)
    half_spread_bps = float(min(max(action[0], RL_HALF_SPREAD_RANGE[0]), RL_HALF_SPREAD_RANGE[1]))
    skew_bps = float(min(max(action[1], RL_SKEW_RANGE[0]), RL_SKEW_RANGE[1]))
    return half_spread_bps, skew_bps


def run(symbol=DEFAULT_SYMBOL, qty=DEFAULT_QTY, execute=False, flatten=False, rl_policy=None,
        spread_widen_bps=DEFAULT_SPREAD_WIDEN_BPS):
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
            # Persist the watermark IMMEDIATELY after each fill is logged (Oct
            # 2026 dedup fix), not deferred to run()'s end via the later
            # save_state() calls. Previously, if this cycle crashed/raised
            # ANYWHERE after appending fill rows but before reaching one of
            # those later save_state() calls (quote fetch failure, halt,
            # uncaught exception in run_loop's safety net, process kill),
            # state["last_fill_activity_id"] never made it to disk - so the
            # NEXT cycle re-fetched the same already-logged fills from
            # /v2/account/activities/FILL and appended duplicate activity_id
            # rows to mm_fills_log_{symbol}.csv. Confirmed root cause of the
            # dashboard's fake multi-hundred-dollar realized P&L whipsaws
            # (James flagged a ~$1k-to-$1k swing in under an hour that the
            # broker's own activity log shows never happened - traced to
            # ~280 duplicate fill rows logged across SOXL/TQQQ/GME that day).
            state["last_fill_activity_id"] = a["id"]
            save_state(symbol, state)
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

        # Clear price alongside order_id (Oct 2026 fix - see the matching
        # fix in the BLOCKED-cross branches below): app.py's dashboard
        # fallback (orders_doc empty -> read mm_state_*.json) keys off
        # open_buy_price/open_sell_price being truthy, NOT the order_id, so
        # leaving a stale price here after flattening would show a resting
        # quote on the dashboard that doesn't exist at the broker anymore.
        state["open_buy_order_id"] = None
        state["open_buy_price"] = None
        state["open_sell_order_id"] = None
        state["open_sell_price"] = None
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

    # ── RL quote pricing (Oct 2026): if --rl-policy was passed, replace the
    # baseline "quote exactly at bid/ask" target prices with the trained
    # policy's (half_spread_bps, skew_bps) output, mirroring mm_env.py's
    # step() price math exactly. ANY failure here (model load, inference,
    # bad output) falls back to the baseline bid/ask - never quotes with a
    # half-computed RL result. ──────────────────────────────────────────────
    target_buy_price, target_sell_price = bid, ask
    if rl_policy:
        try:
            half_spread_bps, skew_bps = get_rl_quote_params(
                rl_policy, shares, position["avg_entry"], mid, spread_bps
            )
            buy_dist_bps = max(half_spread_bps - skew_bps, 0.01)
            sell_dist_bps = max(half_spread_bps + skew_bps, 0.01)
            target_buy_price = mid * (1 - buy_dist_bps / 10000.0)
            target_sell_price = mid * (1 + sell_dist_bps / 10000.0)
            result["rl"] = {
                "half_spread_bps": round(half_spread_bps, 3),
                "skew_bps": round(skew_bps, 3),
                "target_buy_price": round(target_buy_price, 4),
                "target_sell_price": round(target_sell_price, 4),
            }
            actions.append(f"RL policy: half_spread={half_spread_bps:.2f}bps skew={skew_bps:+.2f}bps")
        except Exception as e:
            errors.append(f"RL policy inference failed, falling back to baseline bid/ask quoting: {e}")
            target_buy_price, target_sell_price = bid, ask
            result["rl"] = {"error": str(e)}

    # ── Spread widening (Oct 2026): push each side further from mid by a
    # fixed extra bps on TOP of whatever target_buy/sell_price already is
    # (raw touch, or the RL policy's quote) - applied AFTER the RL branch so
    # it composes with either pricing source instead of being bypassed by
    # one of them. This only changes WHERE we quote, not WHEN - cadence
    # (loop-interval-sec / fill-triggered early wake) is untouched, so a
    # real move to the new, wider price is still caught just as fast as
    # before. Purely a response to overtrading at the raw touch (median
    # NBBO spread on these names is only a few bps, so quoting AT it means
    # almost any tick triggers a cancel/replace or a fill).
    if spread_widen_bps:
        target_buy_price = target_buy_price * (1 - spread_widen_bps / 10000.0)
        target_sell_price = target_sell_price * (1 + spread_widen_bps / 10000.0)
        actions.append(f"spread widened +/-{spread_widen_bps:.2f}bps "
                        f"-> buy {round(target_buy_price,4)} / sell {round(target_sell_price,4)}")

    try:
        open_orders = get_open_orders(symbol, headers)
    except Exception as e:
        errors.append(f"open orders fetch failed: {e}")
        open_orders = []

    open_buy = next((o for o in open_orders if o.get("side") == "buy"), None)
    open_sell = next((o for o in open_orders if o.get("side") == "sell"), None)

    # ── Self-cross hard block (Oct 2026): precompute what EACH side's
    # resting price will actually be after this cycle BEFORE touching
    # either order, and refuse to submit/hold anything this cycle if that
    # would leave a buy resting at/above a sell. This is SEPARATE from (and
    # stronger than) enforce_no_cross, which only validates each side
    # independently against a freshly re-fetched external NBBO - two sides
    # can each individually pass that check (fetched moments apart) and
    # still cross EACH OTHER, e.g. if a bad feed snapshot priced both sides
    # off a locked/crossed NBBO (see get_quote's bid>=ask guard added the
    # same day), or if only ONE side needed requoting this cycle and the
    # other side's untouched, already-resting order is left behind the new
    # price. Checking the PROJECTED final state of both sides up front
    # (not just the two freshly computed targets) catches that case too.
    # On a block: cancel BOTH resting orders and place nothing this cycle -
    # partial action (only fixing one side) could still leave a crossed
    # book, and the underlying price computation is already known bad.
    need_requote_buy = want_buy and ((not open_buy) or (float(open_buy.get("limit_price", 0)) != round(target_buy_price, 2)))
    need_requote_sell = want_sell and ((not open_sell) or (float(open_sell.get("limit_price", 0)) != round(target_sell_price, 2)))
    projected_buy_price = (round(target_buy_price, 2) if need_requote_buy
                            else (float(open_buy["limit_price"]) if (want_buy and open_buy) else None))
    projected_sell_price = (round(target_sell_price, 2) if need_requote_sell
                             else (float(open_sell["limit_price"]) if (want_sell and open_sell) else None))

    self_cross_blocked = False
    if projected_buy_price is not None and projected_sell_price is not None:
        ok, reason = enforce_no_self_cross(symbol, projected_buy_price, projected_sell_price)
        if not ok:
            self_cross_blocked = True
            errors.append(reason)
            actions.append(f"SELF-CROSS HARD BLOCK: {reason} - cancelling both sides, no quotes placed this cycle")
            if execute:
                if open_buy:
                    cancel_order(open_buy["id"], headers)
                    actions.append(f"cancelled buy @ {open_buy.get('limit_price')} (self-cross safety)")
                if open_sell:
                    cancel_order(open_sell["id"], headers)
                    actions.append(f"cancelled sell @ {open_sell.get('limit_price')} (self-cross safety)")
            state["open_buy_order_id"] = None
            state["open_buy_price"] = None
            state["open_sell_order_id"] = None
            state["open_sell_price"] = None

    if self_cross_blocked:
        result["open_orders"]["buy"] = None
        result["open_orders"]["sell"] = None
        buy_action, buy_price = "BLOCKED (self-cross safety)", None
        sell_action, sell_price = "BLOCKED (self-cross safety)", None
    else:
        # BUY side
        if not want_buy:
            if open_buy:
                if execute:
                    cancel_order(open_buy["id"], headers)
                actions.append(f"cancelled buy (inventory cap: {shares} >= {inventory_cap})")
            result["open_orders"]["buy"] = None
            buy_action, buy_price = "SKIP (inventory cap)", None
        else:
            need_requote = need_requote_buy
            if need_requote:
                if open_buy and execute:
                    cancel_order(open_buy["id"], headers)
                    actions.append(f"cancelled stale buy @ {open_buy.get('limit_price')}")
                # Hard safety gate: re-check against a FRESH quote immediately
                # before submission, refuse to place a buy priced above the
                # live ask (see enforce_no_cross docstring for why this exists).
                ok, fresh_bid, fresh_ask, reason = enforce_no_cross(symbol, "buy", target_buy_price, data_headers)
                if not ok:
                    errors.append(reason)
                    actions.append(f"BLOCKED buy order: {reason}")
                    result["open_orders"]["buy"] = None
                    # Bug fix (Oct 2026): the stale buy was already cancelled
                    # at the broker a few lines up (if open_buy and execute),
                    # but state["open_buy_order_id"/"open_buy_price"] was
                    # never cleared here - unlike the broker-rejection branch
                    # below, which does clear it. That left a dead order id/
                    # price sitting in mm_state_{symbol}.json, which both the
                    # live dashboard's fallback display (app.py) and
                    # live_poller.py's seed_orders_from_state() read straight
                    # from - showing a "resting" buy/sell that doesn't exist
                    # at the broker anymore, sometimes crossed against the
                    # other (correctly updated) side. This is what produced
                    # the stale/crossed quote lines James flagged on the
                    # dashboard. Clearing it here makes this branch match the
                    # broker-rejection branch's behavior.
                    state["open_buy_order_id"] = None
                    state["open_buy_price"] = None
                    buy_action, buy_price = "BLOCKED (would cross market)", None
                else:
                    ok2, resp, reason2, capped_qty = place_limit_safe(
                        symbol, "buy", qty, target_buy_price, shares, headers, dry_run=not execute)
                    if not ok2:
                        errors.append(reason2)
                        actions.append(f"BLOCKED buy order: {reason2}")
                        result["open_orders"]["buy"] = None
                        state["open_buy_order_id"] = None
                        state["open_buy_price"] = None
                        buy_action, buy_price = "BLOCKED (broker rejected/flip-capped)", None
                    else:
                        state["open_buy_order_id"] = resp.get("id")
                        state["open_buy_price"] = round(target_buy_price, 2)
                        qty_note = f" (qty capped {qty}->{capped_qty}, avoiding long/short flip)" if capped_qty != qty else ""
                        actions.append(f"placed buy {capped_qty}sh @ {round(target_buy_price,2)} (id={resp.get('id')}){qty_note}")
                        buy_action, buy_price = "PLACED", round(target_buy_price, 2)
                        result["open_orders"]["buy"] = {"price": round(target_buy_price, 2), "qty": capped_qty}
            else:
                buy_action, buy_price = "HOLD (already at touch)", round(target_buy_price, 2)
                result["open_orders"]["buy"] = {"price": round(target_buy_price, 2), "qty": qty}

        # SELL side
        if not want_sell:
            if open_sell:
                if execute:
                    cancel_order(open_sell["id"], headers)
                actions.append(f"cancelled sell (inventory cap: {shares} <= {-inventory_cap})")
            result["open_orders"]["sell"] = None
            sell_action, sell_price = "SKIP (inventory cap)", None
        else:
            need_requote = need_requote_sell
            if need_requote:
                if open_sell and execute:
                    cancel_order(open_sell["id"], headers)
                    actions.append(f"cancelled stale sell @ {open_sell.get('limit_price')}")
                # Hard safety gate: re-check against a FRESH quote immediately
                # before submission, refuse to place a sell priced below the
                # live bid (see enforce_no_cross docstring for why this exists).
                ok, fresh_bid, fresh_ask, reason = enforce_no_cross(symbol, "sell", target_sell_price, data_headers)
                if not ok:
                    errors.append(reason)
                    actions.append(f"BLOCKED sell order: {reason}")
                    result["open_orders"]["sell"] = None
                    # Bug fix (Oct 2026): mirrors the buy-side fix above -
                    # clear the stale order id/price so state (and anything
                    # reading it, e.g. the dashboard) doesn't keep reporting
                    # a resting sell that the broker no longer has.
                    state["open_sell_order_id"] = None
                    state["open_sell_price"] = None
                    sell_action, sell_price = "BLOCKED (would cross market)", None
                else:
                    ok2, resp, reason2, capped_qty = place_limit_safe(
                        symbol, "sell", qty, target_sell_price, shares, headers, dry_run=not execute)
                    if not ok2:
                        errors.append(reason2)
                        actions.append(f"BLOCKED sell order: {reason2}")
                        result["open_orders"]["sell"] = None
                        state["open_sell_order_id"] = None
                        state["open_sell_price"] = None
                        sell_action, sell_price = "BLOCKED (broker rejected/flip-capped)", None
                    else:
                        state["open_sell_order_id"] = resp.get("id")
                        state["open_sell_price"] = round(target_sell_price, 2)
                        qty_note = f" (qty capped {qty}->{capped_qty}, avoiding long/short flip)" if capped_qty != qty else ""
                        actions.append(f"placed sell {capped_qty}sh @ {round(target_sell_price,2)} (id={resp.get('id')}){qty_note}")
                        sell_action, sell_price = "PLACED", round(target_sell_price, 2)
                        result["open_orders"]["sell"] = {"price": round(target_sell_price, 2), "qty": capped_qty}
            else:
                sell_action, sell_price = "HOLD (already at touch)", round(target_sell_price, 2)
                result["open_orders"]["sell"] = {"price": round(target_sell_price, 2), "qty": qty}

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


LOCK_PATH_TMPL = os.path.join(OUT_DIR, "mm_loop_{symbol}.lock")


def _acquire_lock(symbol):
    """Non-blocking exclusive lock so a loop invocation can never overlap a
    still-running prior one (cron fires every 1 min; --loop runs for most of
    that minute, so without this a slow cycle could cause two concurrent
    processes to both see the same stale open order and double up).
    Returns an open file handle (keep it alive - closing releases the lock)
    or None if another instance already holds it.
    """
    import fcntl
    os.makedirs(OUT_DIR, exist_ok=True)
    path = LOCK_PATH_TMPL.format(symbol=symbol)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def run_loop(symbol, qty, execute, rl_policy, interval_sec, total_sec, min_cycle_gap_sec=2.0,
             fill_poll_sec=1.0, spread_widen_bps=DEFAULT_SPREAD_WIDEN_BPS):
    """Re-run the quoting cycle every interval_sec for up to total_sec inside
    ONE process (so a cached RL model, see load_rl_model's _rl_model_cache,
    is loaded once rather than once per cycle - torch+PPO load is ~13s on
    the Pi, which would blow the whole budget if repeated every 15s).

    Exists because the cron scheduler's minimum granularity is 1 minute but
    the touch can move meaningfully faster than that on a leveraged ETF -
    this is how we get effective ~15s requoting without cron supporting
    sub-minute schedules directly. total_sec should leave a buffer before
    the next cron tick (default 55s of a 60s window) so this process exits
    cleanly before the next one starts rather than relying on the lock to
    sort out an overrun.

    Fill-triggered early wake (Oct 2026): rather than sleeping the full
    interval_sec blindly between cycles, this polls
    get_latest_live_fill_epoch() (sourced from mm_dashboard/live_poller.py's
    websocket-fed live-fills file - sub-second latency, no extra Alpaca REST
    calls) every fill_poll_sec and breaks the sleep early the instant a NEW
    fill epoch appears, so a side that just got hit is requoted immediately
    (fresh inventory/skew) instead of waiting up to interval_sec. Falls back
    to the plain fixed-interval sleep with no behavior change if the live
    poller isn't running (get_latest_live_fill_epoch returns None - never
    treated as "a fill happened").

    min_cycle_gap_sec is a floor on how soon a fill-triggered wake can fire
    another cycle after the previous one started - without it a burst of
    several partial fills on the same order (common - see mm_live_fills
    examples, a single order often fills in 2-4 partials within ~1s) would
    trigger a cycle per partial and hammer the Alpaca API for no benefit,
    since one reprice already accounts for the new inventory from all of
    them by the time it runs.
    """
    lock_fh = _acquire_lock(symbol)
    if lock_fh is None:
        print(json.dumps({"skipped": True, "reason": f"previous --loop invocation for {symbol} still running"}))
        return
    try:
        import time
        start = time.monotonic()
        n = 0
        fill_wakes = 0
        last_seen_fill_epoch = get_latest_live_fill_epoch(symbol)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= total_sec:
                break
            cycle_start = time.monotonic()
            try:
                out = run(symbol=symbol, qty=qty, execute=execute, flatten=False, rl_policy=rl_policy,
                          spread_widen_bps=spread_widen_bps)
            except Exception as e:
                # Last-resort safety net (Oct 2026): place_limit_safe already
                # catches broker rejections for individual orders, but this
                # catches ANYTHING else unanticipated (network blip, Alpaca
                # outage, a bug) so a single bad cycle degrades to "skip this
                # cycle, try again next interval" instead of killing the
                # entire remaining loop budget (up to total_sec with zero
                # requoting on either side) - which is exactly what happened
                # in production before this was added: an uncaught
                # HTTPError from a single rejected order took down the whole
                # SOXL and TQQQ bots' 1-minute cron cycles.
                out = {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"), "symbol": symbol,
                       "errors": [f"UNCAUGHT exception in run(), cycle skipped: {e}"], "actions": [],
                       "loop_cycle_crashed": True}
            n += 1
            print(json.dumps(out))
            # snapshot AFTER running - a fill that happened mid-cycle (and is
            # therefore already reflected in the inventory/open-orders state
            # this cycle just fetched) shouldn't immediately re-trigger
            last_seen_fill_epoch = get_latest_live_fill_epoch(symbol) or last_seen_fill_epoch

            # Fast-retry on a blocked side (Oct 2026): "BLOCKED (would cross
            # market)" / self-cross-block means a side that SHOULD be resting
            # right now has nothing live at the broker (see the state-clearing
            # fix in run() this same day - previously the dead order id/price
            # lingered in state and showed as a stale/crossed quote on the
            # dashboard, which is the actual bug James flagged: "orders
            # flipped" / missed profits / unnecessary losses were naked or
            # stale inventory sitting unhedged while we waited out the full
            # interval_sec before trying that side again). Rather than wait
            # the full 15s for the next scheduled cycle, retry almost
            # immediately (same min_cycle_gap_sec floor as fill-triggered
            # wakes, so a volatile stretch of repeated blocks still can't
            # hammer the API). This does NOT change behavior when nothing
            # was blocked - sleep_budget stays at the normal interval_sec.
            was_blocked = any(
                "BLOCKED" in a or "SELF-CROSS" in a for a in (out.get("actions") or [])
            )

            elapsed = time.monotonic() - start
            remaining_in_window = total_sec - elapsed
            sleep_budget = min(interval_sec, max(remaining_in_window, 0))
            if was_blocked:
                sleep_budget = min(sleep_budget, min_cycle_gap_sec)
            if sleep_budget <= 0:
                break

            # Sleep in short slices, polling for a new fill epoch each time,
            # so we can wake early instead of always waiting the full interval.
            woke_on_fill = False
            slept = 0.0
            while slept < sleep_budget:
                since_last_cycle = time.monotonic() - cycle_start
                if since_last_cycle >= min_cycle_gap_sec:
                    latest = get_latest_live_fill_epoch(symbol)
                    if latest is not None and (last_seen_fill_epoch is None or latest > last_seen_fill_epoch):
                        woke_on_fill = True
                        break
                this_slice = min(fill_poll_sec, sleep_budget - slept)
                time.sleep(this_slice)
                slept += this_slice
            if woke_on_fill:
                fill_wakes += 1
        print(json.dumps({"loop_summary": True, "symbol": symbol, "cycles_run": n, "fill_triggered_wakes": fill_wakes}))
    finally:
        import fcntl
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--qty", type=int, default=DEFAULT_QTY)
    parser.add_argument("--execute", action="store_true", help="place real paper orders (default: dry run)")
    parser.add_argument("--flatten", action="store_true", help="cancel resting orders + market-close position")
    parser.add_argument("--pnl", action="store_true", help="read-only: print realized/unrealized P&L from the fills log, no quoting")
    parser.add_argument("--rl-policy", default=None,
                         help="path to a trained PPO .zip (python/rl/) to drive quote pricing "
                              "instead of the baseline quote-at-touch rule. See RL_* constants "
                              "above - must match the model's training config. Any inference "
                              "failure falls back to baseline bid/ask quoting for that cycle.")
    parser.add_argument("--loop-interval-sec", type=float, default=None,
                         help="if set, re-run the quoting cycle every N seconds inside this one "
                              "process instead of running once (see run_loop docstring) - this is "
                              "how sub-minute requoting is achieved despite cron's 1-minute floor.")
    parser.add_argument("--loop-total-sec", type=float, default=55.0,
                         help="with --loop-interval-sec: stop looping after this many seconds "
                              "total (default 55, leaving buffer before a 1-min cron's next tick).")
    parser.add_argument("--spread-widen-bps", type=float, default=DEFAULT_SPREAD_WIDEN_BPS,
                         help="extra bps pushed onto EACH side beyond the raw touch (or beyond "
                              "the RL policy's quote if --rl-policy is set) - widens the quoted "
                              "spread to reduce fill/requote frequency (overtrading) WITHOUT "
                              "slowing down reaction speed (loop cadence is unaffected). "
                              "0 = unchanged baseline/RL pricing.")
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
        if args.loop_interval_sec:
            run_loop(symbol=args.symbol.upper(), qty=args.qty, execute=args.execute,
                     rl_policy=args.rl_policy, interval_sec=args.loop_interval_sec,
                     total_sec=args.loop_total_sec, spread_widen_bps=args.spread_widen_bps)
        else:
            out = run(symbol=args.symbol.upper(), qty=args.qty, execute=args.execute, flatten=args.flatten,
                       rl_policy=args.rl_policy, spread_widen_bps=args.spread_widen_bps)
            print(json.dumps(out, indent=2))
