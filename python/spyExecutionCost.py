#!/usr/bin/env python3
"""
spyExecutionCost.py
===================
Empirical SPY execution-cost model from data already on disk, replacing the
blanket 1bp (or 0.5bp) per-side assumption used in every intraday backtest
in this repo.

Sources
  1. NBBO minute aggregates (fetchQuotesDaily.py):
     outputs/markets/quotes/SPY_{YEAR}_micro.csv  -> spread_bps_mean per minute.
     Half the quoted spread is the cost of crossing for a small market order.
  2. Autopilot fills (alpaca_fills_cache.csv, SPY FILL rows) vs the 1-min
     bar VWAP of the fill minute (outputs/markets/intraday/SPY_{YEAR}_1min.csv):
     realized slippage of real paper market orders, signed so + = cost.
  3. Autopilot implementation shortfall: tradeSpy runs at 16:15 so its market
     orders fill at the NEXT open. Cost vs the signal's close price = the
     overnight gap on the shares traded, signed by side.

Outputs
  outputs/markets/spy_cost_model.json       cost table by time-of-day bucket
  outputs/markets/spy_cost_model.csv        same, long form
  outputs/markets/spy_cost_fills.csv        per-fill realized slippage
  outputs/markets/spy_cost_model.png        navy chart (r/spyExecutionCostPlot.R)

Usage
  venv/bin/python3 python/spyExecutionCost.py
  from spyExecutionCost import cost_bps; cost_bps("15:45")  -> per-side bps (median)
"""
import os
import sys
import json
import glob
import subprocess

import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/discordBot")
QUOTES = sorted(glob.glob(os.path.join(BASE, "outputs/markets/quotes/SPY_*_micro.csv")))
BARS = sorted(glob.glob(os.path.join(BASE, "outputs/markets/intraday/SPY_*_1min.csv")))
FILLS = os.path.join(BASE, "outputs/markets/alpaca_fills_cache.csv")
TRADE_LOG = os.path.join(BASE, "outputs/markets/spy_trade_log.csv")
OUT_JSON = os.path.join(BASE, "outputs/markets/spy_cost_model.json")
OUT_CSV = os.path.join(BASE, "outputs/markets/spy_cost_model.csv")
OUT_FILLS = os.path.join(BASE, "outputs/markets/spy_cost_fills.csv")
OUT_PNG = os.path.join(BASE, "outputs/markets/spy_cost_model.png")
R_PLOT = os.path.join(BASE, "r/spyExecutionCostPlot.R")

BUCKETS = [("09:30", "09:35", "open 0-5m"), ("09:35", "10:00", "open 5-30m"), ("10:00", "12:00", "morning"),
           ("12:00", "14:00", "midday"), ("14:00", "15:30", "afternoon"), ("15:30", "15:55", "last 30m"),
           ("15:55", "16:00", "last 5m")]


def _bucket(hhmm):
    for lo, hi, name in BUCKETS:
        if lo <= hhmm < hi:
            return name
    return None


