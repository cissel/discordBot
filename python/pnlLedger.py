#!/usr/bin/env python3
"""
pnlLedger.py
============
Consolidated, account-reconciled P&L ledger across every live strategy on
the shared Alpaca paper account, plus the sportsbook +EV logs.

Why this exists: the SPY autopilot, three market-making bots and three crypto
grid bots all trade ONE paper account. Each writes its own state/fill logs,
and the SPY trade log's "equity" column is account-wide, so none of them can
answer "is THIS strategy making money" on its own. This script rebuilds P&L
per strategy from the broker's own FILL activities (the only complete record
of what executed) rather than from each bot's self-reported logs.

Attribution: symbols are disjoint across strategies, so symbol -> strategy.
Fills before a strategy's launch date (early tests, manual trades, old sweeps
in AAPL/TSLA/etc.) are bucketed as "legacy/manual" and shown separately.

P&L method: signed average-cost per symbol, processed chronologically.
  realized   = closed qty * (exit - avg cost)   (sign-aware; shorts work)
  unrealized = open qty * (mark - avg cost)     (mark = Alpaca position
               current_price, else last daily close)
  Daily mark-to-market series per strategy (realized-to-date + EOD unrealized
  from daily bars) -> max drawdown in $.
Fees: Alpaca paper DOES charge regulatory fees on stock sells (FEE: REG/TAF/
CAT) and crypto pair fees (CFEE, in USD or in-coin). Both are pulled from the
activities API and netted into realized P&L per strategy (attribution rules
in build_books). In-coin fees also reduce book inventory, which is why the
broker position and a naive sum of fills disagree slightly for crypto.

Carry-in: if a symbol already had inventory when its strategy launched (e.g.
16 SPY bought by hand before the autopilot went live), that inventory and its
cost basis are transferred into the strategy's book at launch, because the
strategy manages the whole position from then on. Legacy keeps only the
realized P&L from before launch.

Outputs
  outputs/markets/alpaca_fills_cache.csv      incremental cache of FILL activities
  outputs/markets/pnl_ledger.csv              today's per-strategy table
  outputs/markets/pnl_ledger_daily.csv        per-strategy daily MTM series (for R)
  outputs/markets/pnl_ledger_history.csv      one row per strategy per run (append)
  outputs/markets/pnl_ledger.png              R navy chart (cumulative P&L by strategy)

Usage
  venv/bin/python3 python/pnlLedger.py              # print table, write files, no post
  venv/bin/python3 python/pnlLedger.py --post       # also post table + chart to #markets
  venv/bin/python3 python/pnlLedger.py --post --channel test-env
  venv/bin/python3 python/pnlLedger.py --no-sports  # skip the slow ESPN grading pass
"""
import os
import sys
import re
import csv
import json
import math
import argparse
import datetime as dt
import subprocess
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from marketMaker import load_env, PAPER_BASE  # noqa: E402

BASE = os.path.expanduser("~/discordBot")
OUT = os.path.join(BASE, "outputs/markets")
FILLS_CACHE = os.path.join(OUT, "alpaca_fills_cache.csv")
LEDGER_CSV = os.path.join(OUT, "pnl_ledger.csv")
DAILY_CSV = os.path.join(OUT, "pnl_ledger_daily.csv")
HISTORY_CSV = os.path.join(OUT, "pnl_ledger_history.csv")
PNG = os.path.join(OUT, "pnl_ledger.png")
R_SCRIPT = os.path.join(BASE, "r/pnlLedgerChart.R")
DATA_BASE = "https://data.alpaca.markets"

CACHE_START = "2021-01-01T00:00:00Z"

# symbol -> (strategy label, launch date). Fills before launch -> legacy.
STRATEGIES = {
    "SPY":     ("SPY autopilot", "2026-07-02"),
    "SOXL":    ("MM SOXL",       "2026-09-22"),
    "TQQQ":    ("MM TQQQ (RL)",  "2026-10-15"),
    "GME":     ("MM GME",        "2026-10-15"),
    "BTC/USD": ("Grid BTC",      "2026-10-15"),
    "ETH/USD": ("Grid ETH",      "2026-10-15"),
    "SOL/USD": ("Grid SOL",      "2026-10-15"),
}
LEGACY = "legacy/manual"

