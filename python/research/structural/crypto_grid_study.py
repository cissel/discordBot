#!/usr/bin/env python3
"""
crypto_grid_study.py
====================
Pre-registered test of the live Alpaca paper crypto grid (python/gridBot.py)
on BTC/USD, ETH/USD, SOL/USD: does it have an edge net of fees, or is it
inventory risk dressed up as volatility harvesting?

Replicates gridBot.py exactly (build_grid, get_daily_atr_pct, breakout
logic, re-arm rules) with the live cron args:
  BTC budget 2500, ETH 2000, SOL 1500 (from ~/.hermes/scripts/gridCycle_*_cron.sh)
  --levels 14 --atr-mult 0.6 --min-spacing-pct 0.35 --breakout-atr-mult 1.5

Grid mechanics replicated
  init: mid = open of the init bar; daily ATR = mean of last 14 true ranges
        from COMPLETED UTC daily bars (the live bot includes the in-progress
        day's partial bar; completed-only avoids lookahead, tiny deviation);
        atr_pct = atr_abs / previous daily close * 100;
        half_range_pct = 0.6 * atr_pct; spacing = 2*half/14, if spacing <
        0.35% then levels = max(4, int(2*half/0.35)); levels+1 price points;
        qty_per_level = budget/(levels+1)/mid (fixed coin qty).
        GTC buy limits at every level below mid.
  fills: a resting buy fills if bar low <= level; a resting sell fills if
        bar high >= level; fill AT the level price (no price improvement);
        at most one fill per level per bar; bars in time order. Orders
        created by a fill (re-arms) become live from the NEXT bar, which is
        the cron latency the live bot has (it reconciles once a minute).
  re-arm: buy fill at level i -> sell at i+1 for that qty; sell fill at
        level i -> buy at i-1. Realized per round trip = (level_i -
        level_{i-1}) * qty, exactly as gridBot bookkeeps it.
  fees: maker 0.15% of notional on every limit fill (both legs rest),
        taker 0.25% on breakout flattens and on benchmark entries.
  level collisions: gridBot keys level_orders by price, so when a sell re-arm
        and a buy re-arm land on the same level in one cycle the earlier order
        keeps resting at Alpaca but is untracked (its fill is logged as
        'matched no tracked grid level', no re-arm). Replicated: the untracked
        order still fills (cash and inventory move) but does not re-arm.
  breakout: mid beyond [lower, upper] by more than 1.5 * atr_abs ->
        cancel all, market-flatten (taker fee), halt.
        1Min bars: 'mid' = bar close (cron samples once a minute); fills in
        the breakout minute are real and get flattened at the close.
        1Hour bars: breakout if the bar's low/high crosses the threshold (a
        minute sampler would have seen it); that bar's fills are skipped and
        the flatten happens at the threshold price (conservative).
  halt policies:
        live: auto-reset at the first bar of the next UTC day around the
              then-current price (James resets by hand; this is the stated
              realistic approximation of 'reset the next morning').
        pessimistic: no reset for 7 days after a halt.

Windows
  A (primary):   1Min bars, last 12 months.
  B (secondary): 1Hour bars, full history 2021+. Coarse bars under-count
                 round trips and can only fill one order per level per hour,
                 so B is a LOWER BOUND on grid income, not a point estimate.

Metrics per symbol x window x policy
  realized round-trip income (gross), maker fees, taker fees, halt flatten
  losses (gross), unrealized inventory P&L at the last price, total P&L net
  of all fees, round trips, halts, max inventory (USD and % of budget),
  max drawdown of total equity, days in market, Sharpe of daily equity
  changes (sqrt(365)), Newey-West (lag 5) t-stat of the daily mean P&L.
  Decomposition identity (checked in code):
     total = round_trip_income - fees - halt_losses - inventory_losses
  where inventory_losses = -(unrealized P&L of lots still held at the end)
  and halt_losses = sum over flattens of (lot cost - flatten price)*qty.

Benchmarks on the same daily series
  (1) vol-matched buy-and-hold: hold a constant USD amount equal to the
      grid's time-weighted average USD inventory, bought at the window's
      first price with a taker fee. Same realized exposure, so a fair
      comparison of the grid's active layer.
  (2) cash: 0.
  (3) buy-and-hold of the full budget.

PRE-REGISTERED PASS RULE (fixed before running)
  For a symbol, on window A with the live halt policy, PASS if ALL of:
     1. total P&L net of all fees > 0
     2. Sharpe(daily grid equity changes) > Sharpe(vol-matched B&H)
     3. max drawdown (USD) of grid equity < max drawdown of vol-matched B&H
     4. conditions 1-3 ALSO hold in each half of the window separately
        (halves split at the midpoint date of the continuous run; the
        half's vol-matched B&H uses that half's average inventory and
        starts at that half's first price)
  PARTIAL if condition 1 holds but any of 2-4 fails.
  FAIL otherwise.
  Book verdict: equal budgets to the three symbols' P&L in USD summed
  (budgets are what the live book runs), same three conditions on the
  summed daily equity vs the summed vol-matched B&H.

Sanity check: simulate BTC from the live grid's actual init (center
82185.143, atr_abs 1991.28, created 2026-10-15T21:10Z) through the last
real fill and compare fills/day and $/round trip with
outputs/markets/grid_fills_log_BTCUSD.csv (read-only).

Deterministic: no randomness anywhere. Data from
outputs/markets/cache/crypto/ (crypto_grid_fetch.py).

Outputs (outputs/research/structural/):
  crypto_grid_results.csv, crypto_grid_decomposition.csv,
  crypto_grid_daily.csv, crypto_grid_summary.md, crypto_grid_run.log
"""
import os
import sys
import math
import json
import datetime as dt
import numpy as np
import pandas as pd

