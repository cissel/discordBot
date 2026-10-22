#!/usr/bin/env python3
"""
holdout_2011_2016_study.py  (pre-registered, Oct 19 2026)
===========================================================
Out-of-sample checks of three condor-book rules/leads on data they were never
fit on: SPX price (spy_gex_daily.csv "price" column, 2011-05+), GEX (same file),
VIX (1990+), CBOE SKEW (2011+). Holdout = 2011-05-02 .. 2016-06-23 (the day
before the in-sample window of studies 6, 7, 12 begins). Confirmation period
2016-06-24 .. 2026-09 reported alongside for reference (that is in-sample for
rule 1 and 4 and for the skew lead, and is NOT the test).

Synthetic 30-day variance swap as in vrp_regime_study: short at K = VIX^2,
settle on next-21-day realised variance of SPX log returns, P&L in vol points
per unit vega. Regime variables lagged one day. NW t with 21 lags.

 R1 Live gate, GEX half (term inversion needs VIX9D, 2016+, so untestable here).
    H: GEX quintile 1 (most negative, trailing 252) has the highest RV/IV and the
    lowest short-variance P&L on 2011-2016.
    PASS: q1 mean P&L < mean of q2-q5 with NW t <= -2 AND q1 RV/IV is the
    highest of the five quintiles.
 R2 Complacent-skew tail gate (lead from study 12, found on 2016-2026).
    H: SKEW z-score (vs trailing 252) < -1 precedes (a) more 21-day drawdowns
    worse than -5% and (b) higher RV/IV, on 2011-2016.
    PASS: both differences positive with NW t >= 2 on the holdout.
 R4 Post-dip entries (study 7 V2 cell, 5d-return half only).
    H: entries after a bottom-tercile trailing 5-day SPX return earn more
    short-variance P&L than other entries on 2011-2016.
    PASS: difference > 0 with NW t >= 2.
Out: outputs/research/structural/holdout_2011_2016_results.csv, _summary.md,
     holdout_2011_2016.png (r/holdout20112016Plot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from vrp_regime_study import nw_t  # noqa: E402

CACHE = os.path.expanduser("~/discordBot/outputs/markets/cache")
OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
H = 21
SPLIT = pd.Timestamp("2016-06-24")


def build():
    g = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"]).set_index("date").sort_index()
    vix = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
    sk = pd.read_csv(os.path.join(CACHE, "cboe_skew_daily.csv"), parse_dates=["date"]).set_index("date")["cboe_skew"]
    d = pd.DataFrame({"px": g["price"], "gex": g["gex"]})
    d["vix"] = vix.reindex(d.index, method="ffill")
    d["skew"] = sk.reindex(d.index, method="ffill").shift(1)
    d["skew_z"] = (d["skew"] - d["skew"].rolling(252).mean()) / d["skew"].rolling(252).std()
    lr = np.log(d["px"]).diff()
    d["ret"] = d["px"].pct_change()
    d["rv_fwd_var"] = (lr.shift(-1) ** 2).rolling(H).mean().shift(-(H - 1)) * 252 * 1e4
    d["rv_fwd"] = np.sqrt(d["rv_fwd_var"])
    d["pnl_vol"] = (d["vix"] ** 2 - d["rv_fwd_var"]) / (2 * d["vix"])
    d["rv_iv"] = d["rv_fwd"] / d["vix"]
    gq = d["gex"].rolling(252, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    d["gex_q"] = (np.floor((gq * 5).clip(0, 4.999)) + 1).shift(1)
    # term inversion from CBOE history (VIX9D from 2011, VIX3M from 2009), lagged one day
    def cboe(name):
        c = pd.read_csv(os.path.join(CACHE, f"{name}_cboe_history.csv"))
        c["date"] = pd.to_datetime(c["DATE"]); return c.set_index("date")["CLOSE"]
    v9, v3 = cboe("VIX9D"), cboe("VIX3M")
    d["inverted"] = (v9.reindex(d.index, method="ffill") > v3.reindex(d.index, method="ffill")).astype(float).shift(1)
    d["gate_both"] = (d["gex_q"] != 1) & (d["inverted"] == 0)
    fwd_min = pd.Series([((1 + d["ret"].iloc[i + 1:i + 1 + H]).cumprod().min() - 1) if i + H < len(d) else np.nan for i in range(len(d))], index=d.index)
    d["crash21"] = (fwd_min < -0.05).astype(float)
    d["ret5"] = (1 + d["ret"]).rolling(5).apply(np.prod, raw=True) - 1
    d = d.dropna(subset=["pnl_vol", "vix"])
    d["period"] = np.where(d.index < SPLIT, "holdout 2011-2016", "in-sample 2016-2026")
    return d


def dtest(s, m):
    m = m.fillna(False).astype(bool)
    dd = s * np.where(m, 1.0, 0.0) / max(m.mean(), 1e-9) - s * np.where(~m, 1.0, 0.0) / max((~m).mean(), 1e-9)
    return float(s[m].mean() - s[~m].mean()), nw_t(dd.values), int(m.sum())


def main():
    d = build()
    rows = []
    for per, a in d.groupby("period"):
        # R1 GEX
        a1 = a.dropna(subset=["gex_q"])
        qtab = a1.groupby("gex_q").agg(n=("pnl_vol", "size"), pnl=("pnl_vol", "mean"), rv_iv=("rv_iv", "mean")).round(3)
        diff, t, n = dtest(a1["pnl_vol"], a1["gex_q"] == 1)
        q1_rviv_highest = bool(qtab["rv_iv"].idxmax() == 1)
        rows.append({"period": per, "rule": "R1 GEX q1 vs rest", "n_in": n, "diff": round(diff, 3), "nw_t": round(t, 2),
                     "extra": f"q1 RV/IV highest: {q1_rviv_highest}; RV/IV by q: {qtab['rv_iv'].to_dict()}; P&L by q: {qtab['pnl'].to_dict()}",
                     "pass": bool(t <= -2 and q1_rviv_highest)})
        # R1b term inversion (CBOE history) and the full live gate
        a1b = a.dropna(subset=["inverted"])
        di, ti, ni = dtest(a1b["pnl_vol"], a1b["inverted"] == 1)
        rows.append({"period": per, "rule": "R1b inverted vs normal", "n_in": ni, "diff": round(di, 3), "nw_t": round(ti, 2),
                     "extra": f"RV/IV inverted {a1b.loc[a1b.inverted==1,'rv_iv'].mean():.3f} vs normal {a1b.loc[a1b.inverted==0,'rv_iv'].mean():.3f}; P&L {a1b.loc[a1b.inverted==1,'pnl_vol'].mean():.2f} vs {a1b.loc[a1b.inverted==0,'pnl_vol'].mean():.2f}",
                     "pass": bool(ti <= -2 and di < 0)})
        a1c = a.dropna(subset=["gex_q", "inverted"])
        s_all = a1c["pnl_vol"]; s_g = s_all.where(a1c["gate_both"], 0.0)
        def msharpe(x):
            return float(np.mean([x.iloc[k::H].mean() / x.iloc[k::H].std() * np.sqrt(12) for k in range(3) if x.iloc[k::H].std() > 0]))
        dg, tg, ng = dtest(s_all, a1c["gate_both"])
        rows.append({"period": per, "rule": "R1c live gate (not q1 AND not inverted) vs excluded", "n_in": ng, "diff": round(dg, 3), "nw_t": round(tg, 2),
                     "extra": f"monthly Sharpe ungated {msharpe(s_all):.2f} vs gated {msharpe(s_g):.2f}; eligible {a1c['gate_both'].mean():.2f}; worst cal-month mean ungated {s_all.groupby(s_all.index.to_period('M')).mean().min():.1f} gated {s_g.groupby(s_g.index.to_period('M')).mean().min():.1f}",
                     "pass": bool(msharpe(s_g) > msharpe(s_all))})
        # R2 skew
        a2 = a.dropna(subset=["skew_z"])
        m = a2["skew_z"] < -1
        dc, tc, n = dtest(a2["crash21"], m); dr, tr, _ = dtest(a2["rv_iv"], m); dp, tp, _ = dtest(a2["pnl_vol"], m)
        rows.append({"period": per, "rule": "R2 SKEW z<-1 vs rest", "n_in": n, "diff": round(dc, 3), "nw_t": round(tc, 2),
                     "extra": f"crash rate {a2.loc[m,'crash21'].mean():.3f} vs {a2.loc[~m,'crash21'].mean():.3f}; RV/IV diff {dr:+.3f} (t {tr:.2f}); P&L diff {dp:+.2f} (t {tp:.2f})",
                     "pass": bool(tc >= 2 and tr >= 2)})
        # R4 post-dip
        a4 = a.dropna(subset=["ret5"])
        edge = a4["ret5"].quantile(1 / 3)
        m4 = a4["ret5"] < edge
        dp4, tp4, n4 = dtest(a4["pnl_vol"], m4)
        rows.append({"period": per, "rule": "R4 5d-return bottom tercile vs rest", "n_in": n4, "diff": round(dp4, 3), "nw_t": round(tp4, 2),
                     "extra": f"tercile edge {edge:+.4f}; mean in {a4.loc[m4,'pnl_vol'].mean():.2f} vs out {a4.loc[~m4,'pnl_vol'].mean():.2f}",
                     "pass": bool(tp4 >= 2 and dp4 > 0)})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, "holdout_2011_2016_results.csv"), index=False)
    d[["period", "pnl_vol", "rv_iv", "gex_q", "skew_z", "crash21", "ret5"]].to_csv(os.path.join(OUT, "holdout_2011_2016_daily.csv"))
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 200)
    print(d.groupby("period").size())
    print(res.to_string(index=False))
    hold = res[res.period == "holdout 2011-2016"].set_index("rule")["pass"].to_dict()
    print("\nHOLDOUT VERDICTS:", {k: ("PASS" if v else "FAIL") for k, v in hold.items()})
    with open(os.path.join(OUT, "holdout_2011_2016_summary.md"), "w") as f:
        f.write("# 2011-2016 holdout checks (R1 GEX gate, R2 complacent skew, R4 post-dip)\n\n")
        for k, v in hold.items():
            f.write(f"- {k}: {'PASS' if v else 'FAIL'} on the holdout\n")
        f.write("\n" + res.to_markdown(index=False) + "\n")
    rp = os.path.expanduser("~/discordBot/r/holdout20112016Plot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-600:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
