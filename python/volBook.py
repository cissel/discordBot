#!/usr/bin/env python3
"""
volBook.py - SPY short iron condor book (paper), conditional VRP harvest
=========================================================================
Trades the one vol effect that survived the Oct 2026 structural studies
(python/research/structural/vrp_regime_study.py): the SPY variance risk
premium is worth harvesting EXCEPT when GEX is in its most negative trailing
quintile or the VIX term structure is inverted (VIX9D > VIX3M). On those days
RV/IV runs 0.86-0.91 and the premium is ~zero or negative.

Pre-registered rules (do not tune on live results)
  ENTRY (one per run, max MAX_OPEN concurrent, min MIN_GAP_DAYS between entries)
    eligible: not inverted (VIX9D <= VIX3M). GEX quintile gate DROPPED Oct 19 2026:
              2011-2016 holdout reversed it (study 14); gex_q is still logged at every
              entry for a forward test (re-add only if q1 entries prove worse live).
    expiry  : nearest to TARGET_DTE (30), within [DTE_MIN, DTE_MAX]
    shorts  : put and call closest to |delta| = SHORT_DELTA (0.16)
    wings   : WING_WIDTH (5.0) further OTM
    size    : contracts = floor(risk_budget / max_loss_per_contract), min 1,
              risk_budget = RISK_PCT (1%) * equity, flat (vol scalar dropped Oct 20 2026, logged only)
    price   : limit at mid of the net credit minus one tick; credit must be
              >= MIN_CREDIT_FRAC (0.20) of wing width or no trade
  EXIT (checked every run, whichever first)
    take profit : condor mark <= (1 - TP_FRAC) * credit   (TP_FRAC 0.50)
    stop        : condor mark >= (1 + STOP_MULT) * credit  (STOP_MULT 2.0)
    time        : dte <= EXIT_DTE (21)
    regime      : NOT used for exits (variance swap in the study was held to term)
  KILL (book-level; stop trading and post)
    cumulative net P&L <= -KILL_DD_PCT (4%) of launch equity, OR
    (calibration, volbook_kill_calibration.py Oct 2026: at 25 closed the win-rate review
    catches ~49% of no-edge (70%) books and ~79% of negative (62%) books at ~3% false-kill
    on an 85% book (16% on the clustered empirical distribution). It is a sanity check,
    not a test: 80% power needs ~80-100 closed positions. The 60% bar caught 10%.)
    >= 25 closed condors with win rate < 70% and net P&L < 0

Alpaca
  chain  : data v1beta1/options/snapshots/SPY (feed=indicative; greeks + IV included)
  orders : /v2/orders order_class=mleg, type=limit, 4 legs. Credit orders carry a
           NEGATIVE limit_price (dashboard convention); if the API rejects the sign
           we retry positive once and record which worked in state["price_sign"].
  level  : account options level 3 (defined-risk only), verified Oct 19 2026.

Files
  outputs/markets/vol_book_state.json   open positions, price_sign, kill flag
  outputs/markets/vol_book_log.csv      one row per entry / exit / skip
  python/pnlLedger.py maps SPY option symbols to "Vol book (IC)" (x100 multiplier).

Usage
  venv/bin/python3 python/volBook.py              # dry run: prints what it would do
  venv/bin/python3 python/volBook.py --execute    # manage exits, then maybe enter
  venv/bin/python3 python/volBook.py --execute --no-entry   # exits only
  venv/bin/python3 python/volBook.py --status     # print book
"""
import os
import re
import sys
import csv
import json
import math
import argparse
import datetime as dt

import requests

sys.path.insert(0, os.path.dirname(__file__))
from marketMaker import load_env, PAPER_BASE  # noqa: E402

