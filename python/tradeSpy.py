#!/usr/bin/env python3
"""
tradeSpy.py
===========
SPY execution layer - paper trading via Alpaca.

Reads today's signal from predictSpy.py, computes target position,
diffs against current broker position, and places an order if needed.

Outputs JSON to stdout (used by Discord /spy trade command and EOD cron).

Paper trading endpoint: https://paper-api.alpaca.markets
Uses APCA_PAPER_API_KEY_ID + APCA_PAPER_API_SECRET_KEY from .env
(separate from live keys - requires a free Alpaca paper account).

Usage:
  venv/bin/python3 python/tradeSpy.py           # dry run - show signal + proposed order
  venv/bin/python3 python/tradeSpy.py --execute  # actually place the order

Output JSON:
{
  "date": "2026-06-26",
  "signal": { ...predictSpy output... },
  "account": { "equity": 100000.00, "cash": 98000.00, "buying_power": 98000.00 },
  "current_position": { "shares": 50, "market_value": 26500.00, "avg_entry": 527.50 },
  "target": { "position_size": 0.043, "target_shares": 8, "target_notional": 4240.00 },
  "order": { "action": "BUY"|"SELL"|"HOLD", "shares": 8, "reason": "..." },
  "risk_checks": { "passed": true, "flags": [] },
  "executed": false,
  "order_id": null,
  "trade_log_row": { ... },
  "errors": []
}
"""

import os
import sys
import json
import math
import csv
import datetime
import argparse
import importlib.util
import requests

# ── Config ──────────────────────────────────────────────────────────────────
ENV_PATH      = os.path.expanduser("~/discordBot/.env")
TRADE_LOG     = os.path.expanduser("~/discordBot/outputs/markets/spy_trade_log.csv")
PAPER_BASE    = "https://paper-api.alpaca.markets"
PYTHON        = os.path.expanduser("~/discordBot/venv/bin/python3")
PREDICT_SCRIPT = os.path.expanduser("~/discordBot/python/predictSpy.py")

# Risk limits
MAX_POSITION_PCT   = 0.20    # never more than 20% of equity in SPY
MAX_DAILY_LOSS_PCT = 0.02    # halt if the SPY STRATEGY's own daily P&L < -2% of equity
MAX_STRATEGY_DD_PCT = 0.05   # halt new buys if the SPY strategy's cumulative P&L is > 5% of equity below its peak
                             # (was 2.5% under the Kelly-sized model; the trend/vol book runs ~10-30% exposure)

# ── Strategy mode (Oct 19 2026) ──────────────────────────────────────────────
# "trend_vol": long SPY when its close is above the 200-day moving average, flat
#   below; exposure = BASE_EXPOSURE x vol-regime scalar (spyVolRegime). Rebalance
#   only when the target moves by more than REBALANCE_BAND of itself or the trend
#   state flips. The direction model still runs every day and is logged
#   (signal_direction / signal_prob) so the watchdog keeps scoring it against
#   always-UP, but it does not size or gate the position (shadow mode).
#   Evidence: studies 8 and 10 (python/research/structural/spy_direction_audit.py,
#   spy_horizon_baseline_study.py): the model is indistinguishable from always-UP
#   out of sample (log-loss 0.72 vs 0.69), and the 200dma rule matches or beats it
#   (Sharpe 0.85 vs 0.72, maxDD -24% vs -44%, NW t 0.13) with 6 trades a year.
# "model": the previous behaviour (Kelly size from predictSpy x vol scalar).
STRATEGY_MODE  = "trend_vol"
BASE_EXPOSURE  = 0.25     # fraction of equity in SPY when above the 200dma at vol scalar 1.0
TREND_MA_DAYS  = 200
REBALANCE_BAND = 0.10     # ignore target changes smaller than 10% of the current target
SPY_RET_CACHE  = os.path.expanduser("~/discordBot/outputs/markets/cache/SPY.csv")


def trend_state(ma_days=TREND_MA_DAYS):
    """SPY close vs its 200-day MA from the daily-return cache (which carries today's
    provisional row in the pre-close run). Returns (above: bool, ratio index/MA)."""
    import csv as _csv
    rets = []
    with open(SPY_RET_CACHE) as f:
        for row in _csv.DictReader(f):
            try:
                rets.append(float(row["SPY_ret"]))
            except (ValueError, KeyError):
                continue
    if len(rets) < ma_days + 1:
        raise RuntimeError(f"only {len(rets)} rows in SPY.csv, need {ma_days}")
    idx, lvl = [], 1.0
    for r in rets:
        lvl *= (1.0 + r)
        idx.append(lvl)
    ma = sum(idx[-ma_days:]) / ma_days
    return idx[-1] > ma, idx[-1] / ma