# Options (OCC symbols like SPY261120P00700000). The vol book trades SPY iron
# condors (volBook.py, launched Oct 20 2026). Option fills are per-share prices
# on a 100-share multiplier, so prices are scaled x100 into USD-per-contract
# before they hit a Book; quantities stay in contracts.
OPTION_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
VOL_BOOK = ("Vol book (IC)", "2026-10-20")


def _strategy_for(sym):
    """(strategy label, launch date) for a symbol, option-aware."""
    if sym in STRATEGIES:
        return STRATEGIES[sym]
    m = OPTION_RE.match(sym or "")
    if m and m.group(1) == "SPY":
        return VOL_BOOK
    return (LEGACY, None)


def _mult(sym):
    return 100.0 if OPTION_RE.match(sym or "") else 1.0


def _launch_of(strat):
    for name, launch in list(STRATEGIES.values()) + [VOL_BOOK]:
        if name == strat and launch:
            return launch
    return None


# ── Alpaca ──────────────────────────────────────────────────────────────────

def alpaca_headers():
    env = load_env()
    key = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    sec = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
    if not key or not sec:
        raise RuntimeError("APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not set in .env")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def load_fill_cache():
    if not os.path.exists(FILLS_CACHE):
        return []
    with open(FILLS_CACHE) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if not r.get("activity_type"):
            r["activity_type"] = "FILL"
    return rows


CACHE_COLS = ["id", "activity_type", "transaction_time", "symbol", "side", "qty", "price",
              "order_id", "net_amount", "sub_type"]