DATA = "https://data.alpaca.markets"
OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets")
STATE = os.path.join(OUT_DIR, "vol_book_state.json")
LOG = os.path.join(OUT_DIR, "vol_book_log.csv")
LOG_COLS = ["ts", "event", "pos_id", "exp", "dte", "spot", "put_long", "put_short", "call_short", "call_long",
            "contracts", "credit", "mark", "pnl_usd", "reason", "gex_q", "inverted", "vix", "scalar", "order_id", "note"]

# pre-registered parameters
TARGET_DTE, DTE_MIN, DTE_MAX = 30, 25, 38
SHORT_DELTA = 0.16
WING_WIDTH = 5.0
RISK_PCT = 0.01
MIN_CREDIT_FRAC = 0.20
TP_FRAC = 0.50
STOP_MULT = 2.0
EXIT_DTE = 21
MAX_OPEN = 3
MIN_GAP_DAYS = 2
KILL_DD_PCT = 0.04
KILL_MIN_CLOSED, KILL_WIN_RATE = 25, 0.70   # 0.60 -> 0.70 Oct 20 2026, see kill calibration note
TICK = 0.01
OCC = re.compile(r"^SPY(\d{6})([CP])(\d{8})$")


def headers():
    env = load_env()
    return {"APCA-API-KEY-ID": env["APCA_PAPER_API_KEY_ID"], "APCA-API-SECRET-KEY": env["APCA_PAPER_API_SECRET_KEY"],
            "accept": "application/json", "content-type": "application/json"}


def get(url, h, **params):
    r = requests.get(url, headers=h, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"GET {url} -> {r.status_code} {r.text[:200]}")
    return r.json()


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"positions": [], "closed": [], "price_sign": -1, "killed": False, "launch_equity": None, "last_entry": None}


def save_state(s):
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=2, default=str)


def log(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_COLS})


def occ(exp, typ, strike):
    return f"SPY{exp.strftime('%y%m%d')}{typ}{int(round(strike * 1000)):08d}"


def parse_occ(sym):
    m = OCC.match(sym)
    exp = dt.datetime.strptime(m.group(1), "%y%m%d").date()
    return exp, m.group(2), int(m.group(3)) / 1000.0


# ── market data ─────────────────────────────────────────────────────────────
def spot_price(h):
    s = get(f"{DATA}/v2/stocks/snapshots", h, symbols="SPY", feed="iex")["SPY"]
    return float(s["latestTrade"]["p"])


def chain(h, exp_lo, exp_hi):
    snaps, token = {}, None
    while True:
        p = {"feed": "indicative", "limit": 1000, "expiration_date_gte": exp_lo.isoformat(), "expiration_date_lte": exp_hi.isoformat()}
        if token:
            p["page_token"] = token
        d = get(f"{DATA}/v1beta1/options/snapshots/SPY", h, **p)
        snaps.update(d.get("snapshots", {}))
        token = d.get("next_page_token")
        if not token:
            break
    rows = []
    for sym, v in snaps.items():
        if not OCC.match(sym):
            continue
        exp, typ, k = parse_occ(sym)
        q = v.get("latestQuote") or {}
        g = v.get("greeks") or {}
        bid, ask = q.get("bp"), q.get("ap")
        if bid is None or ask is None:
            continue
        rows.append({"sym": sym, "exp": exp, "type": typ, "strike": k, "bid": float(bid), "ask": float(ask),
                     "mid": (float(bid) + float(ask)) / 2, "delta": g.get("delta"), "iv": v.get("impliedVolatility")})
    return rows


def quotes(h, syms):
    d = get(f"{DATA}/v1beta1/options/quotes/latest", h, symbols=",".join(syms), feed="indicative")
    out = {}
    for s, q in d.get("quotes", {}).items():
        out[s] = {"bid": float(q["bp"]), "ask": float(q["ap"]), "mid": (float(q["bp"]) + float(q["ap"])) / 2}
    return out


def condor_mark(qs, pos, side="mid"):
    """Cost to close the condor per contract (positive = debit to buy it back)."""
    def px(sym, buy):
        q = qs[sym]
        if side == "mid":
            return q["mid"]
        return q["ask"] if buy else q["bid"]
    return (px(pos["put_short"], True) + px(pos["call_short"], True)
            - px(pos["put_long"], False) - px(pos["call_long"], False))