LEDGER_DAILY = os.path.expanduser("~/discordBot/outputs/markets/pnl_ledger_daily.csv")
# NOTE (Oct 2026): this paper account is shared with other bots (MM, crypto
# grids). Account-level equity / last_equity therefore says nothing about THIS
# strategy, so every risk check below is computed from the SPY position and
# the per-strategy ledger (pnlLedger.py), never from account P&L.
MIN_ORDER_SHARES   = 1       # don't trade fractional shares or 0-share orders
MAX_SHARES_PER_DAY = 500     # hard cap on single-day order size (safety)


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


def get_signal():
    """Run predictSpy.py and return parsed JSON."""
    import subprocess
    result = subprocess.run(
        [PYTHON, PREDICT_SCRIPT],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"predictSpy.py failed: {result.stderr[:400]}")
    return json.loads(result.stdout)


def alpaca_get(endpoint, headers):
    """GET from Alpaca paper API."""
    url = f"{PAPER_BASE}{endpoint}"
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def alpaca_post(endpoint, payload, headers):
    """POST to Alpaca paper API."""
    url = f"{PAPER_BASE}{endpoint}"
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_account(headers):
    data = alpaca_get("/v2/account", headers)
    return {
        "equity":        float(data.get("equity", 0)),
        "cash":          float(data.get("cash", 0)),
        "buying_power":  float(data.get("buying_power", 0)),
        "last_equity":   float(data.get("last_equity", 0)),
        "status":        data.get("status", "unknown"),
    }


def get_spy_position(headers):
    """Returns current SPY position or None if flat."""
    try:
        data = alpaca_get("/v2/positions/SPY", headers)
        return {
            "shares":       float(data.get("qty", 0)),
            "market_value": float(data.get("market_value", 0)),
            "avg_entry":    float(data.get("avg_entry_price", 0)),
            "unrealized_pl": float(data.get("unrealized_pl", 0)),
            # SPY-only P&L since yesterday's close (Alpaca computes this per position)
            "intraday_pl":   float(data.get("unrealized_intraday_pl", 0) or 0),
            "side":          data.get("side", "long"),
        }
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"shares": 0, "market_value": 0.0, "avg_entry": 0.0, "unrealized_pl": 0.0,
                    "intraday_pl": 0.0, "side": "none"}
        raise


def spy_strategy_drawdown():
    """(current_total, peak_total, drawdown) for the 'SPY autopilot' row of the
    pnlLedger daily series, or None if the ledger hasn't run yet."""
    if not os.path.exists(LEDGER_DAILY):
        return None
    vals = []
    with open(LEDGER_DAILY) as f:
        for r in csv.DictReader(f):
            if r.get("strategy") == "SPY autopilot":
                try:
                    vals.append(float(r["total"]))
                except (TypeError, ValueError):
                    pass
    if not vals:
        return None
    peak = max(vals)
    return vals[-1], peak, vals[-1] - peak


def get_spy_price(headers):
    """Latest SPY mid price from Alpaca snapshot."""
    try:
        data = requests.get(
            "https://data.alpaca.markets/v2/stocks/SPY/snapshot",
            headers=headers, timeout=10
        ).json()
        latest = data.get("latestTrade", {})
        return float(latest.get("p", 0)) or float(data.get("latestQuote", {}).get("ap", 0))
    except Exception:
        return None


def get_today_orders(headers):
    """Get today's filled/pending orders for SPY."""
    today = datetime.date.today().isoformat()
    try:
        orders = alpaca_get(f"/v2/orders?status=all&after={today}T00:00:00Z&symbols=SPY&limit=50", headers)
        return orders if isinstance(orders, list) else []
    except Exception:
        return []


