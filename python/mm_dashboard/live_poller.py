#!/usr/bin/env python3
"""
mm_dashboard/live_poller.py
============================
Persistent background service providing TICK-LEVEL live data for the MM
dashboard, independent of marketMaker.py's ~1-minute cron cycle.

Why this exists: marketMaker.py only logs a cycle snapshot (mid price,
resting order prices) and discovers fills once every ~1 minute when its own
cron fires. A dashboard reading only those files is blind to ANYTHING that
happens between cycles - which is most of what you actually want to watch
(ticks moving, orders getting hit). This service holds two persistent
websocket connections directly to Alpaca and writes small rolling JSON files
that the Flask dashboard polls at ~1Hz:

  1. Market data stream (wss://stream.data.alpaca.markets/v2/iex)
     - "quotes" channel -> every NBBO bid/ask tick
     - "trades" channel -> every actual trade print (tick-level last price)
     Written to outputs/markets/mm_live_ticks_{symbol}.json, pruned to the
     trailing LIVE_BUFFER_MINUTES.

  2. Trading account stream (wss://paper-api.alpaca.markets/stream,
     trade_updates channel) -> fires the instant an order is placed,
     replaced, canceled, or filled - this is what lets the dashboard show
     the CURRENT resting order price with no lag, and fills the instant
     they happen rather than up to 6 minutes late.
     Written to:
       outputs/markets/mm_live_orders_{symbol}.json  (current open buy/sell)
       outputs/markets/mm_live_fills_{symbol}.json   (rolling recent fills)

Both streams auto-reconnect with backoff on any error/disconnect - treat
disconnects as routine, not fatal.

Usage:
  venv/bin/python3 python/mm_dashboard/live_poller.py
Run as a long-lived background process (systemd recommended - see
mm-dashboard.service). Safe to restart any time; it holds no state that
survives a restart other than what's already in the written JSON files
(which just get reconstructed fresh from the next stream of events).
"""
import os
import json
import time
import asyncio
import datetime
import collections

import websockets

ENV_PATH = os.path.expanduser("~/discordBot/.env")
OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets")
SYMBOLS = ["SOXL", "TQQQ", "GME"]

DATA_WS_URL = "wss://stream.data.alpaca.markets/v2/sip"
TRADE_WS_URL = "wss://paper-api.alpaca.markets/stream"
# Paid Alpaca subscriptions carry SIP (full-market CTA/UTP feed) entitlement
# on the account itself, independent of whether you're trading on paper or
# live - confirmed by auth-testing the paper API keys directly against
# v2/sip (authenticated successfully, Oct'26). Previously hardcoded to
# v2/iex (the free-tier feed), which was needlessly leaving SIP's fuller
# trade tape / exchange diversity unused. If this ever reverts to IEX-only
# behavior (single exchange code 'V' on every tick), check the subscription
# status via GET /v2/account or Alpaca's dashboard - a downgraded/expired
# plan would show up as an auth or subscribe-time rejection on v2/sip, not
# silently.