# ── regime ──────────────────────────────────────────────────────────────────
def regime():
    from spyVolRegime import current_scalar
    r = current_scalar()
    q = r.get("gex_quintile")
    inv = bool(r.get("term_inverted") or False)
    return {"gex_q": q, "inverted": inv, "vix": r.get("vix"), "scalar": float(r.get("scalar", 1.0)),
            "eligible": (not inv), "raw": r}   # GEX gate dropped Oct 19 2026 (study 14 holdout)


def entry_context():
    """Forward-validation fields for study 7 cell V2 (post-dip entries with a normal
    term structure earned 3.3 vs 1.5 vol pts in-sample): trailing 5-day SPY return
    and its in-sample tercile edges, logged at every entry so the live split can be
    checked after ~25-50 positions. Never used for decisions."""
    try:
        import csv as _csv
        rets = []
        with open(os.path.expanduser("~/discordBot/outputs/markets/cache/SPY.csv")) as f:
            for row in _csv.DictReader(f):
                try:
                    rets.append(float(row["SPY_ret"]))
                except (ValueError, KeyError):
                    pass
        r5 = 1.0
        for r in rets[-5:]:
            r5 *= (1 + r)
        r5 -= 1
        # in-sample tercile edges of the 5d return, 2016-2026 (vrp_conditions_study)
        terc = "down" if r5 < -0.00226 else ("up" if r5 > 0.01129 else "flat")
        return {"ret5": round(r5, 5), "ret5_tercile": terc}
    except Exception as e:
        return {"ret5": None, "ret5_tercile": None, "err": str(e)[:60]}


# ── construction ────────────────────────────────────────────────────────────
def pick_condor(rows, spot, today):
    exps = sorted({r["exp"] for r in rows if DTE_MIN <= (r["exp"] - today).days <= DTE_MAX})
    if not exps:
        return None, "no expiry in DTE window"
    exp = min(exps, key=lambda e: abs((e - today).days - TARGET_DTE))
    c = [r for r in rows if r["exp"] == exp and r["delta"] is not None]
    puts = [r for r in c if r["type"] == "P" and r["strike"] < spot]
    calls = [r for r in c if r["type"] == "C" and r["strike"] > spot]
    if not puts or not calls:
        return None, "empty side"
    ps = min(puts, key=lambda r: abs(abs(r["delta"]) - SHORT_DELTA))
    cs = min(calls, key=lambda r: abs(abs(r["delta"]) - SHORT_DELTA))
    by = {(r["type"], r["strike"]): r for r in c}
    pl = by.get(("P", ps["strike"] - WING_WIDTH))
    cl = by.get(("C", cs["strike"] + WING_WIDTH))
    if pl is None or cl is None:
        return None, "wing strike missing"
    credit_mid = ps["mid"] + cs["mid"] - pl["mid"] - cl["mid"]
    credit_nat = ps["bid"] + cs["bid"] - pl["ask"] - cl["ask"]
    return {"exp": exp, "dte": (exp - today).days, "put_long": pl, "put_short": ps, "call_short": cs, "call_long": cl,
            "credit_mid": credit_mid, "credit_nat": credit_nat}, None


def place_mleg(h, legs, qty, limit_price, dry):
    payload = {"order_class": "mleg", "qty": str(qty), "type": "limit", "limit_price": f"{limit_price:.2f}",
               "time_in_force": "day", "legs": legs}
    if dry:
        return {"id": "DRY-RUN", "status": "simulated", "payload": payload}
    r = requests.post(f"{PAPER_BASE}/v2/orders", headers=h, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"mleg order rejected {r.status_code}: {r.text[:300]}")
    return r.json()


def order_status(h, oid):
    try:
        return get(f"{PAPER_BASE}/v2/orders/{oid}", h)
    except Exception:
        return {}


