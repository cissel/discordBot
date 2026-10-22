#!/usr/bin/env python3
"""
spyVolRegime.py
===============
Volatility-regime position scalar for the SPY autopilot, from the Oct 2026
structural-flow study (outputs/research/structural/gex_dix_summary.md):

  * GEX trailing-252-day quintile is a strong, stable predictor of REALIZED
    vol relative to VIX-implied: RV5/VIX = 0.89 (q0) ... 0.60 (q4), NW t 9.1,
    identical spread in both halves of 2011-2026.
  * VIX9D > VIX3M inversion: RV5/VIX = 1.01 vs 0.68 (t 6.3). The one state
    where realized reaches implied.

Neither predicts direction (long/flat on GEX lost Sharpe), so they are used
only to scale the Kelly size, never to flip it:

  expected_vol = VIX_prev * ratio[gex_quintile]      (ratio -> 1.01 if inverted)
  scalar       = clip(TARGET_VOL / expected_vol, MIN, MAX)
  size_eff     = position_size_kelly * scalar

All inputs are from the prior trading day or earlier (GEX publishes with a
1-2 day lag; the latest row <= t-1 is used). Nothing here looks forward.

Usage
  venv/bin/python3 python/spyVolRegime.py                # today's scalar as JSON
  venv/bin/python3 python/spyVolRegime.py --date 2026-10-16
  venv/bin/python3 python/spyVolRegime.py --backtest     # 2016-2026 study, CSV + navy PNG

Backtest writes outputs/markets/spy_vol_regime_backtest.csv and
outputs/markets/spy_vol_regime_backtest.png (r/spyVolRegimePlot.R).
"""
import os
import sys
import json
import math
import argparse
import datetime as dt
import subprocess

import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/discordBot")
GEX_CSV = os.path.join(BASE, "outputs/markets/cache/spy_gex_daily.csv")
VIX_CSV = os.path.join(BASE, "outputs/markets/cache/VIX.csv")
TERM_CSV = os.path.join(BASE, "outputs/markets/cache/vix_term_history.csv")
SPY_CSV = os.path.join(BASE, "outputs/markets/cache/SPY.csv")
TRADE_LOG = os.path.join(BASE, "outputs/markets/spy_trade_log.csv")
OUT_CSV = os.path.join(BASE, "outputs/markets/spy_vol_regime_backtest.csv")
OUT_PNG = os.path.join(BASE, "outputs/markets/spy_vol_regime_backtest.png")
R_PLOT = os.path.join(BASE, "r/spyVolRegimePlot.R")

# RV5 / VIX by GEX trailing-252 quintile, full sample 2012-2026 (gex_dix_results.csv, B_GEX_quintile ratio5)
RATIO_BY_Q = {0: 0.887, 1: 0.786, 2: 0.725, 3: 0.685, 4: 0.598}
RATIO_INVERTED = 1.01          # vix9d > vix3m
RANK_WINDOW = 252
TARGET_VOL = 0.11              # annualized expected-realized vol at which scalar = 1.0 (~ median expected vol 2016-2026)
SCALAR_MIN, SCALAR_MAX = 0.35, 1.25


def _load():
    gex = pd.read_csv(GEX_CSV, parse_dates=["date"]).sort_values("date")
    gex["gex_rank"] = (gex["gex"].rolling(RANK_WINDOW, min_periods=126)
                       .apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True))
    gex["gex_q"] = np.floor(gex["gex_rank"] * 5).clip(0, 4)
    vix = pd.read_csv(VIX_CSV, parse_dates=["date"]).rename(columns={"VIXCLS_val": "vix"})
    vix = vix.dropna(subset=["vix"]).sort_values("date")
    term = pd.read_csv(TERM_CSV, parse_dates=["date"]).sort_values("date")
    term["inverted"] = (term["vix9d"] > term["vix3m"]).astype(float)
    return gex[["date", "gex", "gex_q"]], vix[["date", "vix"]], term[["date", "vix9d", "vix3m", "inverted"]]


def scalar_series(dates):
    """Causal daily scalar for each date in `dates` (uses info strictly before each date)."""
    gex, vix, term = _load()
    df = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates)).sort_values().values})
    # as-of merge on t-1: shift each source so the row available on date t is the latest < t
    def asof(src):
        s = src.copy()
        s["avail"] = s["date"] + pd.Timedelta(days=1)
        return pd.merge_asof(df, s.drop(columns="date"), left_on="date", right_on="avail", direction="backward").drop(columns="avail")
    out = asof(gex)
    out["vix"] = asof(vix)["vix"].values
    t = asof(term)
    out["vix9d"], out["vix3m"], out["inverted"] = t["vix9d"].values, t["vix3m"].values, t["inverted"].values
    out["ratio"] = out["gex_q"].map(RATIO_BY_Q)
    out.loc[out["inverted"] == 1.0, "ratio"] = np.maximum(out.loc[out["inverted"] == 1.0, "ratio"], RATIO_INVERTED)
    out["expected_vol"] = out["vix"] / 100.0 * out["ratio"]
    out["scalar"] = (TARGET_VOL / out["expected_vol"]).clip(SCALAR_MIN, SCALAR_MAX)
    out.loc[out["expected_vol"].isna(), "scalar"] = 1.0   # no data -> neutral
    return out


