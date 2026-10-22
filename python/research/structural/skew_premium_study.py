#!/usr/bin/env python3
"""
skew_premium_study.py  (pre-registered, Oct 19 2026)  - C2
===========================================================
H: when the CBOE SKEW index is high, the put side of the SPY surface carries
the premium: realised DOWNSIDE semivariance over the next 21 days does not rise
in proportion to what the skew implies, so (a) the short-variance P&L is
higher in high-SKEW states and (b) the realised downside share of variance is
NOT higher in high-SKEW states. If both hold, an asymmetric condor (wider put
wing distance, or put-spread-only) is the implementation to forward-test.

Data: outputs/markets/cache/cboe_skew_daily.csv (date, cboe_skew), SPY/VIX/
GEX/term via vrp_regime_study.load(). SKEW lagged one day. Sample 2016-06 to
2026-09, 2573 entries.

Cells: SKEW tercile (low/mid/high) and SKEW z-score vs trailing 252 days
(>+1, <-1). Outcomes per entry:
  pnl_vol      short variance swap P&L (vol pts per vega), as in study 6
  down_share   sum of squared negative daily log returns / total sum, next 21d
  rv_fwd/vix   realised over implied
  crash21      1 if min 21-day-forward cumulative return < -5%
Also downside-only variance swap proxy: payoff (VIX^2 * 0.5 - 2 * downside_var)
is NOT computed (no downside strike available); down_share is the honest proxy.

PASS (both required, predicted signs, NW t 21 lags, same sign in both halves):
  (a) mean pnl_vol high-SKEW tercile > low-SKEW tercile, t >= 2
  (b) mean down_share high-SKEW tercile <= low-SKEW tercile (t of the
      difference must NOT be >= +2; i.e. high skew does not foretell more
      downside realisation).
PARTIAL if only (a). FAIL otherwise.
Out: outputs/research/structural/skew_premium_results.csv, _summary.md,
     skew_premium.png (r/skewPremiumPlot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from vrp_regime_study import load, track_a, nw_t  # noqa: E402

OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
SKEW = os.path.expanduser("~/discordBot/outputs/markets/cache/cboe_skew_daily.csv")
H = 21


def diff_t(a, mask_in, mask_out, col):
    s = a[col]
    m_in = mask_in.astype(bool); m_out = mask_out.astype(bool)
    d = s * np.where(m_in, 1.0, 0.0) / max(m_in.mean(), 1e-9) - s * np.where(m_out, 1.0, 0.0) / max(m_out.mean(), 1e-9)
    d = d[m_in | m_out]
    return float(s[m_in].mean() - s[m_out].mean()), nw_t(d.values)


def main():
    d = load(); _, _, a = track_a(d)
    sk = pd.read_csv(SKEW, parse_dates=["date"]).set_index("date")["cboe_skew"]
    a["skew"] = sk.reindex(a.index, method="ffill").shift(1)
    a["skew_z"] = ((a["skew"] - a["skew"].rolling(252).mean()) / a["skew"].rolling(252).std())
    a = a.dropna(subset=["skew"])
    lr = np.log1p(a["spy_ret"])
    neg2 = (lr.clip(upper=0) ** 2)
    tot2 = lr ** 2
    a["down_var_fwd"] = neg2.shift(-1).rolling(H).sum().shift(-(H - 1))
    a["tot_var_fwd"] = tot2.shift(-1).rolling(H).sum().shift(-(H - 1))
    a["down_share"] = a["down_var_fwd"] / a["tot_var_fwd"]
    cum = (1 + a["spy_ret"]).shift(-1)[::-1].cumprod()[::-1]  # placeholder not used
    fwd_min = pd.Series([((1 + a["spy_ret"].iloc[i + 1:i + 1 + H]).cumprod().min() - 1) if i + H < len(a) else np.nan for i in range(len(a))], index=a.index)
    a["crash21"] = (fwd_min < -0.05).astype(float)
    a["rv_iv"] = a["rv_fwd"] / a["vix"]
    a = a.dropna(subset=["down_share", "pnl_vol"])
    a["skew_terc"] = pd.qcut(a["skew"], 3, labels=["low", "mid", "high"])
    rows = []
    for col in ["pnl_vol", "down_share", "rv_iv", "crash21"]:
        for t in ["low", "mid", "high"]:
            m = a["skew_terc"] == t
            rows.append({"outcome": col, "cell": f"skew {t}", "n": int(m.sum()), "mean": round(float(a.loc[m, col].mean()), 4),
                         "mean_H1": round(float(a.loc[m & (a.half == "H1"), col].mean()), 4), "mean_H2": round(float(a.loc[m & (a.half == "H2"), col].mean()), 4)})
        for nm, m in [("skew z > +1", a["skew_z"] > 1), ("skew z < -1", a["skew_z"] < -1)]:
            m = m.fillna(False)
            rows.append({"outcome": col, "cell": nm, "n": int(m.sum()), "mean": round(float(a.loc[m, col].mean()), 4),
                         "mean_H1": round(float(a.loc[m & (a.half == "H1"), col].mean()), 4), "mean_H2": round(float(a.loc[m & (a.half == "H2"), col].mean()), 4)})
    res = pd.DataFrame(rows)
    hi, lo = a["skew_terc"] == "high", a["skew_terc"] == "low"
    tests = {}
    for col in ["pnl_vol", "down_share", "rv_iv", "crash21"]:
        dd, t = diff_t(a, hi, lo, col)
        h1 = a.half == "H1"
        d1 = a.loc[hi & h1, col].mean() - a.loc[lo & h1, col].mean(); d2 = a.loc[hi & ~h1, col].mean() - a.loc[lo & ~h1, col].mean()
        tests[col] = {"diff_high_minus_low": round(dd, 4), "nw_t": round(t, 2), "diff_H1": round(d1, 4), "diff_H2": round(d2, 4)}
    tdf = pd.DataFrame(tests).T.reset_index().rename(columns={"index": "outcome"})
    a_ok = tests["pnl_vol"]["nw_t"] >= 2 and tests["pnl_vol"]["diff_H1"] > 0 and tests["pnl_vol"]["diff_H2"] > 0
    b_ok = tests["down_share"]["nw_t"] < 2
    verdict = "PASS" if (a_ok and b_ok) else ("PARTIAL" if a_ok else "FAIL")
    # also the simple correlation of SKEW level with forward outcomes (Spearman)
    from scipy.stats import spearmanr
    sp = {c: round(float(spearmanr(a["skew"], a[c]).correlation), 3) for c in ["pnl_vol", "down_share", "rv_iv", "crash21"]}
    pd.set_option("display.width", 200)
    print(f"n {len(a)}  {a.index.min().date()} -> {a.index.max().date()}  SKEW tercile edges {a['skew'].quantile([1/3, 2/3]).round(1).tolist()}")
    print(res.to_string(index=False)); print(); print(tdf.to_string(index=False)); print("Spearman(SKEW, outcome):", sp)
    print(f"\n(a) pnl high>low t>=2 both halves: {a_ok}   (b) down_share not higher (t<2): {b_ok}   -> {verdict}")
    res.to_csv(os.path.join(OUT, "skew_premium_results.csv"), index=False)
    tdf.to_csv(os.path.join(OUT, "skew_premium_tests.csv"), index=False)
    a[["skew", "skew_z", "skew_terc", "pnl_vol", "down_share", "rv_iv", "crash21", "half"]].to_csv(os.path.join(OUT, "skew_premium_daily.csv"))
    with open(os.path.join(OUT, "skew_premium_summary.md"), "w") as f:
        f.write(f"# C2 skew premium\n\nVerdict: **{verdict}**  (a) {a_ok}  (b) {b_ok}\n\nSpearman: {sp}\n\n" + tdf.to_markdown(index=False) + "\n\n" + res.to_markdown(index=False) + "\n")
    rp = os.path.expanduser("~/discordBot/r/skewPremiumPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-600:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