# ── book logic ──────────────────────────────────────────────────────────────
def manage_exits(h, st, today, reg, execute):
    msgs = []
    still = []
    for pos in st["positions"]:
        syms = [pos["put_long"], pos["put_short"], pos["call_short"], pos["call_long"]]
        exp = dt.date.fromisoformat(pos["exp"])
        dte = (exp - today).days
        # confirm the opening order actually filled; drop ghosts
        if pos.get("status") == "pending":
            o = order_status(h, pos["order_id"])
            if o.get("status") == "filled":
                pos["status"] = "open"
                pos["credit"] = abs(float(o.get("filled_avg_price") or pos["credit"]))
            elif o.get("status") in ("canceled", "expired", "rejected", "replaced"):
                log({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": "cancelled", "pos_id": pos["id"], "exp": pos["exp"],
                     "reason": o.get("status"), "order_id": pos["order_id"]})
                msgs.append(f"{pos['id']}: opening order {o.get('status')}, dropped")
                continue
            else:
                still.append(pos); continue
        try:
            qs = quotes(h, syms)
            mark = condor_mark(qs, pos)
        except Exception as e:
            msgs.append(f"{pos['id']}: quote fetch failed ({e}); kept")
            still.append(pos); continue
        credit = float(pos["credit"])
        pnl = (credit - mark) * 100 * pos["contracts"]
        reason = None
        if mark <= (1 - TP_FRAC) * credit:
            reason = f"take profit (mark {mark:.2f} <= {(1-TP_FRAC)*credit:.2f})"
        elif mark >= (1 + STOP_MULT) * credit:
            reason = f"stop (mark {mark:.2f} >= {(1+STOP_MULT)*credit:.2f})"
        elif dte <= EXIT_DTE:
            reason = f"time ({dte} dte)"
        if reason is None:
            pos["last_mark"], pos["last_pnl"] = round(mark, 2), round(pnl, 2)
            still.append(pos)
            msgs.append(f"{pos['id']} exp {pos['exp']} ({dte}d) credit {credit:.2f} mark {mark:.2f} pnl {pnl:+.0f}")
            continue
        legs = [{"symbol": pos["put_long"], "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_close"},
                {"symbol": pos["put_short"], "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_close"},
                {"symbol": pos["call_short"], "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_close"},
                {"symbol": pos["call_long"], "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_close"}]
        # closing a short condor is a DEBIT: positive limit, pay up to natural + 1 tick
        nat = condor_mark(qs, pos, side="nat")
        limit = round(max(mark + TICK, min(nat + TICK, mark + 0.10)), 2)
        try:
            o = place_mleg(h, legs, pos["contracts"], limit, dry=not execute)
            ev = "exit" if execute else "exit(dry)"
            log({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": ev, "pos_id": pos["id"], "exp": pos["exp"], "dte": dte,
                 "put_long": pos["put_long"], "put_short": pos["put_short"], "call_short": pos["call_short"], "call_long": pos["call_long"],
                 "contracts": pos["contracts"], "credit": credit, "mark": round(mark, 2), "pnl_usd": round(pnl, 2), "reason": reason,
                 "gex_q": reg["gex_q"], "inverted": reg["inverted"], "vix": reg["vix"], "scalar": reg["scalar"], "order_id": o.get("id")})
            msgs.append(f"{pos['id']}: EXIT {reason}, est pnl {pnl:+.0f}, limit {limit:.2f}, order {o.get('id')}")
            if execute:
                pos.update({"closed_ts": dt.datetime.now().isoformat(timespec="seconds"), "exit_mark": round(mark, 2),
                            "exit_pnl": round(pnl, 2), "exit_reason": reason, "exit_order_id": o.get("id")})
                st["closed"].append(pos)
            else:
                still.append(pos)
        except Exception as e:
            msgs.append(f"{pos['id']}: exit order FAILED {e}; kept")
            still.append(pos)
    st["positions"] = still
    return msgs


