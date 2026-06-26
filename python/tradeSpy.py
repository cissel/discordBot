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
MAX_DAILY_LOSS_PCT = 0.02    # halt trading if daily P&L < -2% of equity
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
            "side":          data.get("side", "long"),
        }
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"shares": 0, "market_value": 0.0, "avg_entry": 0.0, "unrealized_pl": 0.0, "side": "none"}
        raise


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

    # 2. Daily loss halt
    if account["last_equity"] > 0:
        daily_pnl_pct = (account["equity"] - account["last_equity"]) / account["last_equity"]
        if daily_pnl_pct < -MAX_DAILY_LOSS_PCT:
            flags.append(f"BLOCK: daily loss {daily_pnl_pct*100:.2f}% exceeds -{MAX_DAILY_LOSS_PCT*100:.0f}% limit")

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


def place_order(action, shares, headers, dry_run=True):
    """Place a market order. Returns order dict or simulated dict if dry_run."""
    if dry_run:
        return {
            "id": "DRY-RUN",
            "status": "simulated",
            "side": action.lower(),
            "qty": str(shares),
            "type": "market",
            "time_in_force": "day",
            "symbol": "SPY",
        }
    payload = {
        "symbol":        "SPY",
        "qty":           str(shares),
        "side":          action.lower(),   # "buy" or "sell"
        "type":          "market",
        "time_in_force": "day",
    }
    return alpaca_post("/v2/orders", payload, headers)


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


def run(execute=False):
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
    target_shares   = compute_target_shares(position_size, account["equity"], spy_price)
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
    if execute and order_shares >= MIN_ORDER_SHARES and passed:
        try:
            order_resp = place_order(action, order_shares, headers, dry_run=False)
            order_id = order_resp.get("id")
            result["executed"] = True
            result["order_id"] = order_id
            result["order"]["order_response"] = order_resp
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
    args = parser.parse_args()

    out = run(execute=args.execute)
    print(json.dumps(out, indent=2))
