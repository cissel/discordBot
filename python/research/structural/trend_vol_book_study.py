#!/usr/bin/env python3
"""
trend_vol_book_study.py  (pre-registered, Oct 19 2026)
=======================================================
Validates the rule that went live in tradeSpy on Oct 20 2026 and checks it is
not a knife-edge. SPY 2016-06 -> 2026-10 (first 200 rows warm the MA), daily
returns from outputs/markets/cache/SPY.csv, vol-regime scalar from
spyVolRegime.scalar_series (as-of, lagged inside that function). Signals use
yesterday's close vs MA and yesterday's scalar; applied to today's return.
Cost 0.25bp per unit of |exposure change| (measured SPY round trip).

 T1 live combination
    arms: always-long; always-long x scalar; 200dma; 200dma x scalar (LIVE);
          each also with the 10% rebalance band used live.
    PASS: 200dma x scalar has higher Sharpe AND smaller max drawdown than plain
    200dma in BOTH halves (H1 < 2021-07-01, H2 after), and its annual return is
    not more than 3 points below plain 200dma pooled.
 T2 knife-edge
    MA length 100/150/200/250, with and without a 1% hysteresis band (enter above
    MA x 1.01, exit below MA x 0.99), all x scalar.
    PASS: every variant's pooled Sharpe within 0.15 of the 200-day no-band version
    AND every variant's max drawdown smaller than always-long.
 C1 VRP by trend state
    short 30d variance swap (vrp_regime_study track A) cell mean above vs below
    the 200dma, also crossed with the gate (not GEX q1, not inverted).
    PASS (predicted sign: above > below): NW t >= 2 and same sign both halves.

Out: outputs/research/structural/trend_vol_book_results.csv, _summary.md,
     trend_vol_book.png (r/trendVolBookPlot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.expanduser("~/discordBot/python"))
sys.path.insert(0, os.path.dirname(__file__))
from spyVolRegime import scalar_series, SPY_CSV  # noqa: E402
from vrp_regime_study import load as vrp_load, track_a, nw_t  # noqa: E402

OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
COST = 0.25e-4
BASE = 1.0           # unit exposure; live uses 0.25 x this, Sharpe is scale-free
BAND = 0.10


def stats(pnl, exposure, dates):
    dates = pd.DatetimeIndex(pd.Series(dates).values)
    pnl = pd.Series(np.asarray(pnl), index=dates); ex = pd.Series(np.asarray(exposure), index=dates)
    eq = (1 + pnl).cumprod(); dd = (eq / eq.cummax() - 1).min()
    chg = ex.diff().abs().fillna(0)
    out = {"ann_ret": pnl.mean() * 252 * 100, "sharpe": pnl.mean() / pnl.std() * np.sqrt(252) if pnl.std() > 0 else np.nan,
           "max_dd": dd * 100, "in_mkt": (ex > 0).mean(), "mean_exp": ex.mean(), "trades_per_yr": (chg > 1e-9).sum() / (len(pnl) / 252)}
    for h, m in [("H1", np.asarray(dates < pd.Timestamp("2021-07-01"))), ("H2", np.asarray(dates >= pd.Timestamp("2021-07-01")))]:
        p = pnl[m]; e = (1 + p).cumprod()
        out[f"sharpe_{h}"] = p.mean() / p.std() * np.sqrt(252) if p.std() > 0 else np.nan
        out[f"max_dd_{h}"] = (e / e.cummax() - 1).min() * 100
    return {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in out.items()}


def run_rule(r, idx, ma, scalar, use_trend=True, use_scalar=True, band=0.0, rebal=0.0):
    """exposure decided on t-1 info, applied to r_t."""
    n = len(r); ex = np.zeros(n); state = 0
    for t in range(1, n):
        if use_trend:
            if np.isnan(ma[t - 1]):
                ex[t] = 0.0; continue
            if state == 0 and idx[t - 1] > ma[t - 1] * (1 + band):
                state = 1
            elif state == 1 and idx[t - 1] < ma[t - 1] * (1 - band):
                state = 0
            on = state
        else:
            on = 1
        target = BASE * (scalar[t - 1] if use_scalar else 1.0) * on
        if rebal > 0 and ex[t - 1] > 0 and target > 0 and abs(target - ex[t - 1]) < rebal * target:
            target = ex[t - 1]
        ex[t] = target
    cost = np.abs(np.diff(ex, prepend=0.0)) * COST
    return ex * r - cost, ex


def main():
    spy = pd.read_csv(SPY_CSV, parse_dates=["date"]).dropna().sort_values("date")
    spy = spy[spy["date"] < pd.Timestamp.today().normalize()]       # drop provisional today
    sc = scalar_series(spy["date"])
    df = spy.merge(sc[["date", "scalar"]], on="date", how="left")
    df["scalar"] = df["scalar"].fillna(1.0)
    r = df["SPY_ret"].values; dates = df["date"]
    idx = np.cumprod(1 + r)
    s = df["scalar"].values
    rows = []
    mas = {L: pd.Series(idx).rolling(L).mean().values for L in (100, 150, 200, 250)}
    arms = [("always-long", dict(use_trend=False, use_scalar=False)), ("always-long x scalar", dict(use_trend=False, use_scalar=True)),
            ("200dma", dict(use_trend=True, use_scalar=False)), ("200dma x scalar", dict(use_trend=True, use_scalar=True)),
            ("200dma x scalar + 10% band (LIVE)", dict(use_trend=True, use_scalar=True, rebal=BAND)),
            ("200dma + 10% band", dict(use_trend=True, use_scalar=False, rebal=BAND))]
    # evaluate only where the 250-day MA exists so all arms share the same window
    valid = ~np.isnan(mas[250])
    series = {}
    for name, kw in arms:
        pnl, ex = run_rule(r, idx, mas[200], s, **kw)
        st = stats(pnl[valid], ex[valid], dates[valid]); st.update({"test": "T1", "arm": name}); rows.append(st)
        series[name] = pd.Series(pnl, index=dates)[valid]
    for L in (100, 150, 200, 250):
        for band in (0.0, 0.01):
            pnl, ex = run_rule(r, idx, mas[L], s, use_trend=True, use_scalar=True, band=band)
            st = stats(pnl[valid], ex[valid], dates[valid]); st.update({"test": "T2", "arm": f"{L}dma{' +1% hyst' if band else ''} x scalar"}); rows.append(st)
    res = pd.DataFrame(rows)
    # C1
    d = vrp_load(); _, _, a = track_a(d)
    spy_idx = pd.Series(idx, index=dates); ma200 = pd.Series(mas[200], index=dates)
    above = (spy_idx > ma200).shift(1).reindex(a.index).fillna(False).astype(bool)
    def cell(mask, label, sign):
        sm = a["pnl_vol"]; m = mask.astype(bool)
        dser = (sm * np.where(m, 1.0, 0.0) / max(m.mean(), 1e-9)) - (sm * np.where(~m, 1.0, 0.0) / max((~m).mean(), 1e-9))
        h1 = a["half"] == "H1"
        o1 = sm[m & h1].mean() - sm[~m & h1].mean(); o2 = sm[m & ~h1].mean() - sm[~m & ~h1].mean()
        return {"test": "C1", "arm": label, "n": int(m.sum()), "mean_in": round(sm[m].mean(), 3), "mean_out": round(sm[~m].mean(), 3),
                "nw_t": round(nw_t(dser.values), 2), "diff_H1": round(o1, 3), "diff_H2": round(o2, 3),
                "pass": bool(abs(nw_t(dser.values)) >= 2 and np.sign(o1) == sign and np.sign(o2) == sign)}
    c1 = [cell(above, "above 200dma (all days)", +1), cell(above & a["gate_both"], "above 200dma AND gated", +1),
          cell(~above & a["gate_both"], "below 200dma AND gated", -1)]
    c1df = pd.DataFrame(c1)
    full = pd.concat([res, c1df], ignore_index=True)
    full.to_csv(os.path.join(OUT, "trend_vol_book_results.csv"), index=False)
    pd.DataFrame(series).to_csv(os.path.join(OUT, "trend_vol_book_daily.csv"))
    pd.set_option("display.width", 240)
    print(f"window {dates[valid].min().date()} -> {dates[valid].max().date()}  n={valid.sum()}")
    print(res[["test", "arm", "ann_ret", "sharpe", "max_dd", "in_mkt", "mean_exp", "trades_per_yr", "sharpe_H1", "sharpe_H2", "max_dd_H1", "max_dd_H2"]].to_string(index=False))
    print(c1df.to_string(index=False))
    g = lambda n: res[res.arm == n].iloc[0]
    live, plain, al = g("200dma x scalar"), g("200dma"), g("always-long")
    t1 = all([live.sharpe_H1 > plain.sharpe_H1, live.sharpe_H2 > plain.sharpe_H2, live.max_dd_H1 > plain.max_dd_H1,
              live.max_dd_H2 > plain.max_dd_H2, live.ann_ret >= plain.ann_ret - 3])
    t2v = res[res.test == "T2"]; ref = t2v[t2v.arm == "200dma x scalar"].iloc[0].sharpe
    t2 = bool(((t2v.sharpe - ref).abs() <= 0.15).all() and (t2v.max_dd > al.max_dd).all())
    verdict = {"T1": "PASS" if t1 else "FAIL", "T2": "PASS" if t2 else "FAIL",
               "C1": "PASS" if c1df.iloc[0]["pass"] else "FAIL"}
    print("\nVERDICTS", verdict)
    with open(os.path.join(OUT, "trend_vol_book_summary.md"), "w") as f:
        f.write("# Trend/vol book validation (T1 live combo, T2 knife-edge, C1 VRP by trend)\n\n")
        for k, v in verdict.items():
            f.write(f"- {k}: {v}\n")
        f.write("\n" + res.to_markdown(index=False) + "\n\n" + c1df.to_markdown(index=False) + "\n")
    rp = os.path.expanduser("~/discordBot/r/trendVolBookPlot.R")
    if os.path.exists(rp):
        rr = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if rr.returncode:
            print(rr.stderr[-600:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