def kill_check(st, equity):
    if st["launch_equity"] is None:
        st["launch_equity"] = equity
    closed = st["closed"]
    net = sum(float(p.get("exit_pnl", 0)) for p in closed)
    open_pnl = sum(float(p.get("last_pnl", 0)) for p in st["positions"])
    total = net + open_pnl
    if total <= -KILL_DD_PCT * st["launch_equity"]:
        return f"KILL: book P&L {total:+.0f} <= -{KILL_DD_PCT*100:.0f}% of launch equity {st['launch_equity']:.0f}"
    if len(closed) >= KILL_MIN_CLOSED:
        wr = sum(1 for p in closed if float(p.get("exit_pnl", 0)) > 0) / len(closed)
        if wr < KILL_WIN_RATE and net < 0:
            return f"KILL: {len(closed)} closed, win rate {wr:.0%} < {KILL_WIN_RATE:.0%} and net {net:+.0f} < 0"
    return None


def maybe_enter(h, st, today, reg, equity, execute):
    if os.environ.get("VOLBOOK_FORCE") == "1" and not execute:
        reg = dict(reg, eligible=True)      # dry-run plumbing test only; never with --execute
    if not reg["eligible"]:
        log({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": "skip", "reason": "regime ineligible",
             "gex_q": reg["gex_q"], "inverted": reg["inverted"], "vix": reg["vix"], "scalar": reg["scalar"]})
        return f"no entry: term structure inverted (VIX9D > VIX3M); GEX q{reg['gex_q']} logged only"
    if len(st["positions"]) >= MAX_OPEN:
        return f"no entry: {len(st['positions'])} open (max {MAX_OPEN})"
    if st["last_entry"]:
        gap = (today - dt.date.fromisoformat(st["last_entry"])).days
        if gap < MIN_GAP_DAYS:
            return f"no entry: last entry {gap}d ago (min {MIN_GAP_DAYS})"
    spot = spot_price(h)
    rows = chain(h, today + dt.timedelta(days=DTE_MIN), today + dt.timedelta(days=DTE_MAX))
    c, err = pick_condor(rows, spot, today)
    if c is None:
        return f"no entry: {err}"
    if c["credit_mid"] < MIN_CREDIT_FRAC * WING_WIDTH:
        log({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": "skip", "reason": f"credit {c['credit_mid']:.2f} < {MIN_CREDIT_FRAC*WING_WIDTH:.2f}",
             "spot": spot, "exp": c["exp"], "gex_q": reg["gex_q"], "inverted": reg["inverted"], "vix": reg["vix"]})
        return f"no entry: credit {c['credit_mid']:.2f} below {MIN_CREDIT_FRAC*WING_WIDTH:.2f} (IV too low to pay)"
    limit_credit = round(math.floor((c["credit_mid"] - TICK) / TICK) * TICK, 2)
    max_loss = (WING_WIDTH - limit_credit) * 100
    budget = RISK_PCT * equity   # flat 1% max loss; vol scalar dropped Oct 20 2026 (study 16B: inverse-vol sizing adds exposure before the tail)
    n = max(1, int(budget // max_loss))
    legs = [{"symbol": c["put_long"]["sym"], "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_open"},
            {"symbol": c["put_short"]["sym"], "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": c["call_short"]["sym"], "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": c["call_long"]["sym"], "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_open"}]
    pid = f"IC{today.strftime('%y%m%d')}"
    note = ""
    try:
        o = place_mleg(h, legs, n, st.get("price_sign", -1) * limit_credit, dry=not execute)
    except RuntimeError as e:
        if "limit" in str(e).lower() or "price" in str(e).lower():
            st["price_sign"] = -st.get("price_sign", -1)
            note = f"price sign flipped to {st['price_sign']} after reject: {str(e)[:120]}"
            o = place_mleg(h, legs, n, st["price_sign"] * limit_credit, dry=not execute)
        else:
            raise
    ctx = entry_context()
    pos = {"id": pid, "opened": today.isoformat(), "exp": c["exp"].isoformat(), "contracts": n, "credit": limit_credit,
           "entry_context": ctx,
           "put_long": c["put_long"]["sym"], "put_short": c["put_short"]["sym"], "call_short": c["call_short"]["sym"],
           "call_long": c["call_long"]["sym"], "spot_at_entry": spot, "max_loss_usd": max_loss * n,
           "iv_short_put": c["put_short"]["iv"], "iv_short_call": c["call_short"]["iv"], "order_id": o.get("id"),
           "status": "pending" if execute else "dry", "regime": {k: reg[k] for k in ("gex_q", "inverted", "vix", "scalar")}}
    log({"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": "entry" if execute else "entry(dry)", "pos_id": pid,
         "exp": c["exp"], "dte": c["dte"], "spot": spot, "put_long": c["put_long"]["strike"], "put_short": c["put_short"]["strike"],
         "call_short": c["call_short"]["strike"], "call_long": c["call_long"]["strike"], "contracts": n, "credit": limit_credit,
         "reason": f"deltas {c['put_short']['delta']:+.2f}/{c['call_short']['delta']:+.2f} mid {c['credit_mid']:.2f} nat {c['credit_nat']:.2f}",
         "gex_q": reg["gex_q"], "inverted": reg["inverted"], "vix": reg["vix"], "scalar": reg["scalar"], "order_id": o.get("id"),
         "note": (note + f" ret5={ctx.get('ret5')} ({ctx.get('ret5_tercile')})").strip()})
    if execute:
        st["positions"].append(pos)
        st["last_entry"] = today.isoformat()
    return (f"ENTRY {pid}: {n}x {c['put_long']['strike']:.0f}/{c['put_short']['strike']:.0f}P {c['call_short']['strike']:.0f}/{c['call_long']['strike']:.0f}C "
            f"exp {c['exp']} ({c['dte']}d) credit {limit_credit:.2f} (mid {c['credit_mid']:.2f}) max loss ${max_loss*n:.0f} "
            f"budget ${budget:.0f} spot {spot:.2f} order {o.get('id')} {note}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--no-entry", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--post", default=None, help="Discord channel key to post the summary to (e.g. test-env)")
    a = ap.parse_args()
    h = headers()
    st = load_state()
    today = dt.date.today()
    out = []
    if a.status:
        print(json.dumps(st, indent=2, default=str)); return 0
    acct = get(f"{PAPER_BASE}/v2/account", h)
    equity = float(acct["equity"])
    reg = regime()
    out.append(f"Vol book {today} | GEX q{reg['gex_q']} inverted={reg['inverted']} VIX {reg['vix']} scalar {reg['scalar']:.2f} "
               f"-> {'ELIGIBLE' if reg['eligible'] else 'ineligible'} | open {len(st['positions'])} closed {len(st['closed'])}")
    if st.get("killed"):
        out.append(f"BOOK KILLED: {st['killed']}. Exits only.")
    out += manage_exits(h, st, today, reg, a.execute)
    k = kill_check(st, equity)
    if k and not st.get("killed"):
        st["killed"] = k
        out.append(k)
    if not a.no_entry and not st.get("killed"):
        try:
            out.append(maybe_enter(h, st, today, reg, equity, a.execute))
        except Exception as e:
            out.append(f"entry FAILED: {e}")
    net = sum(float(p.get("exit_pnl", 0)) for p in st["closed"]); op = sum(float(p.get("last_pnl", 0)) for p in st["positions"])
    out.append(f"book P&L: realized {net:+.0f}, open {op:+.0f} (est, mid marks, before fees)")
    if a.execute:
        save_state(st)
    text = "\n".join(out)
    print(text)
    if a.post:
        try:
            from discordPost import post
            post(a.post, "```\n" + text[:1900] + "\n```")
        except Exception as e:
            print(f"post failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