LIVE_BUFFER_MINUTES = 12  # dashboard's longest live-mode window is 10 min - keep a little margin, not 20min of raw tick history
FLUSH_INTERVAL_SEC = 1.0
FILLS_BUFFER_MINUTES = 240  # keep longer fill history, it's cheap and useful for "last N" views


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


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class LiveState:
    def __init__(self):
        # per-symbol deque of {"t": iso, "type": "quote"/"trade", "bid":, "ask":, "mid":, "price":, "size":}
        self.ticks = {s: collections.deque() for s in SYMBOLS}
        # per-symbol {"buy": {...}|None, "sell": {...}|None}
        self.orders = {s: {"buy": None, "sell": None} for s in SYMBOLS}
        # per-symbol deque of fill dicts
        self.fills = {s: collections.deque() for s in SYMBOLS}
        # per-symbol set of fill "key"s currently represented in self.fills,
        # kept in lockstep with the deque's prune window - see add_fill's
        # dedup docstring.
        self._seen_fill_keys = {s: set() for s in SYMBOLS}
        self.lock = asyncio.Lock()
        # last-stored quote mid per symbol, to dedupe (see add_tick docstring)
        self._last_quote_mid = {s: None for s in SYMBOLS}

    async def add_tick(self, symbol, tick):
        """Store a tick, with quote-dedup: only keep a quote tick if the mid
        actually moved since the last one we stored for this symbol.

        Why: on a liquid ETF the IEX feed can emit 100+ quote updates/sec
        even when many of them re-report the SAME bid/ask (size-only churn,
        or genuinely identical NBBO re-broadcast) - confirmed on TQQQ,
        Oct'26: 101,915 quote ticks accumulated in under 13 minutes with
        ZERO duplicate (t, bid, ask) tuples (so not a bug replaying the same
        message), but the resulting 20MB/120k-row live-ticks file took long
        enough to json.dump() on every 1s flush_loop tick that it was
        STARVING the websocket reader task of event-loop time, making the
        feed itself fall behind (confirmed: newest tick age climbed to 7+
        minutes stale while the process sat pegged at ~75% CPU). Since the
        dashboard no longer plots individual quote points (trade-prints-only
        per James's request) and the mark-to-market equity curve only cares
        about mid CHANGES (cash + position*mid is unchanged between ticks
        with the same mid), storing every repeat-mid tick was pure waste on
        both axes. Trade ticks are NOT deduped (every trade print is
        meaningful - it's a real, usually-unique transaction)."""
        async with self.lock:
            if tick.get("type") == "quote":
                if self._last_quote_mid[symbol] == tick.get("mid"):
                    return
                self._last_quote_mid[symbol] = tick.get("mid")
            self.ticks[symbol].append(tick)

    async def prune_ticks(self):
        cutoff = time.time() - LIVE_BUFFER_MINUTES * 60
        async with self.lock:
            for s in SYMBOLS:
                dq = self.ticks[s]
                while dq and dq[0]["epoch"] < cutoff:
                    dq.popleft()

    async def set_order(self, symbol, side, order):
        async with self.lock:
            self.orders[symbol][side] = order

    async def clear_order_if_match(self, symbol, side, order_id):
        async with self.lock:
            cur = self.orders[symbol].get(side)
            if cur and cur.get("order_id") == order_id:
                self.orders[symbol][side] = None

    async def add_fill(self, symbol, fill):
        async with self.lock:
            # Dedup by stable fill identity (Oct 2026): the trade_updates
            # websocket reconnects on any drop (see trade_updates_loop's
            # except/backoff) and Alpaca can redeliver recent fill events
            # after a reconnect - with no guard here those got appended a
            # second time, so the SAME fill showed up twice in
            # mm_live_fills_{symbol}.json. That buffer feeds the dashboard's
            # live-tail P&L replay (see app.py's fills_after/
            # state_at_global_anchor), so a redelivered fill double-counted
            # straight into the animated realized/unrealized P&L - confirmed
            # root cause of a fake ~$1k-in-under-an-hour swing James flagged
            # that the broker's own activity log shows never happened.
            # fill["key"] is set by the caller to execution_id when Alpaca
            # provides one (the only truly unique identifier here), else a
            # composite of (side, qty, price, t) as a reasonable fallback.
            key = fill.get("key")
            if key is not None and key in self._seen_fill_keys[symbol]:
                return
            if key is not None:
                self._seen_fill_keys[symbol].add(key)
            self.fills[symbol].append(fill)
            cutoff = time.time() - FILLS_BUFFER_MINUTES * 60
            dq = self.fills[symbol]
            while dq and dq[0]["epoch"] < cutoff:
                dropped = dq.popleft()
                self._seen_fill_keys[symbol].discard(dropped.get("key"))

    async def snapshot(self):
        async with self.lock:
            return (
                {s: list(self.ticks[s]) for s in SYMBOLS},
                {s: dict(self.orders[s]) for s in SYMBOLS},
                {s: list(self.fills[s]) for s in SYMBOLS},
            )


STATE = LiveState()