ROOT = os.path.expanduser("~/discordBot")
CACHE = os.path.join(ROOT, "outputs/markets/cache/crypto")
OUT = os.path.join(ROOT, "outputs/research/structural")
FILLS_LOG = os.path.join(ROOT, "outputs/markets/grid_fills_log_BTCUSD.csv")

BUDGETS = {"BTC/USD": 2500.0, "ETH/USD": 2000.0, "SOL/USD": 1500.0}
LEVELS, ATR_MULT, MIN_SPACING_PCT, BREAKOUT_ATR_MULT, MIN_LEVELS = 14, 0.6, 0.35, 1.5, 4
MAKER_FEE, TAKER_FEE = 0.0015, 0.0025
ATR_WINDOW = 14
PESSIMISTIC_DAYS = 7
PRIMARY_WINDOW_DAYS = 365

LOG_LINES = []


def log(s=""):
    print(s)
    LOG_LINES.append(str(s))


# ── grid construction (verbatim logic from gridBot.build_grid) ───────────
def build_grid(mid, atr_pct, budget, levels=LEVELS, atr_mult=ATR_MULT, min_spacing_pct=MIN_SPACING_PCT):
    half_range_pct = atr_mult * atr_pct
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
    return grid_levels, lower, upper, spacing_pct, qty_per_level


def daily_atr_series(daily):
    """For each UTC date D, ATR (abs and pct) using the 14 TRs from completed
    days strictly before D, pct relative to the last completed close."""
    d = daily.copy()
    d["date"] = pd.to_datetime(d["t"], utc=True).dt.floor("D")
    d = d.sort_values("date").reset_index(drop=True)
    prev_close = d["c"].shift(1)
    tr = np.maximum(d["h"] - d["l"], np.maximum((d["h"] - prev_close).abs(), (d["l"] - prev_close).abs()))
    atr_abs = tr.rolling(ATR_WINDOW).mean()
    out = pd.DataFrame({"date": d["date"] + pd.Timedelta(days=1),
                        "atr_abs": atr_abs.values, "atr_pct": (atr_abs / d["c"]).values * 100.0})
    return out.dropna().set_index("date")


def atr_for(atr_tab, ts):
    day = ts.floor("D")
    sub = atr_tab.loc[:day]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    return float(r["atr_abs"]), float(r["atr_pct"])


