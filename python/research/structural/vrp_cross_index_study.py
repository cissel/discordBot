#!/usr/bin/env python3
"""
vrp_cross_index_study.py  (pre-registered, Oct 19 2026)
========================================================
Does the short-variance premium, and the VIX term-structure gate that improved
it on SPY (vrp_regime_study.py), generalise to QQQ (VXN) and IWM (RVX)?

METHOD (identical to Track A of vrp_regime_study.py, one index at a time)
  Each day t: short a synthetic 30-day variance swap at strike K_t = IV_t^2
  (IV in vol points: VIX for SPY, VXN for QQQ, RVX for IWM); settle on the
  realised variance of the next 21 daily log returns (annualised, x1e4 so
  the units are vol-pts^2). P&L per unit vega in vol points:
      pnl_t = (K_t - RV_t) / (2 IV_t)
  Overlapping daily entries. Inference: Newey-West t with 21 lags on the
  daily series (nw_t imported from vrp_regime_study). Monthly Sharpe =
  sample every 21st day, average over the 3 offsets. Worst month = worst
  calendar-month mean of the daily series (zeros on ineligible days).
  Halves: H1 before 2021-07-01, H2 from 2021-07-01.

GATE (one gate only, market-wide): not inverted, i.e. VIX9D <= VIX3M, taken
  from vix_term_history.csv and lagged one trading day. The same SPX gate is
  applied to all three indices. GEX is SPX-only and is not used here.

WINDOW: common dates where all three indices have IV and returns, and the
  term-structure flag exists (2016-06-24 onward), through the last date with
  a complete 21-day forward window. Rows dated today are dropped. SPY is
  recomputed on exactly this window as the control.

PRE-REGISTERED PASS RULES (per index; written before any result was seen)
  H1  unconditional premium is positive:
      (a) full-sample mean vol pts per entry > 0,
      (b) NW t (21 lags) of the unconditional daily P&L >= 2.0,
      (c) unconditional mean > 0 in BOTH H1 and H2.
  H2  the lagged not-inverted gate helps, in both halves:
      for each of H1 and H2 (evaluated within the half):
      (a) gated monthly Sharpe > unconditional monthly Sharpe,
      (b) gated worst calendar-month mean is less negative than the
          unconditional worst calendar-month mean (smaller magnitude).
  PASS    = H1 and H2 both hold.
  PARTIAL = exactly one of H1, H2 holds.
  FAIL    = neither holds.

Also reported (no pass rule): hit rate, eligible fraction, worst single
entry, and the cross-index correlation matrix of daily gated P&L (and
ungated) between SPY, QQQ, IWM. Correlation > 0.9 is flagged plainly.

Outputs
  outputs/research/structural/vrp_cross_index_results.csv   (index x gate x period)
  outputs/research/structural/vrp_cross_index_daily.csv     (date, per-index gated/ungated P&L)
  outputs/research/structural/vrp_cross_index_summary.md
  outputs/research/structural/vrp_cross_index.png           (r/vrpCrossIndexPlot.R)
"""
import os
import sys
import subprocess
import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/jhcv/discordBot/python/research/structural")
from vrp_regime_study import nw_t  # noqa: E402