def load_quotes():
    df = pd.concat([pd.read_csv(f) for f in QUOTES], ignore_index=True)
    df["t"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/New_York")
    df["hhmm"] = df["t"].dt.strftime("%H:%M")
    df["bucket"] = df["hhmm"].map(_bucket)
    df = df.dropna(subset=["bucket", "spread_bps_mean"])
    df["half_spread_bps"] = df["spread_bps_mean"] / 2.0
    return df


def load_bars_for(dates):
    years = sorted({d[:4] for d in dates})
    frames = []
    for y in years:
        p = os.path.join(BASE, f"outputs/markets/intraday/SPY_{y}_1min.csv")
        if os.path.exists(p):
            b = pd.read_csv(p, usecols=["date", "open", "close", "vw", "volume"])
            b["t"] = pd.to_datetime(b["date"], utc=True).dt.tz_convert("America/New_York")
            b["day"] = b["t"].dt.strftime("%Y-%m-%d")
            frames.append(b[b["day"].isin(dates)])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fills_analysis():
    f = pd.read_csv(FILLS)
    f = f[(f["symbol"] == "SPY") & (f.get("activity_type", "FILL").fillna("FILL") == "FILL")].copy()
    f["t"] = pd.to_datetime(f["transaction_time"], utc=True).dt.tz_convert("America/New_York")
    f["day"] = f["t"].dt.strftime("%Y-%m-%d")
    f["minute"] = f["t"].dt.floor("min")
    f["price"] = f["price"].astype(float)
    f["qty"] = f["qty"].astype(float)
    bars = load_bars_for(sorted(f["day"].unique()))
    if bars.empty:
        return f.iloc[0:0], {}
    bars["minute"] = bars["t"].dt.floor("min")
    # regular-session open print per day (first bar at/after 09:30)
    reg = bars[bars["t"].dt.strftime("%H:%M") >= "09:30"]
    open_px = reg.sort_values("t").groupby("day").first()["open"]
    m = f.merge(bars[["minute", "vw", "close"]], on="minute", how="left")
    sign = np.where(m["side"] == "buy", 1.0, -1.0)
    m["slip_vs_bar_vwap_bps"] = sign * (m["price"] / m["vw"] - 1) * 1e4
    m["slip_vs_open_print_bps"] = sign * (m["price"] / m["day"].map(open_px) - 1) * 1e4
    # implementation shortfall vs the signal's close price the day before
    tl = pd.read_csv(TRADE_LOG).drop_duplicates("date", keep="last")
    tl["date"] = pd.to_datetime(tl["date"])
    tl = tl.sort_values("date")
    prev_close = {}
    for d in m["day"].unique():
        prior = tl[tl["date"] < pd.Timestamp(d)]
        if not prior.empty:
            prev_close[d] = float(prior.iloc[-1]["spy_price"])
    m["signal_close"] = m["day"].map(prev_close)
    m["shortfall_vs_signal_close_bps"] = sign * (m["price"] / m["signal_close"] - 1) * 1e4
    m["hhmm"] = m["t"].dt.strftime("%H:%M")
    m["bucket"] = m["hhmm"].map(_bucket)
    m["dollar"] = m["price"] * m["qty"]
    out = m[["t", "day", "side", "qty", "price", "vw", "slip_vs_bar_vwap_bps", "slip_vs_open_print_bps",
             "signal_close", "shortfall_vs_signal_close_bps", "bucket", "dollar"]]
    out.to_csv(OUT_FILLS, index=False)

    def w(col):
        s = out.dropna(subset=[col])
        return {"n": int(len(s)), "mean_bps": round(float(np.average(s[col], weights=s["dollar"])), 2),
                "median_bps": round(float(s[col].median()), 2),
                "p75_bps": round(float(s[col].quantile(0.75)), 2)}
    summ = {"fills": int(len(out)), "first": str(out["t"].min())[:10], "last": str(out["t"].max())[:10],
            "share_in_first_5min": round(float((m["hhmm"] < "09:35").mean()), 3),
            "slip_vs_bar_vwap": w("slip_vs_bar_vwap_bps"),
            "slip_vs_open_print": w("slip_vs_open_print_bps"),
            "shortfall_vs_signal_close": w("shortfall_vs_signal_close_bps")}
    return out, summ


def build():
    q = load_quotes()
    rows = []
    for lo, hi, name in BUCKETS:
        s = q[q["bucket"] == name]["half_spread_bps"]
        if len(s) == 0:
            continue
        rows.append({"bucket": name, "window": f"{lo}-{hi}", "n_minutes": int(len(s)),
                     "half_spread_median_bps": round(float(s.median()), 3),
                     "half_spread_p75_bps": round(float(s.quantile(0.75)), 3),
                     "half_spread_p95_bps": round(float(s.quantile(0.95)), 3)})
    table = pd.DataFrame(rows)
    allq = q["half_spread_bps"]
    # vol-regime split: by day's mean spread tercile as a proxy for regime
    day_mean = q.groupby(q["t"].dt.date)["half_spread_bps"].mean()
    hi_days = set(day_mean[day_mean >= day_mean.quantile(2 / 3)].index)
    q["hi_vol_day"] = q["t"].dt.date.isin(hi_days)
    regime = q.groupby("hi_vol_day")["half_spread_bps"].median().round(3).to_dict()

    fills, fsum = fills_analysis()

    # recommended per-side cost for a SMALL market order (no impact at <100 shares):
    #   median half-spread by bucket + a slippage allowance equal to the realized
    #   fill slippage vs bar VWAP (dollar-weighted mean, floored at 0)
    slip_allow = max(0.0, fsum.get("slip_vs_bar_vwap", {}).get("mean_bps", 0.0)) if fsum else 0.0
    table["recommended_per_side_bps"] = (table["half_spread_median_bps"] + slip_allow).round(3)
    table["conservative_per_side_bps"] = (table["half_spread_p75_bps"] + slip_allow).round(3)
    table.to_csv(OUT_CSV, index=False)

    model = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "quotes_span": [str(q["t"].min())[:10], str(q["t"].max())[:10]],
        "quote_minutes": int(len(q)),
        "overall_half_spread_bps": {"median": round(float(allq.median()), 3), "p75": round(float(allq.quantile(0.75)), 3),
                                    "p95": round(float(allq.quantile(0.95)), 3)},
        "half_spread_by_day_vol_tercile": {"low_mid_vol_days": regime.get(False), "high_vol_days": regime.get(True)},
        "slippage_allowance_bps": round(slip_allow, 3),
        "buckets": {r["bucket"]: r for r in table.to_dict("records")},
        "autopilot_fills": fsum,
        "legacy_assumptions_bps_per_side": {"swing_backtests": 0.5, "structural_studies": 1.0},
        "note": ("Per-side cost for a small SPY market order = median half-spread in that time bucket + realized "
                 "slippage allowance. Multiply by 2 for a round trip. Does not include the overnight gap that the "
                 "autopilot pays by filling at the next open (see autopilot_fills.shortfall_vs_signal_close)."),
    }
    with open(OUT_JSON, "w") as f:
        json.dump(model, f, indent=2, default=str)
    return model, table


def cost_bps(hhmm, conservative=False):
    """Per-side cost in bps for a small SPY market order at time hhmm ('HH:MM' ET)."""
    with open(OUT_JSON) as f:
        m = json.load(f)
    b = _bucket(hhmm)
    if b is None or b not in m["buckets"]:
        return m["overall_half_spread_bps"]["p75" if conservative else "median"] + m["slippage_allowance_bps"]
    return m["buckets"][b]["conservative_per_side_bps" if conservative else "recommended_per_side_bps"]


def main():
    model, table = build()
    print(table.to_string(index=False))
    print()
    print(json.dumps({k: v for k, v in model.items() if k not in ("buckets",)}, indent=2, default=str))
    if os.path.exists(R_PLOT):
        r = subprocess.run(["Rscript", R_PLOT], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