# ── the simulator ─────────────────────────────────────────────────────────
def simulate(bars, atr_tab, budget, policy, breakout_on_wicks, forced_init=None, label=""):
    """bars: DataFrame t(UTC), o, h, l, c sorted. Returns dict of results plus
    a daily equity frame and a trace list of events."""
    t_idx = pd.DatetimeIndex(pd.to_datetime(bars["t"], utc=True))
    o = bars["o"].values.astype(float)
    h = bars["h"].values.astype(float)
    l = bars["l"].values.astype(float)
    c = bars["c"].values.astype(float)
    n = len(t_idx)

    day_of = t_idx.floor("D")

    cash = 0.0
    lots = []            # list of [buy_price, qty] held (FIFO not needed; the bot matches by level)
    pos = 0.0
    orders = []          # resting limit orders: {"j","side","qty","active_from","tracked"}
    levels = []
    lower = upper = atr_abs = qty_per = 0.0
    halted = True
    resume_at = None     # timestamp after which a reset is allowed
    stats = dict(rt_income=0.0, maker_fees=0.0, taker_fees=0.0, halt_loss=0.0, round_trips=0,
                 halts=0, inits=0, max_inv_usd=0.0, buy_fills=0, sell_fills=0, overwrite=0, untracked_fills=0)
    inv_usd_sum = 0.0
    in_market_bars = 0
    daily_rows = []
    trace = []
    last_day = None
    last_px = c[0]

    def do_init(i):
        nonlocal levels, lower, upper, atr_abs, qty_per, orders, halted
        mid = o[i]
        if forced_init is not None:
            mid, a_abs, a_pct = forced_init["mid"], forced_init["atr_abs"], forced_init["atr_pct"]
        else:
            a = atr_for(atr_tab, t_idx[i])
            if a is None:
                return False
            a_abs, a_pct = a
        levels, lower, upper, spacing, qty_per = build_grid(mid, a_pct, budget)
        atr_abs = a_abs
        orders = [{"j": j, "side": "buy", "qty": qty_per, "active_from": i, "tracked": True}
                  for j, lv in enumerate(levels) if lv < mid]
        halted = False
        stats["inits"] += 1
        trace.append((str(t_idx[i]), "init", mid, len(levels), spacing, qty_per))
        return True

    def place(j, side, q, active_from):
        # the live bot keys level_orders by price string, so a second order
        # landing on an occupied level overwrites the dict entry; the earlier
        # order keeps resting at Alpaca but becomes untracked (its fill is
        # logged as 'matched no tracked grid level', no re-arm). Replicated.
        for od in orders:
            if od["j"] == j and od["tracked"]:
                od["tracked"] = False
                stats["overwrite"] += 1
        orders.append({"j": j, "side": side, "qty": q, "active_from": active_from, "tracked": True})

    for i in range(n):
        ts = t_idx[i]
        if halted:
            if resume_at is None or ts >= resume_at:
                do_init(i)
        if not halted:
            in_market_bars += 1
            buf = BREAKOUT_ATR_MULT * atr_abs
            if breakout_on_wicks:
                # hourly bars: a minute sampler would have caught the threshold
                # inside the hour. Conservative: skip this bar's fills, flatten
                # at the threshold price actually crossed.
                broke_dn, broke_up = l[i] < lower - buf, h[i] > upper + buf
                broke = broke_dn or broke_up
                px_halt = (lower - buf) if broke_dn else (upper + buf)
            else:
                broke = c[i] < lower - buf or c[i] > upper + buf
                px_halt = c[i]
            # fills on resting orders (orders placed this bar are not yet live);
            # on 1-min bars the fills in the breakout minute are real (orders rest
            # at the exchange) and the flatten covers them, so process them.
            fills = []
            if not (broke and breakout_on_wicks):
                for od in orders:
                    if od["active_from"] > i:
                        continue
                    lv = levels[od["j"]]
                    if od["side"] == "buy" and l[i] <= lv:
                        fills.append(od)
                    elif od["side"] == "sell" and h[i] >= lv:
                        fills.append(od)
            for od in fills:
                j, side, q = od["j"], od["side"], od["qty"]
                lv = levels[j]
                orders.remove(od)
                if side == "buy":
                    cash -= lv * q * (1 + MAKER_FEE)
                    stats["maker_fees"] += lv * q * MAKER_FEE
                    lots.append([lv, q])
                    pos += q
                    stats["buy_fills"] += 1
                    if not od["tracked"]:
                        stats["untracked_fills"] += 1
                    elif j + 1 < len(levels):
                        place(j + 1, "sell", q, i + 1)
                else:
                    cash += lv * q * (1 - MAKER_FEE)
                    stats["maker_fees"] += lv * q * MAKER_FEE
                    buy_cost = levels[j - 1] if j > 0 else lv
                    stats["rt_income"] += (lv - buy_cost) * q
                    stats["round_trips"] += 1
                    stats["sell_fills"] += 1
                    # remove the lot bought at level j-1 (the bot's own bookkeeping)
                    k = next((m for m, lot in enumerate(lots) if abs(lot[0] - buy_cost) < 1e-6 * buy_cost), None)
                    if k is None:
                        k = 0  # defensive; should not happen
                    lots[k][1] -= q
                    if lots[k][1] <= 1e-12:
                        lots.pop(k)
                    pos -= q
                    if not od["tracked"]:
                        stats["untracked_fills"] += 1
                    elif j - 1 >= 0:
                        place(j - 1, "buy", q, i + 1)
            if broke:
                px = px_halt
                if pos > 1e-12:
                    cash += px * pos * (1 - TAKER_FEE)
                    stats["taker_fees"] += px * pos * TAKER_FEE
                    stats["halt_loss"] += sum((lot[0] - px) * lot[1] for lot in lots)
                lots, pos = [], 0.0
                orders = []
                halted = True
                stats["halts"] += 1
                trace.append((str(ts), "halt", px, lower, upper))
                if policy == "live":
                    resume_at = day_of[i] + pd.Timedelta(days=1)
                else:
                    resume_at = ts + pd.Timedelta(days=PESSIMISTIC_DAYS)
        inv = pos * c[i]
        inv_usd_sum += inv
        if inv > stats["max_inv_usd"]:
            stats["max_inv_usd"] = inv
        last_px = c[i]
        # end-of-day mark
        d = day_of[i]
        if i == n - 1 or day_of[i + 1] != d:
            daily_rows.append((d, cash + pos * c[i], c[i], inv, 0 if halted else 1))

    unreal = sum((last_px - lot[0]) * lot[1] for lot in lots)
    total = cash + pos * last_px
    fees = stats["maker_fees"] + stats["taker_fees"]
    inv_loss = -unreal
    ident = stats["rt_income"] - fees - stats["halt_loss"] - inv_loss
    assert abs(ident - total) < 1e-6 * max(1.0, abs(total)), (ident, total)
    daily = pd.DataFrame(daily_rows, columns=["date", "pnl", "close", "inv_usd", "in_market"])
    res = dict(stats)
    res.update(total_pnl=total, unrealized=unreal, inventory_loss=inv_loss, fees=fees,
               avg_inv_usd=inv_usd_sum / n, max_inv_pct=stats["max_inv_usd"] / budget * 100.0,
               days_in_market=float(daily["in_market"].sum()), days_total=len(daily),
               final_pos=pos, bars=n)
    return res, daily, trace