def run_risk_checks(account, current_pos, target_shares, spy_price, signal):
    """
    Returns (passed: bool, flags: list[str]).
    Flags are warnings/blocks. If any flag starts with BLOCK: trade is halted.
    """
    flags = []

    # 1. Account status
    if account["status"] != "ACTIVE":
        flags.append(f"BLOCK: account not ACTIVE (status={account['status']})")

    # 2. Daily loss halt - SPY strategy only (account equity is shared with
    #    the MM / grid bots and would block SPY for their losses)
    if account["equity"] > 0 and current_pos:
        spy_daily = current_pos.get("intraday_pl", 0.0)
        spy_daily_pct = spy_daily / account["equity"]
        if spy_daily_pct < -MAX_DAILY_LOSS_PCT:
            flags.append(f"BLOCK: SPY strategy daily loss ${spy_daily:,.0f} ({spy_daily_pct*100:.2f}% of equity) "
                         f"exceeds -{MAX_DAILY_LOSS_PCT*100:.0f}% limit")
        acct_daily_pct = ((account["equity"] - account["last_equity"]) / account["last_equity"]
                          if account["last_equity"] > 0 else 0.0)
        if acct_daily_pct < -MAX_DAILY_LOSS_PCT:
            flags.append(f"INFO: account-wide daily P&L {acct_daily_pct*100:.2f}% (other bots) - not a SPY block")

    # 2b. Strategy drawdown halt - from the per-strategy ledger (pnlLedger.py)
    dd = spy_strategy_drawdown()
    if dd is not None and account["equity"] > 0:
        cur, peak, draw = dd
        if draw < -MAX_STRATEGY_DD_PCT * account["equity"]:
            flags.append(f"BLOCK: SPY strategy drawdown ${draw:,.0f} from peak ${peak:,.0f} "
                         f"exceeds -{MAX_STRATEGY_DD_PCT*100:.1f}% of equity")

    # 3. Max position cap
    if spy_price and spy_price > 0:
        target_notional = target_shares * spy_price
        if target_notional > account["equity"] * MAX_POSITION_PCT:
            flags.append(
                f"WARN: target notional ${target_notional:,.0f} > "
                f"{MAX_POSITION_PCT*100:.0f}% equity cap ${account['equity']*MAX_POSITION_PCT:,.0f} - clamping"
            )

    # 4. Kill switch from signal
    sizing_reason = signal.get("sizing_reason", "")
    if "kill switch" in sizing_reason.lower():
        flags.append(f"BLOCK: model kill switch active - {sizing_reason}")

    # 5. Event window caution
    macro = signal.get("macro_context", {})
    if macro.get("is_event_window"):
        flags.append("WARN: FOMC/CPI/NFP within 1 day - position sizing already conservative")

    # 6. Order size cap
    current_shares = current_pos["shares"] if current_pos else 0
    delta = abs(target_shares - current_shares)
    if delta > MAX_SHARES_PER_DAY:
        flags.append(f"BLOCK: order size {delta} shares exceeds hard cap {MAX_SHARES_PER_DAY}")

    # 7. Model warnings passthrough
    for w in signal.get("warnings", []):
        flags.append(f"MODEL: {w}")

    blocks = [f for f in flags if f.startswith("BLOCK:")]
    passed = len(blocks) == 0
    return passed, flags


def compute_target_shares(position_size, equity, spy_price):
    """
    Convert Kelly fraction (position_size 0-1) to whole shares.
    position_size already incorporates bear/chop adjustments from predictSpy.
    Clamp to MAX_POSITION_PCT as a hard cap.
    """
    if not spy_price or spy_price <= 0:
        return 0
    max_notional = equity * MAX_POSITION_PCT
    target_notional = min(position_size * equity, max_notional)
    return int(math.floor(target_notional / spy_price))


def place_order(action, shares, headers, dry_run=True, time_in_force="day"):
    """Place a market order. time_in_force 'cls' = market-on-close (Alpaca
    accepts cls until ~15:50 ET). Returns order dict or simulated dict if dry_run."""
    if dry_run:
        return {
            "id": "DRY-RUN",
            "status": "simulated",
            "side": action.lower(),
            "qty": str(shares),
            "type": "market",
            "time_in_force": time_in_force,
            "symbol": "SPY",
        }
    payload = {
        "symbol":        "SPY",
        "qty":           str(shares),
        "side":          action.lower(),   # "buy" or "sell"
        "type":          "market",
        "time_in_force": time_in_force,
    }
    return alpaca_post("/v2/orders", payload, headers)


