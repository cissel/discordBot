#!/usr/bin/env python3
"""
spyPerformance.py
=================
Forward track-record analysis for the SPY paper trading system.

Reads:
  - outputs/markets/spy_trade_log.csv   (written daily by tradeSpy.py via EOD cron)
  - outputs/features/markets/spy_features.csv  (date, SPY_ret - realized daily returns)
  - Alpaca paper portfolio history (optional - graceful fallback if keys missing)

Writes:
  - outputs/markets/spy_performance_daily.csv  (per-day joined record for R plot)
  - outputs/markets/spy_performance.json       (summary stats for Discord embed)

Outputs JSON to stdout (same content as the .json file).

Usage:
  venv/bin/python3 python/spyPerformance.py
"""

import os
import sys
import json
import math
import datetime
import csv

import requests

BASE          = os.path.expanduser("~/discordBot")
ENV_PATH      = os.path.join(BASE, ".env")
TRADE_LOG     = os.path.join(BASE, "outputs/markets/spy_trade_log.csv")
FEATURES_CSV  = os.path.join(BASE, "outputs/features/markets/spy_features.csv")
OUT_DAILY     = os.path.join(BASE, "outputs/markets/spy_performance_daily.csv")
OUT_JSON      = os.path.join(BASE, "outputs/markets/spy_performance.json")
PAPER_BASE    = "https://paper-api.alpaca.markets"

# Backtest reference (Run 36 WFCV) - used for the drift comparison band
WFCV_SHARPE   = 1.855
WFCV_HIT_RATE = None   # filled from experiment log if available
EXPERIMENT_LOG = os.path.join(BASE, "models/meta/spy_experiment_log.csv")

MIN_DAYS_MEANINGFUL = 20


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


def read_trade_log():
    """Read trade log rows, dedup by date (keep last row per date)."""
    if not os.path.exists(TRADE_LOG):
        return []
    rows = {}
    with open(TRADE_LOG) as f:
        for r in csv.DictReader(f):
            d = (r.get("date") or "").strip()
            if d:
                rows[d] = r
    return [rows[k] for k in sorted(rows)]


def read_spy_returns():
    """date -> that day's SPY return, from the features file."""
    rets = {}
    if not os.path.exists(FEATURES_CSV):
        return rets
    with open(FEATURES_CSV) as f:
        reader = csv.reader(f)
        header = next(reader)
        try:
            di = header.index("date")
            ri = header.index("SPY_ret")
        except ValueError:
            return rets
        for row in reader:
            try:
                rets[row[di]] = float(row[ri])
            except (ValueError, IndexError):
                continue
    return rets


def wfcv_expected_hit_rate():
    """Pull the most recent blend/GBM ensemble WFCV accuracy from the experiment log."""
    if not os.path.exists(EXPERIMENT_LOG):
        return None
    best = None
    try:
        with open(EXPERIMENT_LOG) as f:
            for r in csv.DictReader(f):
                for key in ("wfcv_dir_acc_mean", "holdout_dir_acc", "val_dir_acc",
                            "wfcv_acc", "accuracy", "acc", "val_acc"):
                    v = (r.get(key) or "").strip()
                    if v:
                        try:
                            best = float(v)
                        except ValueError:
                            pass
                        break
    except Exception:
        return None
    if best is not None and best > 1.0:
        best = best / 100.0
    return best


def alpaca_portfolio_history(env):
    """Actual paper equity curve from Alpaca. Returns list of (date, equity) or None."""
    key    = env.get("APCA_PAPER_API_KEY_ID", "").strip()
    secret = env.get("APCA_PAPER_API_SECRET_KEY", "").strip()
    if not key or not secret:
        return None
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        resp = requests.get(
            f"{PAPER_BASE}/v2/account/portfolio/history",
            params={"period": "1A", "timeframe": "1D"},
            headers=headers, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ts  = data.get("timestamp") or []
        eq  = data.get("equity") or []
        out = []
        for t, e in zip(ts, eq):
            if e is None:
                continue
            d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat()
            out.append((d, float(e)))
        return out or None
    except Exception:
        return None


def sharpe(returns):
    n = len(returns)
    if n < 2:
        return None
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (mu / sd) * math.sqrt(252)


def max_drawdown(equity):
    peak, mdd = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1.0)
    return mdd


def binom_lower_bound(p, n, z=1.645):
    """One-sided lower bound (normal approx) for expected hit rate p over n trials."""
    if n < 1:
        return 0.0
    return p - z * math.sqrt(p * (1 - p) / n)