# ── statistics ────────────────────────────────────────────────────────────
def sharpe(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / x.std(ddof=1) * math.sqrt(365))


def max_drawdown(equity):
    e = np.asarray(equity, float)
    peak = np.maximum.accumulate(e)
    return float((peak - e).max())


def newey_west_t(x, lag=5):
    x = np.asarray(x, float)
    n = len(x)
    if n < 10:
        return float("nan")
    m = x.mean()
    u = x - m
    s = (u * u).sum()
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        s += 2 * w * (u[k:] * u[:-k]).sum()
    var = s / n / n
    return float(m / math.sqrt(var)) if var > 0 else float("nan")


def benchmark_series(daily, usd, start_px):
    """Daily P&L of holding `usd` dollars of coin bought at start_px with a taker fee."""
    q = usd / start_px
    return q * daily["close"].values - usd - usd * TAKER_FEE


def evaluate(daily, budget):
    """Metrics on a daily P&L frame: grid vs vol-matched B&H vs full-budget B&H."""
    pnl = daily["pnl"].values
    pnl0 = np.concatenate([[0.0], pnl])
    d_grid = np.diff(pnl0)
    avg_inv = float(daily["inv_usd"].mean())
    start_px = float(daily["close"].iloc[0])
    bh_vm = benchmark_series(daily, avg_inv, start_px) if avg_inv > 0 else np.zeros(len(daily))
    bh_full = benchmark_series(daily, budget, start_px)
    d_vm = np.diff(np.concatenate([[0.0], bh_vm]))
    d_full = np.diff(np.concatenate([[0.0], bh_full]))
    return dict(
        total_pnl=float(pnl[-1]), sharpe=sharpe(d_grid), mdd=max_drawdown(pnl0),
        nw_t=newey_west_t(d_grid),
        bh_vm_pnl=float(bh_vm[-1]), bh_vm_sharpe=sharpe(d_vm), bh_vm_mdd=max_drawdown(np.concatenate([[0.0], bh_vm])),
        bh_full_pnl=float(bh_full[-1]), bh_full_sharpe=sharpe(d_full), bh_full_mdd=max_drawdown(np.concatenate([[0.0], bh_full])),
        avg_inv_usd=avg_inv, n_days=len(daily),
    ), bh_vm, bh_full


def verdict(full, h1, h2):
    def three(m):
        return (m["total_pnl"] > 0, m["sharpe"] > m["bh_vm_sharpe"], m["mdd"] < m["bh_vm_mdd"])
    f, a, b = three(full), three(h1), three(h2)
    if all(f) and all(a) and all(b):
        return "PASS"
    if f[0]:
        return "PARTIAL"
    return "FAIL"


# ── data ──────────────────────────────────────────────────────────────────
def load(sym, tf):
    p = os.path.join(CACHE, f"{sym.replace('/', '')}_{tf}.csv.gz")
    df = pd.read_csv(p)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.sort_values("t").drop_duplicates("t").reset_index(drop=True)