# ── Pre-close execution (Oct 2026) ──────────────────────────────────────────
# spyExecutionCost.py measured the autopilot's real cost: the signal is made on
# the 16:15 close but market orders fill at the NEXT 09:30 open, and that
# overnight gap ran +17.9bp against the order (adverse 69% of fills, $340 over
# 75 fills vs +$131 total strategy P&L). The spread itself is 0.12bp.
# Fix: a 15:25 pre-close run rebuilds today's feature row from the partial
# session, runs the same signal, and places a market-on-close (tif=cls) order,
# so the fill happens at the close the signal was computed against. The 16:15
# run then logs the official signal but does NOT trade again (double-trading
# the small residual would re-create the gap cost). State file below is the
# handshake between the two runs.
PRECLOSE_STATE_DIR = os.path.expanduser("~/discordBot/outputs/markets/preclose")
MOC_CUTOFF_ET = datetime.time(15, 49)   # Alpaca rejects cls after ~15:50 ET
# Oct 19 2026: Alpaca PAPER does not simulate closing-auction fills; the first
# tif=cls order sat "new" and expired at 16:00:43 unfilled. So the pre-close run
# no longer places an order itself: it writes the target to the handshake file
# (pending=True) and spyPrecloseFire.py (cron 15:52) sends a plain market order
# for the delta, which fills within seconds, ~8 minutes before the close.
PRECLOSE_FIRE_ONLY = True


def preclose_state_path(day=None):
    d = day or datetime.date.today().isoformat()
    return os.path.join(PRECLOSE_STATE_DIR, f"spy_preclose_{d}.json")


def load_preclose_state(day=None):
    p = preclose_state_path(day)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def save_preclose_state(state):
    os.makedirs(PRECLOSE_STATE_DIR, exist_ok=True)
    with open(preclose_state_path(state["date"]), "w") as f:
        json.dump(state, f, indent=2, default=str)


def now_et():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.datetime.now()


def append_trade_log(row):
    """Append a row to the trade log CSV."""
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    fields = [
        "date", "signal_direction", "signal_prob", "signal_confidence",
        "regime", "position_size_kelly", "spy_price", "equity",
        "prev_shares", "target_shares", "order_action", "order_shares",
        "executed", "order_id", "sizing_reason", "risk_flags", "errors"
    ]
    write_header = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a") as f:
        if write_header:
            f.write(",".join(fields) + "\n")
        vals = []
        for field in fields:
            val = str(row.get(field, "")).replace(",", ";").replace("\n", " ")
            vals.append(val)
        f.write(",".join(vals) + "\n")


def is_market_hours():
    """Rough check - Alpaca paper will reject outside-hours market orders anyway."""
    now_et = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
    if now_et.weekday() >= 5:  # weekend
        return False
    market_open  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et <= market_close


