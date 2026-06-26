#!/usr/bin/env python3
"""
spyTradeState.py
================
Read current paper trading state: position, orders, P&L, trade log summary.
Used by the /spy trade Discord command to display status without placing an order.

Outputs JSON to stdout.

Usage:
  venv/bin/python3 python/spyTradeState.py
"""

import os
import sys
import json
import datetime
import requests
import csv

ENV_PATH   = os.path.expanduser("~/discordBot/.env")
TRADE_LOG  = os.path.expanduser("~/discordBot/outputs/markets/spy_trade_log.csv")
PAPER_BASE = "https://paper-api.alpaca.markets"


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


def alpaca_get(endpoint, headers):
    url = f"{PAPER_BASE}{endpoint}"
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_account(headers):
    data = alpaca_get("/v2/account", headers)
    equity     = float(data.get("equity", 0))
    last_eq    = float(data.get("last_equity", 0))
    daily_pnl  = equity - last_eq
    daily_pct  = (daily_pnl / last_eq * 100) if last_eq > 0 else 0
    return {
        "equity":       round(equity, 2),
        "cash":         round(float(data.get("cash", 0)), 2),
        "buying_power": round(float(data.get("buying_power", 0)), 2),
        "last_equity":  round(last_eq, 2),
        "daily_pnl":    round(daily_pnl, 2),
        "daily_pct":    round(daily_pct, 4),
        "status":       data.get("status", "unknown"),
    }


def get_spy_position(headers):
    try:
        data = alpaca_get("/v2/positions/SPY", headers)
        return {
            "shares":        float(data.get("qty", 0)),
            "market_value":  float(data.get("market_value", 0)),
            "avg_entry":     float(data.get("avg_entry_price", 0)),
            "unrealized_pl": float(data.get("unrealized_pl", 0)),
            "unrealized_pct": float(data.get("unrealized_plpc", 0)) * 100,
            "side":          data.get("side", "long"),
        }
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"shares": 0, "market_value": 0.0, "avg_entry": 0.0,
                    "unrealized_pl": 0.0, "unrealized_pct": 0.0, "side": "none"}
        raise


def get_recent_orders(headers, limit=10):
    try:
        orders = alpaca_get(f"/v2/orders?status=all&limit={limit}&symbols=SPY", headers)
        if not isinstance(orders, list):
            return []
        out = []
        for o in orders:
            out.append({
                "id":          o.get("id", "")[:8],
                "side":        o.get("side", ""),
                "qty":         o.get("qty", ""),
                "filled_qty":  o.get("filled_qty", "0"),
                "status":      o.get("status", ""),
                "filled_avg":  o.get("filled_avg_price", ""),
                "created_at":  (o.get("created_at", "") or "")[:10],
            })
        return out
    except Exception:
        return []


def get_trade_log_summary(n_rows=10):
    """Return last N rows of trade log + simple stats."""
    if not os.path.exists(TRADE_LOG):
        return {"rows": [], "total_trades": 0, "total_executed": 0, "note": "no log yet"}
    rows = []
    with open(TRADE_LOG) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    total     = len(rows)
    executed  = sum(1 for r in rows if str(r.get("executed","")).lower() == "true")
    buys      = sum(1 for r in rows if r.get("order_action") == "BUY" and str(r.get("executed","")).lower() == "true")
    sells     = sum(1 for r in rows if r.get("order_action") == "SELL" and str(r.get("executed","")).lower() == "true")
    last_rows = rows[-n_rows:] if len(rows) > n_rows else rows
    # Only return key fields
    compact = []
    for r in reversed(last_rows):
        compact.append({
            "date":       r.get("date", ""),
            "action":     r.get("order_action", ""),
            "shares":     r.get("order_shares", ""),
            "direction":  r.get("signal_direction", ""),
            "prob":       r.get("signal_prob", ""),
            "regime":     r.get("regime", ""),
            "kelly":      r.get("position_size_kelly", ""),
            "executed":   r.get("executed", ""),
            "flags":      r.get("risk_flags", "") or "",
        })
    return {
        "total_rows":    total,
        "total_executed": executed,
        "total_buys":    buys,
        "total_sells":   sells,
        "recent":        compact,
    }


def run():
    env = load_env()
    paper_key    = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    paper_secret = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()

    if not paper_key or not paper_secret:
        return {
            "error": (
                "APCA_PAPER_API_KEY_ID / APCA_PAPER_API_SECRET_KEY not set in .env. "
                "Create a free Alpaca paper account at app.alpaca.markets and add paper keys."
            ),
            "trade_log": get_trade_log_summary()
        }

    headers = {
        "APCA-API-KEY-ID":     paper_key,
        "APCA-API-SECRET-KEY": paper_secret,
    }

    result = {
        "date":      str(datetime.date.today()),
        "account":   None,
        "position":  None,
        "orders":    [],
        "trade_log": None,
        "errors":    [],
    }

    try:
        result["account"]  = get_account(headers)
    except Exception as e:
        result["errors"].append(f"account: {e}")

    try:
        result["position"] = get_spy_position(headers)
    except Exception as e:
        result["errors"].append(f"position: {e}")

    try:
        result["orders"] = get_recent_orders(headers)
    except Exception as e:
        result["errors"].append(f"orders: {e}")

    result["trade_log"] = get_trade_log_summary()

    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