BASE = os.path.expanduser("~/discordBot")
CACHE = os.path.join(BASE, "outputs/markets/cache")
OUT = os.path.join(BASE, "outputs/research/structural")
H = 21
SPLIT = pd.Timestamp("2021-07-01")
INDICES = {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX"}
GATES = ["gate_none", "gate_term"]


def load_returns(sym):
    if sym == "SPY":
        s = pd.read_csv(os.path.join(CACHE, "SPY.csv"), parse_dates=["date"]).set_index("date")["SPY_ret"]
        return s.astype(float)
    b = pd.read_csv(os.path.join(CACHE, f"{sym}_bars.csv"))
    b["date"] = pd.to_datetime(b["t"], utc=True).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
    c = b.set_index("date")["c"].astype(float).sort_index()
    return c.pct_change().dropna()


def load_iv(name):
    if name == "VIX":
        s = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
        return s.astype(float).dropna()
    df = pd.read_csv(os.path.join(CACHE, f"{name}.csv"))
    df["date"] = pd.to_datetime(df["DATE"])
    return df.set_index("date")["CLOSE"].astype(float).sort_index()


def build():
    today = pd.Timestamp(datetime.date.today())
    vt = pd.read_csv(os.path.join(CACHE, "vix_term_history.csv"), parse_dates=["date"]).set_index("date")
    inv = (vt["vix9d"] > vt["vix3m"]).astype(float)
    panels = {}
    for sym, ivn in INDICES.items():
        r = load_returns(sym); iv = load_iv(ivn)
        d = pd.DataFrame({"ret": r})
        d = d[d.index < today]
        d["iv"] = iv.reindex(d.index, method="ffill")
        lr = np.log1p(d["ret"])
        d["rv_fwd_var"] = (lr.shift(-1) ** 2).rolling(H).mean().shift(-(H - 1)) * 252 * 1e4
        d["rv_fwd"] = np.sqrt(d["rv_fwd_var"])
        d["inverted"] = inv.reindex(d.index, method="ffill").shift(1)
        d["pnl_vol"] = (d["iv"] ** 2 - d["rv_fwd_var"]) / (2 * d["iv"])
        panels[sym] = d
    # common window: every index has iv, pnl and lagged gate
    idx = None
    for sym, d in panels.items():
        ok = d.dropna(subset=["iv", "pnl_vol", "inverted"]).index
        idx = ok if idx is None else idx.intersection(ok)
    idx = idx.sort_values()
    for sym in panels:
        d = panels[sym].loc[idx].copy()
        d["half"] = np.where(d.index < SPLIT, "H1", "H2")
        d["gate_none"] = True
        d["gate_term"] = d["inverted"] == 0
        panels[sym] = d
    return panels


def metrics(a, g, period):
    if period != "full":
        a = a[a["half"] == period]
    s = a["pnl_vol"].where(a[g], 0.0)
    act = a["pnl_vol"][a[g]]
    mo = [s.iloc[k::H] for k in range(3)]
    mo_sharpe = float(np.mean([m.mean() / m.std() * np.sqrt(12) for m in mo if m.std() > 0]))
    cal = s.groupby(s.index.to_period("M")).mean()
    return {"gate": g, "period": period, "n_days": int(a[g].sum()), "eligible_frac": round(float(a[g].mean()), 3),
            "mean_volpts": round(float(act.mean()), 3), "hit_rate": round(float((act > 0).mean()), 3),
            "monthly_sharpe": round(mo_sharpe, 3), "worst_month_volpts": round(float(cal.min()), 2),
            "worst_month": str(cal.idxmin()), "worst_entry_volpts": round(float(act.min()), 1),
            "worst_entry_date": str(act.idxmin().date()), "nw_t": round(nw_t(s.values), 2),
            "mean_rv_over_iv": round(float((a["rv_fwd"][a[g]] / a["iv"][a[g]]).mean()), 3)}


def verdict(res, sym):
    r = res[res["index"] == sym]
    def pick(g, p):
        return r[(r.gate == g) & (r.period == p)].iloc[0]
    u, u1, u2 = pick("gate_none", "full"), pick("gate_none", "H1"), pick("gate_none", "H2")
    g1, g2 = pick("gate_term", "H1"), pick("gate_term", "H2")
    h1 = {"a_mean_pos": bool(u.mean_volpts > 0), "b_nw_t": bool(u.nw_t >= 2.0), "c_both_halves_pos": bool((u1.mean_volpts > 0) and (u2.mean_volpts > 0))}
    h2 = {"H1_sharpe_up": bool(g1.monthly_sharpe > u1.monthly_sharpe), "H1_worst_month_better": bool(abs(g1.worst_month_volpts) < abs(u1.worst_month_volpts)),
          "H2_sharpe_up": bool(g2.monthly_sharpe > u2.monthly_sharpe), "H2_worst_month_better": bool(abs(g2.worst_month_volpts) < abs(u2.worst_month_volpts))}
    ok1, ok2 = all(h1.values()), all(h2.values())
    v = "PASS" if (ok1 and ok2) else ("PARTIAL" if (ok1 or ok2) else "FAIL")
    return v, ok1, h1, ok2, h2


def main():
    panels = build()
    rows = []
    for sym, a in panels.items():
        for g in GATES:
            for p in ["full", "H1", "H2"]:
                m = metrics(a, g, p); m["index"] = sym; m["iv_index"] = INDICES[sym]; rows.append(m)
    res = pd.DataFrame(rows)[["index", "iv_index", "gate", "period", "n_days", "eligible_frac", "mean_volpts", "hit_rate",
                              "monthly_sharpe", "worst_month_volpts", "worst_month", "worst_entry_volpts", "worst_entry_date",
                              "nw_t", "mean_rv_over_iv"]]
    res.to_csv(os.path.join(OUT, "vrp_cross_index_results.csv"), index=False)

    any_sym = next(iter(panels))
    daily = pd.DataFrame(index=panels[any_sym].index)
    daily["inverted"] = panels[any_sym]["inverted"]
    daily["gate_term"] = panels[any_sym]["gate_term"]
    for sym, a in panels.items():
        daily[f"{sym}_iv"] = a["iv"]; daily[f"{sym}_rv_fwd"] = a["rv_fwd"]
        daily[f"{sym}_pnl_ungated"] = a["pnl_vol"]
        daily[f"{sym}_pnl_gated"] = a["pnl_vol"].where(a["gate_term"], 0.0)
    daily.index.name = "date"
    daily.to_csv(os.path.join(OUT, "vrp_cross_index_daily.csv"))

    syms = list(panels)
    cg = daily[[f"{s}_pnl_gated" for s in syms]].corr(); cg.index = syms; cg.columns = syms
    cu = daily[[f"{s}_pnl_ungated" for s in syms]].corr(); cu.index = syms; cu.columns = syms
    # monthly non-overlapping (offset 0) correlation too, since daily series overlap by construction
    mg = daily[[f"{s}_pnl_gated" for s in syms]].iloc[::H].corr(); mg.index = syms; mg.columns = syms

    pd.set_option("display.width", 250)
    span = f"{daily.index.min().date()} -> {daily.index.max().date()}, {len(daily)} days"
    print(f"Common window {span}; H1 < {SPLIT.date()} ({(daily.index < SPLIT).sum()} d), H2 ({(daily.index >= SPLIT).sum()} d)")
    print("\nindex x gate x period (short 30d variance swap, vol pts per unit vega):")
    print(res.to_string(index=False))
    print("\ncorrelation of daily gated P&L:\n" + cg.round(3).to_string())
    print("\ncorrelation of daily ungated P&L:\n" + cu.round(3).to_string())
    print("\ncorrelation of monthly (every 21st day) gated P&L:\n" + mg.round(3).to_string())

    verdicts = {}
    for sym in syms:
        v, ok1, h1, ok2, h2 = verdict(res, sym)
        verdicts[sym] = (v, ok1, h1, ok2, h2)
        print(f"\n{sym} ({INDICES[sym]}): H1 {'ok' if ok1 else 'FAIL'} {h1}\n      H2 {'ok' if ok2 else 'FAIL'} {h2}\n      -> {v}")
    hi = [(a, b, float(cg.loc[a, b])) for i, a in enumerate(syms) for b in syms[i + 1:] if cg.loc[a, b] > 0.9]
    if hi:
        print("\nWARNING daily gated P&L correlation above 0.9: " + ", ".join(f"{a}-{b} {c:.2f}" for a, b, c in hi))

    with open(os.path.join(OUT, "vrp_cross_index_summary.md"), "w") as f:
        f.write("# VRP cross-index study: does the premium and the term gate generalise to QQQ (VXN) and IWM (RVX)?\n\n")
        f.write(f"Common window {span}. Gate = VIX9D <= VIX3M (SPX term structure, lagged one day), applied to all indices.\n\n")
        for sym in syms:
            v, ok1, h1, ok2, h2 = verdicts[sym]
            f.write(f"{sym} ({INDICES[sym]}): **{v}**. H1 {'ok' if ok1 else 'fail'} {h1}. H2 {'ok' if ok2 else 'fail'} {h2}.\n\n")
        if hi:
            f.write("Daily gated P&L correlation above 0.9: " + ", ".join(f"{a}-{b} {c:.2f}" for a, b, c in hi) + "\n\n")
        f.write("## Results\n\n" + res.to_markdown(index=False) + "\n\n")
        f.write("## Correlation of daily gated P&L\n\n" + cg.round(3).to_markdown() + "\n\n")
        f.write("## Correlation of daily ungated P&L\n\n" + cu.round(3).to_markdown() + "\n\n")
        f.write("## Correlation of monthly (every 21st day) gated P&L\n\n" + mg.round(3).to_markdown() + "\n")

    rp = os.path.join(BASE, "r/vrpCrossIndexPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-1200:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