def run(execute=False, preclose=False):
    errors = []
    result = {
        "date":             str(datetime.date.today()),
        "signal":           None,
        "account":          None,
        "current_position": None,
        "target":           None,
        "order":            None,
        "risk_checks":      {"passed": False, "flags": []},
        "executed":         False,
        "order_id":         None,
        "trade_log_row":    None,
        "errors":           errors,
    }

    # ── 1. Load env + build headers ──────────────────────────────────────────
    env = load_env()
    paper_key    = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    paper_secret = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()

    if not paper_key or not paper_secret:
        errors.append(
            "APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not set in .env. "
            "Create a free Alpaca paper account at app.alpaca.markets/paper-trading "
            "and add the paper-specific keys."
        )
        return result

    headers = {
        "APCA-API-KEY-ID":     paper_key,
        "APCA-API-SECRET-KEY": paper_secret,
        "Content-Type":        "application/json",
    }
    # Data API uses the same paper keys but a different base
    data_headers = {
        "APCA-API-KEY-ID":     paper_key,
        "APCA-API-SECRET-KEY": paper_secret,
    }

    # ── 2. Get signal ────────────────────────────────────────────────────────
    try:
        signal = get_signal()
        result["signal"] = signal
    except Exception as e:
        errors.append(f"predictSpy failed: {e}")
        return result

    position_size  = signal.get("position_size", 0.0)
    sizing_reason  = signal.get("sizing_reason", "")
    next_day       = signal.get("next_day") or {}
    regime_probs   = signal.get("regime_probs", {})
    current_regime = max(regime_probs, key=regime_probs.get) if regime_probs else "unknown"

    # ── 2b. Vol-regime scalar (GEX quintile + VIX term structure) ───────────
    # Scales the Kelly size by expected-realized vol so risk per day is
    # roughly constant across dealer-gamma regimes. Never flips direction and
    # never applies to a zero size. See python/spyVolRegime.py for the study
    # (Sharpe 0.80 -> 0.93, max DD -4.1% -> -1.9%, 2016-2026).
    vol_regime = None
    if position_size > 0:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from spyVolRegime import current_scalar
            vol_regime = current_scalar()
            raw_size = position_size
            position_size = round(raw_size * vol_regime["scalar"], 4)
            sizing_reason = f"{sizing_reason} | {vol_regime['label']} raw={raw_size:.4f} -> eff={position_size:.4f}"
        except Exception as e:
            errors.append(f"vol-regime scalar unavailable, using raw Kelly size: {e}")
    result["vol_regime"] = vol_regime

    # ── 2c. Strategy mode: trend/vol book overrides the model size ───────────
    model_size = position_size
    if STRATEGY_MODE == "trend_vol":
        try:
            if vol_regime is None:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from spyVolRegime import current_scalar
                vol_regime = current_scalar()
                result["vol_regime"] = vol_regime
            above, ratio = trend_state()
            scalar = float(vol_regime["scalar"]) if vol_regime else 1.0
            position_size = round(BASE_EXPOSURE * scalar, 4) if above else 0.0
            sizing_reason = (f"TREND/VOL: SPY {'above' if above else 'below'} {TREND_MA_DAYS}dma (idx/ma {ratio:.3f}) "
                             f"-> base {BASE_EXPOSURE:.2f} x vol_scalar {scalar:.2f} = {position_size:.3f}"
                             f" | shadow model: blend={next_day.get('probability', '')} kelly_size={model_size}")
            result["trend"] = {"above_ma": above, "ratio": round(ratio, 4), "ma_days": TREND_MA_DAYS}
        except Exception as e:
            errors.append(f"trend state unavailable, holding current position: {e}")
            position_size = None   # sentinel: do not trade
    result["strategy_mode"] = STRATEGY_MODE

    # ── 3. Get broker state ──────────────────────────────────────────────────
    try:
        account      = get_account(headers)
        result["account"] = account
    except Exception as e:
        errors.append(f"Alpaca account fetch failed: {e}")
        return result

    try:
        current_pos  = get_spy_position(headers)
        result["current_position"] = current_pos
    except Exception as e:
        errors.append(f"Position fetch failed: {e}")
        current_pos = {"shares": 0, "market_value": 0.0, "avg_entry": 0.0, "unrealized_pl": 0.0, "side": "none"}
        result["current_position"] = current_pos

    try:
        spy_price = get_spy_price(data_headers)
    except Exception as e:
        errors.append(f"SPY price fetch failed: {e}")
        spy_price = None

    # ── 4. Compute target ────────────────────────────────────────────────────
    if position_size is None:
        position_size = (current_pos["market_value"] / account["equity"]) if account["equity"] else 0.0
        target_shares = int(current_pos["shares"])
    else:
        target_shares = compute_target_shares(position_size, account["equity"], spy_price)
    # rebalance band: small drifts in the vol scalar should not generate daily orders
    if STRATEGY_MODE == "trend_vol" and target_shares > 0 and int(current_pos["shares"]) > 0:
        if abs(target_shares - int(current_pos["shares"])) < max(1, REBALANCE_BAND * target_shares):
            target_shares = int(current_pos["shares"])
            sizing_reason += " | within rebalance band, hold"
    target_notional = (target_shares * spy_price) if spy_price else 0.0
    result["target"] = {
        "position_size_kelly": position_size,
        "target_shares":       target_shares,
        "target_notional":     round(target_notional, 2),
        "spy_price":           round(spy_price, 2) if spy_price else None,
    }

    # ── 5. Risk checks ───────────────────────────────────────────────────────
    passed, flags = run_risk_checks(account, current_pos, target_shares, spy_price, signal)
    result["risk_checks"] = {"passed": passed, "flags": flags}

    # ── 6. Compute order ─────────────────────────────────────────────────────
    current_shares = int(current_pos["shares"])
    delta          = target_shares - current_shares

    if delta > 0:
        action, order_shares = "BUY", delta
        order_reason = f"increase position {current_shares} -> {target_shares} shares"
    elif delta < 0:
        action, order_shares = "SELL", abs(delta)
        order_reason = f"reduce position {current_shares} -> {target_shares} shares"
    else:
        action, order_shares = "HOLD", 0
        order_reason = "already at target"

    # Flat signal = close any open position
    if position_size == 0.0 and current_shares > 0:
        action, order_shares = "SELL", current_shares
        order_reason = f"signal flat ({sizing_reason}) - close full position"

    result["order"] = {
        "action":       action,
        "shares":       order_shares,
        "reason":       order_reason,
        "sizing_reason": sizing_reason,
    }

    # ── 7. Execute ───────────────────────────────────────────────────────────
    order_id = None
    pre = None if preclose else load_preclose_state()
    if execute and pre is not None and (pre.get("executed") or pre.get("fired")):
        # The 15:25 pre-close run already placed today's MOC order. Log the
        # official 16:15 signal but keep the REAL traded size in the log row so
        # watchdog / performance measure what is actually held.
        action, order_shares = "SKIP", 0
        result["order"]["action"] = "SKIP"
        result["order"]["reason"] = (f"pre-close MOC already placed at {pre.get('placed_at','?')} "
                                     f"(target {pre.get('target_shares')} sh, size {pre.get('position_size')})")
        sizing_reason = (f"{sizing_reason} || PRECLOSE traded: {pre.get('sizing_reason','')}")
        position_size = float(pre.get("position_size", position_size))
        target_shares = int(pre.get("target_shares", target_shares))
        result["preclose"] = pre
    elif execute and preclose and PRECLOSE_FIRE_ONLY and order_shares >= MIN_ORDER_SHARES and passed:
        # defer to spyPrecloseFire.py at 15:52 (see PRECLOSE_FIRE_ONLY note)
        result["order"]["deferred_to_fire"] = True
        result["order"]["time_in_force"] = "day@15:52"
    elif execute and order_shares >= MIN_ORDER_SHARES and passed:
        tif = "day"
        if preclose:
            tif = "cls" if now_et().time() < MOC_CUTOFF_ET else "day"
            if tif == "day":
                errors.append("pre-close run past MOC cutoff - sent a regular market order instead")
        try:
            order_resp = place_order(action, order_shares, headers, dry_run=False, time_in_force=tif)
            order_id = order_resp.get("id")
            result["executed"] = True
            result["order_id"] = order_id
            result["order"]["order_response"] = order_resp
            result["order"]["time_in_force"] = tif
        except Exception as e:
            errors.append(f"Order placement failed: {e}")
    elif execute and not passed:
        errors.append("Order blocked by risk checks - see risk_checks.flags")
    elif execute and order_shares < MIN_ORDER_SHARES:
        result["order"]["action"] = "HOLD"
        result["order"]["reason"] = f"order too small ({order_shares} shares < {MIN_ORDER_SHARES} minimum)"

    # ── 8. Trade log ─────────────────────────────────────────────────────────
    log_row = {
        "date":                 str(datetime.date.today()),
        "signal_direction":     next_day.get("direction", ""),
        "signal_prob":          next_day.get("probability", ""),
        "signal_confidence":    next_day.get("confidence", ""),
        "regime":               current_regime,
        "position_size_kelly":  position_size,
        "spy_price":            round(spy_price, 2) if spy_price else "",
        "equity":               round(account["equity"], 2),
        "prev_shares":          current_shares,
        "target_shares":        target_shares,
        "order_action":         action,
        "order_shares":         order_shares,
        "executed":             result["executed"],
        "order_id":             order_id or "",
        "sizing_reason":        sizing_reason,
        "risk_flags":           "|".join(flags),
        "errors":               "|".join(errors),
    }
    if preclose:
        log_row["sizing_reason"] = "PRECLOSE/MOC " + log_row["sizing_reason"]
        if execute:
            deferred = bool(result["order"].get("deferred_to_fire"))
            save_preclose_state({
                "date": str(datetime.date.today()), "placed_at": now_et().strftime("%H:%M:%S"),
                "executed": bool(result["executed"]), "order_id": order_id or "",
                "pending": deferred, "fired": False, "passed_risk": bool(passed),
                "action": action, "order_shares": order_shares, "target_shares": target_shares,
                "position_size": position_size, "spy_price_proxy": spy_price,
                "signal_prob": next_day.get("probability"), "sizing_reason": sizing_reason,
                "risk_flags": flags, "errors": errors,
            })
    result["trade_log_row"] = log_row
    try:
        append_trade_log(log_row)
    except Exception as e:
        errors.append(f"Trade log write failed: {e}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually place the order (default: dry run only)")
    parser.add_argument("--preclose", action="store_true",
                        help="Pre-close mode: market-on-close order (tif=cls) and write the handshake "
                             "state file so the 16:15 run logs but does not trade again")
    args = parser.parse_args()

    out = run(execute=args.execute, preclose=args.preclose)
    print(json.dumps(out, indent=2))