def main():
    errors = []
    env = load_env()

    log_rows = read_trade_log()
    spy_rets = read_spy_returns()
    ret_dates = sorted(spy_rets)

    def next_trading_ret(d):
        """Realized SPY return on the first trading day AFTER signal date d."""
        for rd in ret_dates:
            if rd > d:
                return rd, spy_rets[rd]
        return None, None

    daily = []
    for r in log_rows:
        d = r["date"]
        direction = (r.get("signal_direction") or "").upper()
        try:
            prob = float(r.get("signal_prob") or 0.5)
        except ValueError:
            prob = 0.5
        try:
            kelly = float(r.get("position_size_kelly") or 0.0)
        except ValueError:
            kelly = 0.0
        executed = str(r.get("executed", "")).strip().lower() == "true"

        rd, realized = next_trading_ret(d)
        if realized is None:
            hit, strat_ret = None, None      # outcome pending
        else:
            if direction == "UP":
                hit = 1 if realized > 0 else 0
            elif direction == "DOWN":
                hit = 1 if realized < 0 else 0
            else:
                hit = None
            strat_ret = kelly * realized     # long-only: flat when kelly=0

        try:
            equity = float(r.get("equity") or 0)
        except ValueError:
            equity = 0
        daily.append({
            "date": d, "direction": direction, "prob": prob, "kelly": kelly,
            "regime": r.get("regime", ""), "executed": executed,
            "outcome_date": rd or "", "realized_ret": realized,
            "strat_ret": strat_ret, "hit": hit, "log_equity": equity,
            "risk_flags": r.get("risk_flags", ""),
        })

    resolved = [d for d in daily if d["strat_ret"] is not None]
    strat_rets = [d["strat_ret"] for d in resolved]
    hits = [d["hit"] for d in resolved if d["hit"] is not None]

    # Equity curves (normalized to 100), strat vs SPY buy-and-hold over the same window
    strat_curve, bh_curve = [], []
    if resolved:
        s_eq, b_eq = 100.0, 100.0
        for d in resolved:
            s_eq *= (1 + d["strat_ret"])
            b_eq *= (1 + (d["realized_ret"] or 0))
            strat_curve.append(s_eq)
            bh_curve.append(b_eq)
        bh_rets = [d["realized_ret"] for d in resolved]
    else:
        bh_rets = []

    n = len(resolved)
    hit_rate = (sum(hits) / len(hits)) if hits else None

    # Calibration: mean predicted prob (of the predicted direction) vs realized hit rate
    dir_probs = []
    for d in resolved:
        if d["hit"] is None:
            continue
        p = d["prob"] if d["direction"] == "UP" else (1 - d["prob"])
        dir_probs.append(p)
    mean_conf = (sum(dir_probs) / len(dir_probs)) if dir_probs else None

    # Rolling 20d hit rate for drift detection
    rolling_hit = None
    if len(hits) >= 20:
        rolling_hit = sum(hits[-20:]) / 20.0

    expected_hit = wfcv_expected_hit_rate()
    drift_flag = None
    if rolling_hit is not None and expected_hit:
        lb = binom_lower_bound(expected_hit, 20)
        drift_flag = rolling_hit < lb

    # Actual broker equity curve (preferred for the plot when available)
    broker_curve = alpaca_portfolio_history(env)
    if broker_curve is None:
        errors.append("Alpaca portfolio history unavailable (keys missing or API error) - using trade-log equity")

    # Write per-day CSV for the R plot
    os.makedirs(os.path.dirname(OUT_DAILY), exist_ok=True)
    with open(OUT_DAILY, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "outcome_date", "direction", "prob", "kelly", "regime",
                    "executed", "realized_ret", "strat_ret", "hit",
                    "strat_equity", "bh_equity", "broker_equity"])
        broker_map = dict(broker_curve) if broker_curve else {}
        for i, d in enumerate(daily):
            idx = None
            if d["strat_ret"] is not None:
                idx = resolved.index(d)
            w.writerow([
                d["date"], d["outcome_date"], d["direction"],
                f'{d["prob"]:.4f}', f'{d["kelly"]:.4f}', d["regime"],
                d["executed"],
                "" if d["realized_ret"] is None else f'{d["realized_ret"]:.6f}',
                "" if d["strat_ret"] is None else f'{d["strat_ret"]:.6f}',
                "" if d["hit"] is None else d["hit"],
                "" if idx is None else f"{strat_curve[idx]:.4f}",
                "" if idx is None else f"{bh_curve[idx]:.4f}",
                broker_map.get(d["outcome_date"] or d["date"], ""),
            ])

    summary = {
        "generated":        datetime.datetime.now().isoformat(timespec="seconds"),
        "log_exists":       os.path.exists(TRADE_LOG),
        "days_logged":      len(daily),
        "days_resolved":    n,
        "days_pending":     len(daily) - n,
        "min_days_note":    (f"need {MIN_DAYS_MEANINGFUL}+ resolved days for meaningful stats"
                             if n < MIN_DAYS_MEANINGFUL else ""),
        "hit_rate":         round(hit_rate, 4) if hit_rate is not None else None,
        "mean_confidence":  round(mean_conf, 4) if mean_conf is not None else None,
        "calibration_gap":  (round(hit_rate - mean_conf, 4)
                             if hit_rate is not None and mean_conf is not None else None),
        "live_sharpe":      round(sharpe(strat_rets), 3) if sharpe(strat_rets) is not None else None,
        "bh_sharpe":        round(sharpe(bh_rets), 3) if sharpe(bh_rets) is not None else None,
        "wfcv_sharpe":      WFCV_SHARPE,
        "expected_hit":     round(expected_hit, 4) if expected_hit else None,
        "rolling_hit_20d":  round(rolling_hit, 4) if rolling_hit is not None else None,
        "drift_flag":       drift_flag,
        "total_return_pct": (round(strat_curve[-1] - 100, 2) if strat_curve else None),
        "bh_return_pct":    (round(bh_curve[-1] - 100, 2) if bh_curve else None),
        "max_drawdown_pct": (round(max_drawdown(strat_curve) * 100, 2) if strat_curve else None),
        "days_in_market":   sum(1 for d in resolved if d["kelly"] > 0),
        "avg_kelly":        (round(sum(d["kelly"] for d in resolved) / n, 4) if n else None),
        "executed_trades":  sum(1 for d in daily if d["executed"]),
        "first_date":       daily[0]["date"] if daily else None,
        "last_date":        daily[-1]["date"] if daily else None,
        "broker_history":   bool(broker_curve),
        "errors":           errors,
    }

    with open(OUT_JSON, "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