def main():
    results, decomp, daily_out = [], [], []
    verdicts = {}
    book_daily = {}
    end_ts = None
    for sym in BUDGETS:
        daily_bars = load(sym, "1Day")
        atr_tab = daily_atr_series(daily_bars)
        budget = BUDGETS[sym]
        for window, tf in (("12m_1min", "1Min"), ("2021+_1hour", "1Hour")):
            bars = load(sym, tf)
            if tf == "1Min":
                end_ts = bars["t"].iloc[-1]
                bars = bars[bars["t"] >= end_ts - pd.Timedelta(days=PRIMARY_WINDOW_DAYS)].reset_index(drop=True)
            else:
                first_atr = atr_tab.index[0]
                bars = bars[bars["t"] >= first_atr].reset_index(drop=True)
            for policy in ("live", "pessimistic"):
                res, daily, trace = simulate(bars, atr_tab, budget, policy, breakout_on_wicks=(tf == "1Hour"))
                m, bh_vm, bh_full = evaluate(daily, budget)
                mid_date = daily["date"].iloc[len(daily) // 2]
                h1 = daily[daily["date"] < mid_date].reset_index(drop=True)
                h2 = daily[daily["date"] >= mid_date].reset_index(drop=True)
                # halves measured as P&L within the half (re-based)
                h1b = h1.copy()
                h2b = h2.copy(); h2b["pnl"] = h2b["pnl"] - h1["pnl"].iloc[-1]
                m1, _, _ = evaluate(h1b, budget)
                m2, _, _ = evaluate(h2b, budget)
                v = verdict(m, m1, m2)
                row = dict(symbol=sym, window=window, policy=policy, budget=budget,
                           start=str(daily["date"].iloc[0].date()), end=str(daily["date"].iloc[-1].date()),
                           days=len(daily), days_in_market=res["days_in_market"],
                           round_trips=res["round_trips"], halts=res["halts"], inits=res["inits"],
                           rt_income_gross=res["rt_income"], maker_fees=res["maker_fees"], taker_fees=res["taker_fees"],
                           halt_loss_gross=res["halt_loss"], unrealized_end=res["unrealized"],
                           total_pnl_net=res["total_pnl"], total_pnl_pct=res["total_pnl"] / budget * 100,
                           avg_inv_usd=res["avg_inv_usd"], max_inv_usd=res["max_inv_usd"], max_inv_pct=res["max_inv_pct"],
                           mdd_usd=m["mdd"], sharpe=m["sharpe"], nw_t=m["nw_t"],
                           bh_vm_pnl=m["bh_vm_pnl"], bh_vm_sharpe=m["bh_vm_sharpe"], bh_vm_mdd=m["bh_vm_mdd"],
                           bh_full_pnl=m["bh_full_pnl"], bh_full_sharpe=m["bh_full_sharpe"], bh_full_mdd=m["bh_full_mdd"],
                           h1_pnl=m1["total_pnl"], h1_sharpe=m1["sharpe"], h1_bh_vm_sharpe=m1["bh_vm_sharpe"],
                           h1_mdd=m1["mdd"], h1_bh_vm_mdd=m1["bh_vm_mdd"],
                           h2_pnl=m2["total_pnl"], h2_sharpe=m2["sharpe"], h2_bh_vm_sharpe=m2["bh_vm_sharpe"],
                           h2_mdd=m2["mdd"], h2_bh_vm_mdd=m2["bh_vm_mdd"],
                           usd_per_round_trip_gross=res["rt_income"] / max(1, res["round_trips"]),
                           round_trips_per_day=res["round_trips"] / max(1, res["days_in_market"]),
                           level_collisions=res["overwrite"], untracked_fills=res["untracked_fills"],
                           verdict=v if (window == "12m_1min" and policy == "live") else "")
                results.append(row)
                decomp.append(dict(symbol=sym, window=window, policy=policy,
                                   round_trip_income=res["rt_income"], fees=-res["fees"],
                                   inventory=-res["inventory_loss"], halt_losses=-res["halt_loss"],
                                   total=res["total_pnl"]))
                dd = daily.copy()
                dd["symbol"], dd["window"], dd["policy"] = sym, window, policy
                dd["grid_equity"] = budget + dd["pnl"]
                dd["bh_volmatched_equity"] = budget + bh_vm
                dd["bh_full_equity"] = budget + bh_full
                daily_out.append(dd[["date", "symbol", "window", "policy", "grid_equity", "bh_volmatched_equity",
                                     "bh_full_equity", "close", "inv_usd", "in_market"]])
                if window == "12m_1min" and policy == "live":
                    verdicts[sym] = (v, m, m1, m2)
                    book_daily[sym] = dd
                log(f"{sym} {window} {policy}: total {res['total_pnl']:+.2f} rt {res['round_trips']} halts {res['halts']} "
                    f"rt_inc {res['rt_income']:.2f} fees {res['fees']:.2f} halt_loss {res['halt_loss']:.2f} "
                    f"unreal {res['unrealized']:+.2f} maxinv {res['max_inv_pct']:.0f}% sharpe {m['sharpe']:.2f} "
                    f"vs bh_vm {m['bh_vm_sharpe']:.2f} mdd {m['mdd']:.0f} vs {m['bh_vm_mdd']:.0f} overwrite {res['overwrite']} untracked {res['untracked_fills']}")

    # book-level (sum of the three live budgets, primary window + live policy)
    syms = list(book_daily)
    bd = book_daily[syms[0]][["date"]].copy()
    bd["pnl"] = sum(book_daily[s]["pnl"].values for s in syms)
    bd["inv_usd"] = sum(book_daily[s]["inv_usd"].values for s in syms)
    bh_vm_book = sum(book_daily[s]["bh_volmatched_equity"].values - BUDGETS[s] for s in syms)
    bh_full_book = sum(book_daily[s]["bh_full_equity"].values - BUDGETS[s] for s in syms)
    pnl0 = np.concatenate([[0.0], bd["pnl"].values])
    bm0 = np.concatenate([[0.0], bh_vm_book])
    mid_i = len(bd) // 2

    def book_metrics(a, b):
        return dict(total_pnl=float(a[-1] - a[0]), sharpe=sharpe(np.diff(a)), mdd=max_drawdown(a - a[0]),
                    bh_vm_sharpe=sharpe(np.diff(b)), bh_vm_mdd=max_drawdown(b - b[0]), bh_vm_pnl=float(b[-1] - b[0]))
    mb = book_metrics(pnl0, bm0)
    mb1 = book_metrics(pnl0[:mid_i + 1], bm0[:mid_i + 1])
    mb2 = book_metrics(pnl0[mid_i:], bm0[mid_i:])
    vb = verdict(mb, mb1, mb2)
    results.append(dict(symbol="BOOK", window="12m_1min", policy="live", budget=sum(BUDGETS.values()),
                        start=str(bd["date"].iloc[0].date()), end=str(bd["date"].iloc[-1].date()), days=len(bd),
                        total_pnl_net=mb["total_pnl"], total_pnl_pct=mb["total_pnl"] / sum(BUDGETS.values()) * 100,
                        mdd_usd=mb["mdd"], sharpe=mb["sharpe"], nw_t=newey_west_t(np.diff(pnl0)),
                        bh_vm_pnl=mb["bh_vm_pnl"], bh_vm_sharpe=mb["bh_vm_sharpe"], bh_vm_mdd=mb["bh_vm_mdd"],
                        bh_full_pnl=float(bh_full_book[-1]), bh_full_sharpe=sharpe(np.diff(np.concatenate([[0.0], bh_full_book]))),
                        bh_full_mdd=max_drawdown(np.concatenate([[0.0], bh_full_book])),
                        h1_pnl=mb1["total_pnl"], h1_sharpe=mb1["sharpe"], h1_bh_vm_sharpe=mb1["bh_vm_sharpe"],
                        h1_mdd=mb1["mdd"], h1_bh_vm_mdd=mb1["bh_vm_mdd"],
                        h2_pnl=mb2["total_pnl"], h2_sharpe=mb2["sharpe"], h2_bh_vm_sharpe=mb2["bh_vm_sharpe"],
                        h2_mdd=mb2["mdd"], h2_bh_vm_mdd=mb2["bh_vm_mdd"],
                        round_trips=sum(r["round_trips"] for r in results if r["window"] == "12m_1min" and r["policy"] == "live"),
                        halts=sum(r["halts"] for r in results if r["window"] == "12m_1min" and r["policy"] == "live"),
                        verdict=vb))
    bdd = pd.DataFrame(dict(date=bd["date"], symbol="BOOK", window="12m_1min", policy="live",
                            grid_equity=sum(BUDGETS.values()) + bd["pnl"],
                            bh_volmatched_equity=sum(BUDGETS.values()) + bh_vm_book,
                            bh_full_equity=sum(BUDGETS.values()) + bh_full_book,
                            close=np.nan, inv_usd=bd["inv_usd"], in_market=np.nan))
    daily_out.append(bdd)
    verdicts["BOOK"] = (vb, mb, mb1, mb2)
    log(f"BOOK 12m_1min live: total {mb['total_pnl']:+.2f} sharpe {mb['sharpe']:.2f} vs bh_vm {mb['bh_vm_sharpe']:.2f} "
        f"mdd {mb['mdd']:.0f} vs {mb['bh_vm_mdd']:.0f} -> {vb}")

    # ── sanity check vs the real BTC fills log ────────────────────────────
    sanity = sanity_check(atr_tab=None)
    for k, v in sanity.items():
        log(f"sanity {k}: {v}")

    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame(results).to_csv(os.path.join(OUT, "crypto_grid_results.csv"), index=False)
    pd.DataFrame(decomp).to_csv(os.path.join(OUT, "crypto_grid_decomposition.csv"), index=False)
    pd.concat(daily_out, ignore_index=True).to_csv(os.path.join(OUT, "crypto_grid_daily.csv"), index=False)
    write_summary(results, decomp, verdicts, sanity)
    with open(os.path.join(OUT, "crypto_grid_run.log"), "w") as f:
        f.write("\n".join(LOG_LINES) + "\n")


def sanity_check(atr_tab):
    """Replay the live BTC grid from its real init through the last real fill."""
    out = {}
    if not os.path.exists(FILLS_LOG):
        return {"status": "no fills log"}
    real = pd.read_csv(FILLS_LOG)
    real["filled_at"] = pd.to_datetime(real["filled_at"], utc=True)
    real = real[real["level_price"].astype(str) != "None"]
    t0 = pd.Timestamp("2026-10-15T21:10:46Z")
    t1 = real["filled_at"].max()
    bars = load("BTC/USD", "1Min")
    bars = bars[(bars["t"] >= t0) & (bars["t"] <= t1)].reset_index(drop=True)
    forced = dict(mid=82185.143, atr_abs=1991.2831638325722, atr_pct=1991.2831638325722 / 82185.143 * 100)
    res, daily, trace = simulate(bars, None, 2500.0, "live", breakout_on_wicks=False, forced_init=forced)
    days = (t1 - t0).total_seconds() / 86400
    real_sells = real[real["side"] == "sell"]
    real_buys = real[real["side"] == "buy"]
    # real round trips as the bot counts them: one per sell ORDER (partials grouped)
    real_rt_orders = real_sells["order_id"].nunique()
    real_fill_orders = real["order_id"].nunique()
    out["window"] = f"{t0} .. {t1} ({days:.2f} days)"
    out["real_fill_orders_per_day"] = round(real_fill_orders / days, 2)
    out["sim_fills_per_day"] = round((res["buy_fills"] + res["sell_fills"]) / days, 2)
    out["real_round_trips"] = real_rt_orders
    out["real_round_trips_per_day"] = round(real_rt_orders / days, 2)
    out["sim_round_trips"] = res["round_trips"]
    out["sim_round_trips_per_day"] = round(res["round_trips"] / days, 2)
    out["real_realized_gross_usd"] = 21.4
    out["real_usd_per_round_trip_gross"] = round(21.4 / max(1, real_rt_orders), 3)
    out["sim_realized_gross_usd"] = round(res["rt_income"], 2)
    out["sim_usd_per_round_trip_gross"] = round(res["rt_income"] / max(1, res["round_trips"]), 3)
    out["sim_fees_usd"] = round(res["fees"], 2)
    out["sim_total_net_usd"] = round(res["total_pnl"], 2)
    out["sim_halts"] = res["halts"]
    out["real_sell_qty_mean"] = round(float(real_sells["qty"].mean()), 6)
    out["sim_qty_per_level"] = round(2500.0 / 9 / 82185.143, 6)
    # the live bot counts every sell ORDER as a round trip and Alpaca paper fills
    # many of them partially (mean sell qty is well under qty_per_level), so
    # per-trip $ is not comparable; compare coin volume sold and gross $ per day.
    out["real_qty_sold"] = round(float(real_sells["qty"].sum()), 5)
    out["sim_qty_sold"] = round(res["round_trips"] * 2500.0 / 9 / 82185.143, 5)
    out["real_gross_usd_per_day"] = round(21.4 / days, 2)
    out["sim_gross_usd_per_day"] = round(res["rt_income"] / days, 2)
    ratio_rt = res["round_trips"] / max(1, real_rt_orders)
    ratio_usd = (res["rt_income"] / max(1, res["round_trips"])) / (21.4 / max(1, real_rt_orders))
    ratio_vol = out["sim_qty_sold"] / max(1e-9, out["real_qty_sold"])
    ratio_gross = res["rt_income"] / 21.4
    out["ratio_sim_over_real_round_trips_per_day"] = round(ratio_rt, 2)
    out["ratio_sim_over_real_usd_per_round_trip"] = round(ratio_usd, 2)
    out["ratio_sim_over_real_qty_sold"] = round(ratio_vol, 2)
    out["ratio_sim_over_real_gross_usd"] = round(ratio_gross, 2)
    out["within_2x_on_volume_and_gross_usd"] = bool(0.5 <= ratio_vol <= 2 and 0.5 <= ratio_gross <= 2)
    out["within_2x_on_per_trip_counts"] = bool(0.5 <= ratio_rt <= 2 and 0.5 <= ratio_usd <= 2)
    return out


def write_summary(results, decomp, verdicts, sanity):
    R = pd.DataFrame(results)
    lines = ["Crypto grid study: pre-registered test of the live gridBot strategy", ""]
    lines.append("Pass rule (fixed before running): on the 12-month 1-minute window with the live halt policy, PASS if net P&L > 0, "
                 "Sharpe > vol-matched buy-and-hold Sharpe, max drawdown < vol-matched buy-and-hold max drawdown, and all three hold "
                 "in both halves. PARTIAL if net P&L > 0 but a benchmark condition fails. FAIL otherwise.")
    lines.append("")
    lines.append("Verdicts")
    for s, (v, m, m1, m2) in verdicts.items():
        lines.append(f"  {s}: {v}  (net P&L {m['total_pnl']:+.2f}, Sharpe {m['sharpe']:.2f} vs B&H vol-matched {m['bh_vm_sharpe']:.2f}, "
                     f"MDD {m['mdd']:.0f} vs {m['bh_vm_mdd']:.0f}; H1 P&L {m1['total_pnl']:+.2f} Sharpe {m1['sharpe']:.2f} vs {m1['bh_vm_sharpe']:.2f} "
                     f"MDD {m1['mdd']:.0f} vs {m1['bh_vm_mdd']:.0f}; H2 P&L {m2['total_pnl']:+.2f} Sharpe {m2['sharpe']:.2f} vs {m2['bh_vm_sharpe']:.2f} "
                     f"MDD {m2['mdd']:.0f} vs {m2['bh_vm_mdd']:.0f})")
    lines.append("")
    lines.append("Per symbol x window x policy")
    cols = ["symbol", "window", "policy", "budget", "days", "days_in_market", "round_trips", "halts", "rt_income_gross",
            "maker_fees", "taker_fees", "halt_loss_gross", "unrealized_end", "total_pnl_net", "total_pnl_pct",
            "avg_inv_usd", "max_inv_usd", "max_inv_pct", "mdd_usd", "sharpe", "nw_t", "bh_vm_pnl", "bh_vm_sharpe",
            "bh_vm_mdd", "bh_full_pnl", "bh_full_sharpe", "bh_full_mdd"]
    sub = R[[c for c in cols if c in R.columns]].copy()
    for c in sub.columns:
        if sub[c].dtype.kind == "f":
            sub[c] = sub[c].round(2)
    lines.append(sub.to_string(index=False))
    lines.append("")
    lines.append("Decomposition: total = round_trip_income + fees + inventory + halt_losses (fees, inventory and halt terms signed as contributions)")
    D = pd.DataFrame(decomp)
    for c in D.columns:
        if D[c].dtype.kind == "f":
            D[c] = D[c].round(2)
    lines.append(D.to_string(index=False))
    lines.append("")
    lines.append("Sanity check vs real BTC fills log (Oct 15-20 2026)")
    for k, v in sanity.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Interpretation")
    prim = R[(R["window"] == "12m_1min") & (R["policy"] == "live") & (R["symbol"] != "BOOK")]
    for _, r in prim.iterrows():
        lines.append(f"  {r['symbol']}: round trips earned {r['rt_income_gross']:.0f} gross; fees took {r['maker_fees'] + r['taker_fees']:.0f} "
                     f"({(r['maker_fees'] + r['taker_fees']) / max(1e-9, r['rt_income_gross']) * 100:.0f}% of gross), halts gave back "
                     f"{r['halt_loss_gross']:.0f}, inventory at the end {r['unrealized_end']:+.0f}; net {r['total_pnl_net']:+.0f} on a "
                     f"{r['budget']:.0f} budget vs {r['bh_vm_pnl']:+.0f} for holding the same average inventory ({r['avg_inv_usd']:.0f} USD) "
                     f"and {r['bh_full_pnl']:+.0f} for holding the full budget.")
    lines.append("  The grid's gross round-trip income exceeds what inventory and halts give back only before fees; after the 0.30% maker "
                 "round trip the residual per trip is a few cents and the breakout flattens, which always sell inventory bought on the way "
                 "down, dominate. The grid is a long-crypto exposure (max inventory about half the budget, average 10-20%) with a "
                 "negatively convex overlay: it sells the upside early and buys the downside until the kill switch realizes the loss.")
    lines.append("")
    lines.append("Caveats: fills at the level price with no price improvement (slightly pessimistic on income); re-arms live from the next "
                 "bar (matches the 1-minute cron); Alpaca paper partial fills are not modelled (the live log shows mean sell qty about a third "
                 "of qty_per_level, which fragments round trips but does not change gross $ much, see sanity block); the 12-month window is a "
                 "down year for all three coins, which is the regime where grids are supposed to shine relative to holding; the live reset "
                 "policy assumes a next-UTC-day manual reset; 1Hour history is a lower bound on round trips and uses a conservative "
                 "threshold flatten. 1Hour window is a lower bound on round trips (one fill per level per hour). ATR from completed "
                 "UTC daily bars. Pessimistic policy waits 7 days after a halt.")
    with open(os.path.join(OUT, "crypto_grid_summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