async def market_data_loop(key, secret):
    backoff = 1
    while True:
        try:
            async with websockets.connect(DATA_WS_URL, ping_interval=15, ping_timeout=10) as ws:
                # expect [{"T":"success","msg":"connected"}]
                await ws.recv()
                await ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
                auth_resp = json.loads(await ws.recv())
                auth_ok = any(
                    isinstance(m, dict) and m.get("T") == "success" and m.get("msg") == "authenticated"
                    for m in (auth_resp if isinstance(auth_resp, list) else [auth_resp])
                )
                if not auth_ok:
                    # Alpaca allows only ONE concurrent market-data websocket
                    # per account (confirmed via docs) - code 406 "connection
                    # limit exceeded" means something else (another script, a
                    # stuck prior connection not yet timed out server-side)
                    # is already holding it. Back off longer for this
                    # specific case since retrying immediately just keeps
                    # hitting the same wall.
                    is_conn_limit = any(
                        isinstance(m, dict) and m.get("code") == 406
                        for m in (auth_resp if isinstance(auth_resp, list) else [auth_resp])
                    )
                    print(f"[market_data] auth failed: {auth_resp}", flush=True)
                    await asyncio.sleep(30 if is_conn_limit else 10)
                    continue
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "quotes": SYMBOLS,
                    "trades": SYMBOLS,
                }))
                sub_resp = json.loads(await ws.recv())
                print(f"[market_data] subscribed: {sub_resp}", flush=True)
                backoff = 1
                msg_count = 0
                last_log = time.time()
                async for raw in ws:
                    msg_count += 1
                    if time.time() - last_log > 10:
                        print(f"[market_data] {msg_count} messages in last ~10s", flush=True)
                        msg_count = 0
                        last_log = time.time()
                    msgs = json.loads(raw)
                    if not isinstance(msgs, list):
                        msgs = [msgs]
                    for m in msgs:
                        t = m.get("T")
                        sym = m.get("S")
                        if sym not in SYMBOLS:
                            continue
                        ts = m.get("t")  # RFC3339 nanosecond string
                        try:
                            epoch = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            epoch = time.time()
                        if t == "q":
                            bid, ask = m.get("bp"), m.get("ap")
                            if bid and ask:
                                await STATE.add_tick(sym, {
                                    "epoch": epoch, "t": ts, "type": "quote",
                                    "bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4),
                                    # exchange + size fields (IEX free feed reports both bid/ask
                                    # from the same venue almost always, but keep them separate
                                    # since SIP/paid feeds can differ) - used by the dashboard to
                                    # color points by exchange and size points by quoted depth.
                                    "bx": m.get("bx"), "ax": m.get("ax"),
                                    "bs": m.get("bs"), "as": m.get("as"),
                                })
                        elif t == "t":
                            price = m.get("p")
                            size = m.get("s")
                            if price:
                                await STATE.add_tick(sym, {
                                    "epoch": epoch, "t": ts, "type": "trade",
                                    "price": price, "size": size,
                                    "exchange": m.get("x"),
                                })
        except Exception as e:
            print(f"[market_data] error: {e!r}, reconnecting in {backoff}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _order_record(order_obj, event_ts):
    try:
        price = float(order_obj.get("limit_price")) if order_obj.get("limit_price") else None
    except (TypeError, ValueError):
        price = None
    return {
        "order_id": order_obj.get("id"),
        "price": price,
        "qty": order_obj.get("qty"),
        "filled_qty": order_obj.get("filled_qty"),
        "side": order_obj.get("side"),
        "updated_at": event_ts,
    }


async def trade_updates_loop(key, secret):
    backoff = 1
    while True:
        try:
            async with websockets.connect(TRADE_WS_URL, ping_interval=15, ping_timeout=10) as ws:
                await ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
                auth_resp = json.loads(await ws.recv())
                status = auth_resp.get("data", {}).get("status")
                if status != "authorized":
                    print(f"[trade_updates] auth failed: {auth_resp}", flush=True)
                    await asyncio.sleep(10)
                    continue
                await ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
                ack = json.loads(await ws.recv())
                print(f"[trade_updates] listening: {ack}", flush=True)
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("stream") != "trade_updates":
                        continue
                    data = msg.get("data", {})
                    event = data.get("event")
                    order = data.get("order", {})
                    symbol = order.get("symbol")
                    side = order.get("side")
                    if symbol not in SYMBOLS or side not in ("buy", "sell"):
                        continue
                    ts = data.get("timestamp") or now_iso()

                    if event in ("new", "replaced"):
                        await STATE.set_order(symbol, side, _order_record(order, ts))
                    elif event in ("canceled", "expired", "rejected", "done_for_day"):
                        await STATE.clear_order_if_match(symbol, side, order.get("id"))
                    elif event in ("fill", "partial_fill"):
                        try:
                            epoch = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            epoch = time.time()
                        # Dedup key (Oct 2026): execution_id is Alpaca's
                        # stable unique identifier for this specific fill
                        # event (distinct from order id, which repeats across
                        # partials) - present on every fill/partial_fill
                        # message per Alpaca's docs. Falls back to a
                        # composite if it's ever missing, so a redelivered
                        # message after a websocket reconnect still mostly
                        # gets caught. See LiveState.add_fill's docstring for
                        # why this matters.
                        exec_id = data.get("execution_id")
                        fill_key = exec_id if exec_id else f"{order.get('id')}|{side}|{data.get('qty')}|{data.get('price')}|{ts}"
                        await STATE.add_fill(symbol, {
                            "epoch": epoch, "t": ts, "side": side,
                            "price": float(data.get("price", 0) or 0),
                            "qty": float(data.get("qty", 0) or 0),
                            "event": event,
                            "key": fill_key,
                        })
                        if event == "fill":
                            await STATE.clear_order_if_match(symbol, side, order.get("id"))
                        else:
                            # partial fill: order still open, refresh its known state (reduced remaining qty)
                            await STATE.set_order(symbol, side, _order_record(order, ts))
        except Exception as e:
            print(f"[trade_updates] error: {e!r}, reconnecting in {backoff}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def flush_loop():
    os.makedirs(OUT_DIR, exist_ok=True)
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SEC)
        await STATE.prune_ticks()
        ticks, orders, fills = await STATE.snapshot()
        # run the actual file writes in a thread executor, not inline on the
        # event loop - json.dump() of a large tick list is synchronous CPU
        # work that previously blocked market_data_loop's ws.recv() for the
        # whole dump duration every single flush tick, which is what caused
        # the TQQQ feed to fall behind under high quote volume (see add_tick
        # docstring for the full incident). Quote-dedup above cuts the
        # typical payload size a lot too, but don't re-introduce a blocking
        # write even so - a sudden volume spike (news, open/close) could
        # still produce a large trades list.
        for s in SYMBOLS:
            await loop.run_in_executor(
                None, atomic_write_json, os.path.join(OUT_DIR, f"mm_live_ticks_{s}.json"),
                {"symbol": s, "ticks": ticks[s], "updated_at": now_iso()})
            await loop.run_in_executor(
                None, atomic_write_json, os.path.join(OUT_DIR, f"mm_live_orders_{s}.json"),
                {"symbol": s, **orders[s], "updated_at": now_iso()})
            await loop.run_in_executor(
                None, atomic_write_json, os.path.join(OUT_DIR, f"mm_live_fills_{s}.json"),
                {"symbol": s, "fills": fills[s], "updated_at": now_iso()})


async def seed_orders_from_state():
    """On startup, trade_updates only fires on NEW events - orders already
    resting from before this process started would show as null until the
    next requote. Seed known-open orders from marketMaker.py's own state
    file (mm_state_{symbol}.json, written every cron cycle) so the dashboard
    isn't blank for up to ~6 minutes after a (re)start."""
    for s in SYMBOLS:
        p = os.path.join(OUT_DIR, f"mm_state_{s}.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                st = json.load(f)
        except Exception:
            continue
        if st.get("open_buy_order_id") and st.get("open_buy_price"):
            await STATE.set_order(s, "buy", {
                "order_id": st["open_buy_order_id"], "price": st["open_buy_price"],
                "qty": None, "filled_qty": None, "side": "buy", "updated_at": "seeded_from_state",
            })
        if st.get("open_sell_order_id") and st.get("open_sell_price"):
            await STATE.set_order(s, "sell", {
                "order_id": st["open_sell_order_id"], "price": st["open_sell_price"],
                "qty": None, "filled_qty": None, "side": "sell", "updated_at": "seeded_from_state",
            })
    print("[live_poller] seeded initial order state from mm_state_*.json", flush=True)


async def main():
    env = load_env()
    key = env.get("APCA_PAPER_API_KEY_ID")
    secret = env.get("APCA_PAPER_API_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not found in .env")
    await seed_orders_from_state()
    print(f"[live_poller] starting, symbols={SYMBOLS}", flush=True)
    await asyncio.gather(
        market_data_loop(key, secret),
        trade_updates_loop(key, secret),
        flush_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