def save_fill_cache(rows):
    rows = sorted({r["id"]: r for r in rows}.values(), key=lambda r: r["id"])
    with open(FILLS_CACHE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def _normalize(a):
    t = a["activity_type"]
    if t == "FILL":
        return {"id": a["id"], "activity_type": t, "transaction_time": a["transaction_time"],
                "symbol": a["symbol"], "side": a["side"], "qty": a["qty"], "price": a["price"],
                "order_id": a.get("order_id", ""), "net_amount": "", "sub_type": ""}
    # FEE (REG/TAF/CAT on stock sells) and CFEE (crypto, USD or in-coin)
    sym = a.get("symbol", "") or ""
    if sym.endswith("USD") and "/" not in sym:
        sym = sym[:-3] + "/USD"
    return {"id": a["id"], "activity_type": t,
            "transaction_time": a.get("created_at") or a.get("date", "") + "T00:00:00Z",
            "symbol": sym, "side": "fee", "qty": a.get("qty", "") or "",
            "price": a.get("price", "") or "", "order_id": "",
            "net_amount": a.get("net_amount", "0"), "sub_type": a.get("activity_sub_type", "")}


def refresh_fills(headers, max_pages=400):
    """Incremental pull of FILL + FEE + CFEE activities newer than the cache
    watermark (per type). Pages newest-first and stops at the watermark;
    first run walks back to CACHE_START. Alpaca paper DOES charge regulatory
    fees on stock sells and crypto pair fees, so fees are part of real P&L."""
    cached = load_fill_cache()
    new = []
    for kind in ("FILL", "FEE", "CFEE"):
        since_id = max((r["id"] for r in cached if r.get("activity_type", "FILL") == kind), default=None)
        page_token, stop = None, False
        for _ in range(max_pages):
            params = {"direction": "desc", "page_size": 100, "after": CACHE_START}
            if page_token:
                params["page_token"] = page_token
            r = requests.get(f"{PAPER_BASE}/v2/account/activities/{kind}",
                             headers=headers, params=params, timeout=20)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for a in batch:
                if since_id and a["id"] <= since_id:
                    stop = True
                    break
                new.append(_normalize(a))
            if stop or len(batch) < 100:
                break
            page_token = batch[-1]["id"]
    return save_fill_cache(cached + new), len(new)


def get_positions(headers):
    r = requests.get(f"{PAPER_BASE}/v2/positions", headers=headers, timeout=20)
    r.raise_for_status()
    return {p["symbol"].replace("USD", "/USD") if p["symbol"].endswith("USD") and "/" not in p["symbol"]
            and p["symbol"][:-3] in ("BTC", "ETH", "SOL", "DOGE") else p["symbol"]: p for p in r.json()}


def get_account(headers):
    r = requests.get(f"{PAPER_BASE}/v2/account", headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def daily_closes(symbol, start_date, headers):
    """{date: close} from Alpaca data API. Stocks: v2 bars (iex feed). Crypto: v1beta3."""
    out = {}
    try:
        if OPTION_RE.match(symbol):
            r = requests.get(f"{DATA_BASE}/v1beta1/options/bars", headers=headers, timeout=20,
                             params={"symbols": symbol, "timeframe": "1Day",
                                     "start": f"{start_date}T00:00:00Z", "limit": 1000})
            r.raise_for_status()
            bars = r.json().get("bars", {}).get(symbol, []) or []
            for b in bars:
                out[b["t"][:10]] = float(b["c"]) * 100.0
            return out
        if "/" in symbol:
            r = requests.get(f"{DATA_BASE}/v1beta3/crypto/us/bars", headers=headers, timeout=20,
                             params={"symbols": symbol, "timeframe": "1Day",
                                     "start": f"{start_date}T00:00:00Z", "limit": 1000})
            r.raise_for_status()
            bars = r.json().get("bars", {}).get(symbol, [])
        else:
            r = requests.get(f"{DATA_BASE}/v2/stocks/{symbol}/bars", headers=headers, timeout=20,
                             params={"timeframe": "1Day", "start": f"{start_date}T00:00:00Z",
                                     "limit": 1000, "feed": "iex", "adjustment": "raw"})
            r.raise_for_status()
            bars = r.json().get("bars", []) or []
        for b in bars:
            out[b["t"][:10]] = float(b["c"])
    except Exception as e:  # chart degrades gracefully; table still correct
        print(f"[pnlLedger] daily bars failed for {symbol}: {e}", file=sys.stderr)
    return out


# ── P&L engine ──────────────────────────────────────────────────────────────

class Book:
    """Signed average-cost book for one symbol."""
    def __init__(self):
        self.qty = 0.0
        self.avg = 0.0
        self.realized = 0.0
        self.fills = 0
        self.bought = 0.0
        self.sold = 0.0
        self.first = None
        self.last = None
        self.realized_by_day = defaultdict(float)
        self.qty_by_day = {}   # EOD qty / avg snapshots
        self.avg_by_day = {}
        self.fees = 0.0
        self.carried_in = None
        self.reconciled = None
        self.sell_proceeds_by_day = defaultdict(float)
        self.notional_by_day = defaultdict(float)

    def fee(self, ts, usd):
        """Charge a fee (negative usd) against realized P&L."""
        self.fees += usd
        self.realized += usd
        self.realized_by_day[ts[:10]] += usd

    def coin_fee(self, ts, qty, price):
        """Crypto pair fee taken in-coin: reduces inventory, costs qty*price."""
        if self.qty > 0:
            self.qty = max(0.0, self.qty - abs(qty))
        self.qty_by_day[ts[:10]] = self.qty
        self.avg_by_day[ts[:10]] = self.avg
        self.fee(ts, -abs(qty) * price)

    def apply(self, ts, side, q, p):
        day = ts[:10]
        signed = q if side == "buy" else -q
        self.fills += 1
        if side == "buy":
            self.bought += q * p
        else:
            self.sold += q * p
            self.sell_proceeds_by_day[day] += q * p
        self.notional_by_day[day] += q * p
        self.first = self.first or ts
        self.last = ts
        if self.qty == 0 or (self.qty > 0) == (signed > 0):
            # opening / adding in same direction
            tot = self.qty + signed
            self.avg = (self.qty * self.avg + signed * p) / tot if tot != 0 else 0.0
            self.qty = tot
        else:
            # closing (possibly flipping)
            closed = min(abs(signed), abs(self.qty))
            direction = 1.0 if self.qty > 0 else -1.0
            pnl = closed * (p - self.avg) * direction
            self.realized += pnl
            self.realized_by_day[day] += pnl
            remaining = self.qty + signed
            if (remaining > 0) != (self.qty > 0) and remaining != 0:
                self.avg = p          # flipped: new position opened at p
            self.qty = remaining
            if abs(self.qty) < 1e-9:
                self.qty, self.avg = 0.0, 0.0
        self.qty_by_day[day] = self.qty
        self.avg_by_day[day] = self.avg


def build_books(acts):
    """Returns {strategy: {symbol: Book}} with launch-date attribution.
    One continuous book per (strategy, symbol); fills before a strategy's
    launch go to the legacy bucket. Fees are attributed after fills:
      CFEE with symbol (in-coin)  -> that symbol's book
      CFEE USD (no symbol)        -> crypto books by share of notional that day
      FEE REG/TAF (stock sells)   -> stock books by share of sell proceeds that day
      FEE CAT (per-trade)         -> stock books by share of notional that day
    """
    books = defaultdict(dict)
    acts = sorted(acts, key=lambda r: r["id"])
    for f in acts:
        if f.get("activity_type", "FILL") != "FILL":
            continue
        sym = f["symbol"]
        ts = f["transaction_time"]
        strat, launch = _strategy_for(sym)
        if launch and ts[:10] < launch:
            strat = LEGACY
        bk = books[strat].get(sym)
        if bk is None:
            bk = books[strat][sym] = Book()
            leg = books.get(LEGACY, {}).get(sym)
            if strat != LEGACY and leg is not None and abs(leg.qty) > 1e-9:
                # carry pre-launch inventory + cost basis into the strategy book
                bk.qty, bk.avg = leg.qty, leg.avg
                bk.carried_in = f"{leg.qty:.4g} @ {leg.avg:.2f}"
                bk.qty_by_day[launch] = bk.qty
                bk.avg_by_day[launch] = bk.avg
                leg.qty, leg.avg = 0.0, 0.0
                leg.qty_by_day[launch] = 0.0
        bk.apply(ts, f["side"], float(f["qty"]), float(f["price"]) * _mult(sym))

    all_books = [(s, sym, b) for s, d in books.items() for sym, b in d.items()]
    for f in acts:
        t = f.get("activity_type", "FILL")
        if t == "FILL":
            continue
        ts = f["transaction_time"]
        day = ts[:10]
        usd = float(f.get("net_amount") or 0.0)
        sym = f.get("symbol") or ""
        if t == "CFEE" and sym:
            q = float(f.get("qty") or 0.0)
            p = float(f.get("price") or 0.0)
            tgt = _owner(books, sym, day)
            if tgt is not None:
                tgt.coin_fee(ts, q, p)
            continue
        if t == "CFEE":
            pool = [(b, b.notional_by_day.get(day, 0.0)) for s, sm, b in all_books if "/" in sm]
        elif f.get("sub_type") == "CAT":
            pool = [(b, b.notional_by_day.get(day, 0.0)) for s, sm, b in all_books if "/" not in sm]
        else:  # REG / TAF
            pool = [(b, b.sell_proceeds_by_day.get(day, 0.0)) for s, sm, b in all_books if "/" not in sm]
        tot = sum(w for _, w in pool)
        if tot <= 0 or usd == 0:
            # no activity that day to attribute to: park it on legacy
            books[LEGACY].setdefault("_fees", Book()).fee(ts, usd)
            continue
        for b, w in pool:
            if w > 0:
                b.fee(ts, usd * w / tot)
    return books


def reconcile_to_broker(books, positions):
    """Alpaca's activities history for this account only reaches back to late
    May 2026, so any inventory that predates it (plus in-coin fee rounding)
    makes the rebuilt book disagree with the broker's position. The broker is
    the source of truth for QUANTITY: shift the live book (and its daily
    snapshots) by the difference and keep the book's cost basis. Realized P&L
    is unaffected; only unrealized moves, and the drift is reported."""
    for strat, syms in books.items():
        if strat == LEGACY:
            continue
        for sym, bk in syms.items():
            pos = positions.get(sym) or positions.get(sym.replace("/", ""))
            pq = float(pos["qty"]) * (-1 if pos and pos.get("side") == "short" else 1) if pos else 0.0
            delta = pq - bk.qty
            if abs(delta) > max(1e-9, 0.005 * max(abs(pq), abs(bk.qty))):
                bk.reconciled = delta
                bk.qty = pq
                for d in bk.qty_by_day:
                    bk.qty_by_day[d] += delta
                if not bk.qty_by_day:
                    bk.qty_by_day[bk.first[:10]] = pq
                    bk.avg_by_day[bk.first[:10]] = bk.avg


def _owner(books, sym, day):
    """Book that owned `sym` on `day`: the live strategy if day >= launch, else legacy."""
    strat, launch = _strategy_for(sym)
    if launch and day < launch:
        strat = LEGACY
    return books.get(strat, {}).get(sym)


def daterange(d0, d1):
    d = dt.date.fromisoformat(d0)
    end = dt.date.fromisoformat(d1)
    while d <= end:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def mtm_series(books_for_strat, closes_by_sym, start, end):
    """Daily cumulative P&L (realized to date + EOD unrealized) for a strategy."""
    series = []
    cum_real = 0.0
    state = {s: (0.0, 0.0) for s in books_for_strat}
    for day in daterange(start, end):
        unreal = 0.0
        for sym, bk in books_for_strat.items():
            cum_real += bk.realized_by_day.get(day, 0.0)
            if day in bk.qty_by_day:
                state[sym] = (bk.qty_by_day[day], bk.avg_by_day[day])
            q, a = state[sym]
            close = _close_on_or_before(closes_by_sym.get(sym, {}), day)
            if q and close is not None:
                unreal += q * (close - a)
        series.append((day, cum_real, unreal, cum_real + unreal))
    return series


def _close_on_or_before(closes, day):
    if not closes:
        return None
    if day in closes:
        return closes[day]
    prior = [d for d in closes if d <= day]
    return closes[max(prior)] if prior else None


def max_drawdown(values):
    peak, mdd = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


# ── sports EV rows ──────────────────────────────────────────────────────────

def sports_rows():
    rows = []
    try:
        import totalBetsSummary as tbs
        import clvTracking
        for label, fn in (("Bets NFL (+EV, $1 flat)", tbs.nfl_totals),
                          ("Bets MLB (+EV, $1 flat)", tbs.mlb_totals)):
            graded = fn()[0]
            wins = sum(1 for g in graded if g["win"])
            net = sum(g["profit"] for g in graded)
            clv_list = [g["clv"] for g in graded if g.get("clv") is not None]
            n_clv, avg_clv, pct_beat, _ = clvTracking.summarize_clv(clv_list)
            rows.append({"strategy": label, "record": f"{wins}-{len(graded) - wins}",
                         "n": len(graded), "net": net,
                         "clv": avg_clv, "clv_beat": pct_beat, "clv_n": n_clv})
    except Exception as e:
        print(f"[pnlLedger] sports rows failed: {e}", file=sys.stderr)
    return rows


# ── formatting ──────────────────────────────────────────────────────────────

def money(x, w=9):
    if abs(x) < 0.005:
        return "0.00".rjust(w)
    s = f"{x:+,.0f}" if abs(x) >= 100 else f"{x:+,.2f}"
    return s.rjust(w)


def status_for(total, mdd, days, fills):
    if fills == 0:
        return "idle"
    if days < 10:
        return "warmup"
    if total > 0 and total > -mdd * 0.5:
        return "green"
    if total > 0:
        return "amber"
    return "red"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--post", action="store_true", help="post table + chart to Discord")
    ap.add_argument("--channel", default="test-env")
    ap.add_argument("--no-sports", action="store_true")
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args(argv)

    headers = alpaca_headers()
    fills, n_new = refresh_fills(headers)
    positions = get_positions(headers)
    account = get_account(headers)
    today = dt.date.today().isoformat()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    books = build_books(fills)
    reconcile_to_broker(books, positions)
    order = [s for s, _ in STRATEGIES.values()] + [VOL_BOOK[0]]
    order = list(dict.fromkeys(order)) + [LEGACY]

    table, daily_rows = [], []
    for strat in order:
        syms = books.get(strat, {})
        if not syms:
            continue
        launch = _launch_of(strat)
        start = launch or min(b.first for b in syms.values() if b.first)[:10]
        closes = {s: daily_closes(s, start, headers) for s in syms} if strat != LEGACY else {}
        realized = sum(b.realized for b in syms.values())
        unreal, inv = 0.0, []
        for s, b in syms.items():
            if abs(b.qty) > 1e-9:
                # mark at the latest daily close so table and chart agree; the
                # broker's position.current_price has been seen stale/odd off-hours
                mark = _close_on_or_before(closes.get(s, {}), today)
                if mark is None:
                    pos = positions.get(s) or positions.get(s.replace("/", ""))
                    mark = float(pos["current_price"]) * _mult(s) if pos else None
                if mark is not None:
                    unreal += b.qty * (mark - b.avg)
                inv.append(f"{b.qty:.4g} {s}" + (f" (incl. {b.carried_in} carried in)" if b.carried_in and not b.reconciled else ""))
        fills_n = sum(b.fills for b in syms.values())
        traded = sum(b.bought + b.sold for b in syms.values())
        real_books = [b for b in syms.values() if b.first]
        first = min(b.first for b in real_books)[:10]
        last = max(b.last for b in real_books)[:10]
        days = max(1, (dt.date.fromisoformat(today) - dt.date.fromisoformat(first)).days + 1)
        total = realized + unreal
        mdd = 0.0
        if strat != LEGACY:
            ser = mtm_series(syms, closes, start, today)
            mdd = max_drawdown([v[3] for v in ser])
            for day, cr, ur, tot in ser:
                daily_rows.append({"date": day, "strategy": strat, "realized": round(cr, 2),
                                   "unrealized": round(ur, 2), "total": round(tot, 2)})
        fees = sum(b.fees for b in syms.values())
        drift = [f"{s} {b.reconciled:+.4g} (pre-history/fee rounding)"
                 for s, b in syms.items() if b.reconciled]
        table.append({
            "strategy": strat, "first_fill": first, "last_fill": last, "days": days,
            "fills": fills_n, "fills_per_day": fills_n / days, "traded_notional": traded,
            "realized": realized, "unrealized": unreal, "total": total,
            "fees": fees, "max_dd": mdd, "position_drift": "; ".join(drift),
            "per_fill": total / fills_n if fills_n else 0.0,
            "inventory": "; ".join(inv) or "flat",
            "status": status_for(total, mdd, days, fills_n),
        })

    sports = [] if args.no_sports else sports_rows()

    # ── write files ──
    with open(LEDGER_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        w.writeheader(); w.writerows(table)
    with open(DAILY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "strategy", "realized", "unrealized", "total"])
        w.writeheader(); w.writerows(daily_rows)
    hist_new = not os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        cols = ["run_at"] + list(table[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        if hist_new:
            w.writeheader()
        for row in table:
            w.writerow({"run_at": now, **row})

    # ── chart ──
    chart_ok = False
    if not args.no_chart and os.path.exists(R_SCRIPT):
        try:
            subprocess.run(["Rscript", R_SCRIPT], check=True, timeout=120,
                           capture_output=True, text=True)
            chart_ok = os.path.exists(PNG)
        except Exception as e:
            print(f"[pnlLedger] chart failed: {getattr(e, 'stderr', e)}", file=sys.stderr)

    # ── discord text ──
    live = [t for t in table if t["strategy"] != LEGACY]
    legacy = next((t for t in table if t["strategy"] == LEGACY), None)
    tot_live = sum(t["total"] for t in live)
    lines = [f"**Strategy P&L ledger** - {now} ET  (paper, account-reconciled from Alpaca fills)",
             "```",
             f"{'strategy':<15}{'days':>5}{'fills':>7}{'realized':>10}{'fees':>7}{'unreal':>9}{'total':>9}{'maxDD':>9}{'$/fill':>8}  inventory",
             "-" * 100]
    for t in live:
        lines.append(f"{t['strategy']:<15}{t['days']:>5}{t['fills']:>7}{money(t['realized'],10)}"
                     f"{money(t['fees'],7)}{money(t['unrealized'])}{money(t['total'])}{money(t['max_dd'])}"
                     f"{t['per_fill']:>8.3f}  {t['inventory']}")
    lines.append("-" * 100)
    lines.append(f"{'LIVE TOTAL':<15}{'':>5}{sum(t['fills'] for t in live):>7}"
                 f"{money(sum(t['realized'] for t in live),10)}{money(sum(t['fees'] for t in live),7)}"
                 f"{money(sum(t['unrealized'] for t in live))}{money(tot_live)}")
    if legacy:
        lines.append(f"{'legacy/manual':<15}{legacy['days']:>5}{legacy['fills']:>7}{money(legacy['realized'],10)}"
                     f"{money(legacy['fees'],7)}{money(legacy['unrealized'])}{money(legacy['total'])}   pre-launch / non-strategy fills")
    drifts = [t["position_drift"] for t in live if t["position_drift"]]
    if drifts:
        lines.append("inventory reconciled to broker: " + " | ".join(drifts))
    lines.append(f"account equity {float(account['equity']):,.0f}  |  realized is net of fees (REG/TAF/CAT, crypto pair fees)  |  {n_new} new activities ingested")
    lines.append("```")
    if sports:
        lines.append("```")
        for s in sports:
            clv = f"  CLV {s['clv']*100:+.2f}pp ({s['clv_beat']*100:.0f}% beat close, n={s['clv_n']})" if s.get("clv") is not None else ""
            lines.append(f"{s['strategy']:<24} {s['record']:>8}  net {s['net']:+.2f}{clv}")
        lines.append("```")
    text = "\n".join(lines)
    print(text)

    if args.post:
        from discordPost import post
        post(args.channel, text, files=[PNG] if chart_ok else None)
        print(f"[pnlLedger] posted to #{args.channel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())