#!/usr/bin/env python3
"""
vrp_regime_study.py  (pre-registered, Oct 19 2026)
===================================================
Can the two vol effects that survived the structural studies (GEX trailing-252
quintile and VIX9D>VIX3M inversion predict REALIZED vol relative to implied)
be traded directly as a conditional variance-risk-premium (VRP) harvest?

Two tracks, same gates, written before results were seen.

TRACK A  Synthetic 30-day variance swap on SPY (what the options trade IS,
         before instrument frictions). Each day t: short variance at strike
         K_t = (VIX_t/100)^2, settle at t+21 on RV_t = annualised variance of
         the next 21 daily SPY log returns. P&L per unit vega-notional in
         vol points: (K - RV) / (2 * VIX/100). Overlapping daily entries:
         inference uses non-overlapping monthly entries (every 21st trading
         day, three offsets averaged) and NW t with 21 lags on the daily
         series. 2016-2026 (SPY.csv span).
TRACK B  VIX ETP: short VXX daily returns (also long SVXY), 2020-07 to 2026
         (Alpaca IEX history). Position held only on eligible days; 2bp per
         side on position changes. This is the stock-like instrument the
         existing bot stack can already trade.

GATES (all known before the day's close, lagged one day)
  gate_gex   : not GEX quintile 1 (most negative GEX)
  gate_term  : VIX9D <= VIX3M (not inverted)
  gate_both  : both
  gate_both_vix : both AND VIX_prev < 30 (tail-avoidance, pre-specified)

PASS RULE (per track, gate_both vs unconditional):
  (a) gated Sharpe > unconditional Sharpe,
  (b) gated worst month (A) / worst 21-day window (B) loss is smaller in magnitude,
  (c) gated mean P&L > 0 in BOTH halves of the sample,
  (d) Track A: NW t (21 lags) of gated daily P&L >= 2.0.
Reported as PASS only if all hold. Partial passes are reported as such.

Outputs
  outputs/research/structural/vrp_regime_results.csv
  outputs/research/structural/vrp_regime_daily.csv
  outputs/research/structural/vrp_regime_summary.md
  outputs/research/structural/vrp_regime.png  (r/vrpRegimePlot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd
from scipy import stats

BASE = os.path.expanduser("~/discordBot")
CACHE = os.path.join(BASE, "outputs/markets/cache")
OUT = os.path.join(BASE, "outputs/research/structural")
H = 21


def nw_t(x, lags=H):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x); m = x.mean(); u = x - m
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(m / np.sqrt(s / n))


def load():
    spy = pd.read_csv(os.path.join(CACHE, "SPY.csv"), parse_dates=["date"]).set_index("date")["SPY_ret"]
    vix = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
    gex = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"]).set_index("date")["gex"]
    vt = pd.read_csv(os.path.join(CACHE, "vix_term_history.csv"), parse_dates=["date"]).set_index("date")
    d = pd.DataFrame({"spy_ret": spy})
    d["vix"] = vix.reindex(d.index, method="ffill")
    gq = gex.rolling(252, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    d["gex_q"] = (np.floor((gq * 5).clip(0, 4.999)) + 1).reindex(d.index, method="ffill").shift(1)
    inv = (vt["vix9d"] > vt["vix3m"]).astype(float)
    d["inverted"] = inv.reindex(d.index, method="ffill").shift(1)
    d["vix3m"] = vt["vix3m"].reindex(d.index, method="ffill").shift(1)
    d["vix_prev"] = d["vix"].shift(1)
    lr = np.log1p(d["spy_ret"])
    # forward realised vol (annualised %), next 21 trading days, excludes today
    d["rv_fwd"] = lr.shift(-1).rolling(H).std().shift(-(H - 1)) * np.sqrt(252) * 100
    d["rv_fwd_var"] = (lr.shift(-1) ** 2).rolling(H).mean().shift(-(H - 1)) * 252 * 1e4   # in vol-pts^2
    d = d.dropna(subset=["vix", "gex_q", "inverted", "vix_prev"])
    d["half"] = np.where(d.index < pd.Timestamp("2021-07-01"), "H1", "H2")
    # gates
    d["gate_none"] = True
    d["gate_gex"] = d["gex_q"] != 1
    d["gate_term"] = d["inverted"] == 0
    d["gate_both"] = d["gate_gex"] & d["gate_term"]
    d["gate_both_vix"] = d["gate_both"] & (d["vix_prev"] < 30)
    return d


def track_a(d):
    """Short 30d variance swap, vega-notional 1 vol point."""
    K = d["vix"] ** 2                       # strike in vol-pts^2 (VIX^2)
    pnl_var = K - d["rv_fwd_var"]           # variance points
    pnl_vol = pnl_var / (2 * d["vix"])      # vol points per unit vega
    a = d.copy(); a["pnl_vol"] = pnl_vol
    a = a.dropna(subset=["pnl_vol"])
    rows = []
    for g in ["gate_none", "gate_gex", "gate_term", "gate_both", "gate_both_vix"]:
        s = a["pnl_vol"].where(a[g], 0.0)
        act = a["pnl_vol"][a[g]]
        # non-overlapping monthly: average of three offsets
        mo = [s.iloc[k::H] for k in range(3)]
        mo_sharpe = np.mean([m.mean() / m.std() * np.sqrt(12) for m in mo if m.std() > 0])
        # worst month = worst CALENDAR-month mean of daily entries (a constant-vega book that
        # enters every eligible day). The earlier "min over 3 stride offsets" was sampling luck:
        # it skipped the Feb 2020 entries for the gated series and understated the tail.
        cal = s.groupby(s.index.to_period("M")).mean()
        mo_worst = cal.min()
        worst_entry = act.min()
        h1 = act[a.loc[act.index, "half"] == "H1"].mean(); h2 = act[a.loc[act.index, "half"] == "H2"].mean()
        rows.append({"track": "A_varswap", "gate": g, "eligible_frac": round(float(a[g].mean()), 3), "n_days": int(a[g].sum()),
                     "mean_volpts": round(float(act.mean()), 3), "hit_rate": round(float((act > 0).mean()), 3),
                     "monthly_sharpe": round(float(mo_sharpe), 3), "worst_month_volpts": round(float(mo_worst), 2),
                     "worst_entry_volpts": round(float(worst_entry), 1),
                     "nw_t": round(nw_t(s.values), 2), "mean_H1": round(float(h1), 3), "mean_H2": round(float(h2), 3),
                     "mean_rv_over_iv": round(float((a["rv_fwd"][a[g]] / a["vix"][a[g]]).mean()), 3)})
    # per-cell diagnostics
    cells = []
    for q in [1, 2, 3, 4, 5]:
        act = a["pnl_vol"][a["gex_q"] == q]
        cells.append({"cell": f"gex_q{q}", "n": len(act), "mean_volpts": round(act.mean(), 3), "hit": round((act > 0).mean(), 3),
                      "rv_over_iv": round(float((a["rv_fwd"] / a["vix"])[a["gex_q"] == q].mean()), 3)})
    for nm, msk in [("inverted", a["inverted"] == 1), ("normal", a["inverted"] == 0)]:
        act = a["pnl_vol"][msk]
        cells.append({"cell": nm, "n": len(act), "mean_volpts": round(act.mean(), 3), "hit": round((act > 0).mean(), 3),
                      "rv_over_iv": round(float((a["rv_fwd"] / a["vix"])[msk].mean()), 3)})
    return pd.DataFrame(rows), pd.DataFrame(cells), a


def load_etp(sym):
    p = os.path.join(CACHE, f"{sym}_bars.csv")
    b = pd.read_csv(p)
    tcol = "t" if "t" in b.columns else "date"
    b["date"] = pd.to_datetime(b[tcol], utc=True).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
    c = b.set_index("date")["c" if "c" in b.columns else "close"].astype(float)
    return c.pct_change()


def track_b(d, cost_bps=2.0):
    rows = []; series = {}
    for sym, sign in [("VXX", -1.0), ("SVXY", 1.0)]:
        r = load_etp(sym)
        b = d[["gate_none", "gate_gex", "gate_term", "gate_both", "gate_both_vix", "half"]].join(r.rename("r"), how="inner").dropna()
        for g in ["gate_none", "gate_gex", "gate_term", "gate_both", "gate_both_vix"]:
            pos = b[g].astype(float)                    # decided with lagged info, held over day t
            pnl = sign * pos * b["r"] - (pos.diff().abs().fillna(pos) * 2 * cost_bps / 1e4)
            eq = (1 + pnl).cumprod(); dd = (eq / eq.cummax() - 1).min()
            w21 = (1 + pnl).rolling(H).apply(np.prod, raw=True).min() - 1
            h = pnl.groupby(b["half"]).mean()
            rows.append({"track": f"B_{'short_' if sign < 0 else 'long_'}{sym}", "gate": g, "eligible_frac": round(float(pos.mean()), 3),
                         "n_days": int(pos.sum()), "ann_ret": round(float(pnl.mean() * 252 * 100), 2),
                         "sharpe": round(float(pnl.mean() / pnl.std() * np.sqrt(252)), 3) if pnl.std() > 0 else np.nan,
                         "max_dd": round(float(dd * 100), 1), "worst_21d": round(float(w21 * 100), 1),
                         "worst_day": round(float(pnl.min() * 100), 1), "mean_H1": round(float(h.get("H1", np.nan) * 1e4), 2),
                         "mean_H2": round(float(h.get("H2", np.nan) * 1e4), 2), "span": f"{b.index.min().date()}..{b.index.max().date()}"})
            series[(sym, g)] = pnl
    return pd.DataFrame(rows), series


def main():
    d = load()
    ra, cells, a = track_a(d)
    rb, sb = track_b(d)
    res = pd.concat([ra, rb], ignore_index=True)
    res.to_csv(os.path.join(OUT, "vrp_regime_results.csv"), index=False)
    cells.to_csv(os.path.join(OUT, "vrp_regime_cells.csv"), index=False)
    daily = pd.DataFrame({"date": a.index, "vix": a["vix"], "rv_fwd": a["rv_fwd"], "gex_q": a["gex_q"], "inverted": a["inverted"],
                          "varswap_pnl_vol": a["pnl_vol"], "gate_both": a["gate_both"]}).set_index("date")
    for (sym, g), s in sb.items():
        if g in ("gate_none", "gate_both"):
            daily[f"{sym}_{g}"] = s
    daily.to_csv(os.path.join(OUT, "vrp_regime_daily.csv"))
    pd.set_option("display.width", 220)
    print(f"Track A span {a.index.min().date()} -> {a.index.max().date()}, {len(a)} days")
    print("\nTRACK A  short 30d variance swap, vol points per unit vega:")
    print(ra.drop(columns=["track"]).to_string(index=False))
    print("\n  cells:"); print(cells.to_string(index=False))
    print("\nTRACK B  VIX ETPs (2bp/side on changes):")
    print(rb.drop(columns=["track"]).assign(track=rb["track"]).to_string(index=False))

    # pass rules
    def pr_a():
        u = ra[ra.gate == "gate_none"].iloc[0]; g = ra[ra.gate == "gate_both"].iloc[0]
        c = {"a_sharpe": g.monthly_sharpe > u.monthly_sharpe, "b_worst": abs(g.worst_month_volpts) < abs(u.worst_month_volpts),
             "c_halves": (g.mean_H1 > 0) and (g.mean_H2 > 0), "d_nw_t": g.nw_t >= 2.0}
        return c, all(c.values())
    def pr_b(track):
        t = rb[rb.track == track]; u = t[t.gate == "gate_none"].iloc[0]; g = t[t.gate == "gate_both"].iloc[0]
        c = {"a_sharpe": g.sharpe > u.sharpe, "b_worst": abs(g.worst_21d) < abs(u.worst_21d), "c_halves": (g.mean_H1 > 0) and (g.mean_H2 > 0)}
        return c, all(c.values())
    ca, va = pr_a(); cb, vb = pr_b("B_short_VXX"); cs, vs = pr_b("B_long_SVXY")
    print(f"\nPASS RULE A (varswap, gate_both vs none): {ca} -> {'PASS' if va else 'FAIL'}")
    print(f"PASS RULE B short VXX: {cb} -> {'PASS' if vb else 'FAIL'}")
    print(f"PASS RULE B long SVXY: {cs} -> {'PASS' if vs else 'FAIL'}")
    with open(os.path.join(OUT, "vrp_regime_summary.md"), "w") as f:
        f.write("# Conditional VRP study (GEX quintile + VIX term gates)\n\n")
        f.write(f"Track A varswap: {'PASS' if va else 'FAIL'} {ca}\n\nTrack B short VXX: {'PASS' if vb else 'FAIL'} {cb}\n\nTrack B long SVXY: {'PASS' if vs else 'FAIL'} {cs}\n\n")
        f.write("## Track A\n\n" + ra.to_markdown(index=False) + "\n\n## Cells\n\n" + cells.to_markdown(index=False) + "\n\n## Track B\n\n" + rb.to_markdown(index=False) + "\n")
    rp = os.path.join(BASE, "r/vrpRegimePlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