def current_scalar(date=None):
    d = pd.Timestamp(date or dt.date.today())
    row = scalar_series([d]).iloc[0]
    q = None if pd.isna(row["gex_q"]) else int(row["gex_q"])
    return {
        "date": d.date().isoformat(),
        "gex": None if pd.isna(row["gex"]) else float(row["gex"]),
        "gex_quintile": q,
        "vix": None if pd.isna(row["vix"]) else float(row["vix"]),
        "vix9d": None if pd.isna(row["vix9d"]) else float(row["vix9d"]),
        "vix3m": None if pd.isna(row["vix3m"]) else float(row["vix3m"]),
        "term_inverted": bool(row["inverted"] == 1.0) if not pd.isna(row["inverted"]) else None,
        "rv_iv_ratio": None if pd.isna(row["ratio"]) else float(row["ratio"]),
        "expected_vol": None if pd.isna(row["expected_vol"]) else round(float(row["expected_vol"]), 4),
        "scalar": round(float(row["scalar"]), 3),
        "label": (f"vol_scalar={row['scalar']:.2f} (GEX q{q}" + (", term inverted" if row["inverted"] == 1.0 else "")
                  + f", exp vol {row['expected_vol']*100:.1f}%)") if q is not None else "vol_scalar=1.00 (no regime data)",
    }


# ── backtest ────────────────────────────────────────────────────────────────

def _stats(r, cost_per_side=0.0001, exposure=None):
    r = np.asarray(r, float)
    if exposure is not None:
        turn = np.abs(np.diff(np.concatenate([[0.0], exposure])))
        r = r - turn * cost_per_side
    mu, sd = r.mean(), r.std(ddof=1)
    eq = np.cumprod(1 + r)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return {"ann_ret": round(mu * 252 * 100, 2), "ann_vol": round(sd * math.sqrt(252) * 100, 2),
            "sharpe": round(mu / sd * math.sqrt(252), 3) if sd > 0 else None, "max_dd": round(dd * 100, 2)}


def backtest():
    spy = pd.read_csv(SPY_CSV, parse_dates=["date"]).dropna().sort_values("date")
    sc = scalar_series(spy["date"])
    df = spy.merge(sc[["date", "scalar", "gex_q", "inverted", "expected_vol"]], on="date")
    df = df.dropna(subset=["expected_vol"]).reset_index(drop=True)
    r = df["SPY_ret"].values
    s = df["scalar"].values
    base_exp = np.full(len(r), 0.10)           # constant 10% exposure proxy for the autopilot's typical size
    rows = []
    for label, exp in [("constant exposure", base_exp), ("vol-regime scaled", base_exp * s)]:
        strat = exp * r
        for per, mask in [("full", np.ones(len(r), bool)),
                          ("2016-2020", (df["date"] < "2021-01-01").values),
                          ("2021-2026", (df["date"] >= "2021-01-01").values)]:
            st = _stats(strat[mask], exposure=exp[mask])
            rows.append({"leg": label, "period": per, **st, "avg_exposure": round(exp[mask].mean(), 4),
                         "n": int(mask.sum())})
    # realized-vol check: did the scalar actually equalize risk?
    df["scaled_ret"] = s * r
    for q in range(5):
        m = df["gex_q"] == q
        rows.append({"leg": f"realized vol by GEX q{q}", "period": "full",
                     "ann_vol": round(r[m].std() * math.sqrt(252) * 100, 2),
                     "ann_ret": round(df.loc[m, "scaled_ret"].std() * math.sqrt(252) * 100, 2),  # scaled vol stored in ann_ret slot
                     "sharpe": None, "max_dd": None, "avg_exposure": round(s[m].mean(), 3), "n": int(m.sum())})
    # live trade-log overlay: kelly size * ret with and without scalar
    live = None
    if os.path.exists(TRADE_LOG):
        tl = pd.read_csv(TRADE_LOG, parse_dates=["date"]).drop_duplicates("date", keep="last").sort_values("date")
        tl = tl.merge(df[["date", "SPY_ret", "scalar"]], on="date", how="inner")
        tl["next_ret"] = tl["SPY_ret"].shift(-1)
        tl = tl.dropna(subset=["next_ret"])
        if len(tl) > 10:
            k = tl["position_size_kelly"].values
            raw = _stats(k * tl["next_ret"].values, exposure=k)
            scl = _stats(k * tl["scalar"].values * tl["next_ret"].values, exposure=k * tl["scalar"].values)
            rows.append({"leg": "live log: kelly only", "period": f"{tl['date'].min().date()}..{tl['date'].max().date()}", **raw,
                         "avg_exposure": round(k.mean(), 4), "n": len(tl)})
            rows.append({"leg": "live log: kelly x scalar", "period": f"{tl['date'].min().date()}..{tl['date'].max().date()}", **scl,
                         "avg_exposure": round((k * tl["scalar"].values).mean(), 4), "n": len(tl)})
            live = tl
    res = pd.DataFrame(rows)
    res.to_csv(OUT_CSV, index=False)
    # daily series for the plot
    daily = df[["date", "SPY_ret", "scalar", "gex_q", "inverted"]].copy()
    daily["eq_const"] = np.cumprod(1 + base_exp * r)
    daily["eq_scaled"] = np.cumprod(1 + base_exp * s * r)
    daily.to_csv(OUT_CSV.replace(".csv", "_daily.csv"), index=False)
    if os.path.exists(R_PLOT):
        subprocess.run(["Rscript", R_PLOT], check=False, capture_output=True, text=True)
    print(res.to_string(index=False))
    print(f"\nscalar distribution: min {s.min():.2f} p25 {np.percentile(s,25):.2f} median {np.median(s):.2f} "
          f"p75 {np.percentile(s,75):.2f} max {s.max():.2f}  (TARGET_VOL={TARGET_VOL}, clip [{SCALAR_MIN},{SCALAR_MAX}])")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None)
    ap.add_argument("--backtest", action="store_true")
    a = ap.parse_args(argv)
    if a.backtest:
        backtest()
        return 0
    print(json.dumps(current_scalar(a.date), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
