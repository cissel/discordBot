#!/usr/bin/env python3
"""
intraday_momentum.py  (pre-registered, Oct 19 2026)
=====================================================
Test of Gao, Han, Li, Zhou (2018) "Market intraday momentum": SPY's first
half-hour return (prior close -> 10:00) predicts its last half-hour return
(15:30 -> 16:00), strongest on high-volatility / high-volume days.

Pre-registered design (written before results were seen)
  Predictors  r_first  = close_{t-1} -> 10:00 close   (paper's main predictor; includes overnight)
              r_open30 = 09:30 open -> 10:00 close    (intraday-only variant)
              r_12     = 15:00 -> 15:30              (paper's second predictor)
  Target      r_last   = 15:30 close -> 16:00 close
  Tests
    1. OLS r_last ~ r_first, Newey-West (lag 5) t-stat, full sample and both halves.
    2. Sign strategy: at 15:30 long if r_first > 0, short if < 0, flat at close.
       Net of measured cost (spyExecutionCost.py): last-30m half-spread 0.107bp +
       last-5m 0.098bp = 0.205bp round trip; conservative run at 0.5bp.
    3. Conditioning (mechanism checks, not fishing): VIX_prev >= 20 vs < 20;
       GEX trailing-252 quintile (q1 = most negative = highest realized vol);
       |r_first| terciles; VIX9D > VIX3M inversion.
  PASS rule (all three, net of 0.205bp):
    (a) pooled NW t >= 2.0 on r_first coefficient,
    (b) sign-strategy Sharpe > 0 in BOTH halves (2021-2023, 2024-2026),
    (c) hit rate > 50% with binomial p < 0.05 pooled.
  Anything that passes only inside a conditioning cell is reported as
  "conditional, needs its own pre-registration", not as a pass.

Data  outputs/markets/intraday/SPY_{2021..2026}_1min.csv (regular session only)
      outputs/markets/cache/VIX.csv, spy_gex_daily.csv, vix_term_history.csv
Out   outputs/research/structural/intraday_momentum_results.csv
      outputs/research/structural/intraday_momentum_daily.csv
      outputs/research/structural/intraday_momentum_summary.md
      outputs/research/structural/intraday_momentum.png  (r/intradayMomentumPlot.R)
"""
import os
import sys
import glob
import json
import subprocess

import numpy as np
import pandas as pd
from scipy import stats

BASE = os.path.expanduser("~/discordBot")
OUT_DIR = os.path.join(BASE, "outputs/research/structural")
os.makedirs(OUT_DIR, exist_ok=True)
COST_JSON = os.path.join(BASE, "outputs/markets/spy_cost_model.json")


def load_minutes():
    frames = []
    for f in sorted(glob.glob(os.path.join(BASE, "outputs/markets/intraday/SPY_20*_1min.csv"))):
        d = pd.read_csv(f, usecols=["date", "open", "close", "volume"])
        frames.append(d)
    m = pd.concat(frames, ignore_index=True)
    m["t"] = pd.to_datetime(m["date"], utc=True).dt.tz_convert("America/New_York")
    m["day"] = m["t"].dt.date
    m["hm"] = m["t"].dt.strftime("%H:%M")
    m = m[(m["hm"] >= "09:30") & (m["hm"] < "16:00")]
    return m


def px_at(m, hm, col="close", how="last"):
    """Price of the bar whose minute == hm (last bar at/before if exact minute is missing)."""
    sub = m[m["hm"] <= hm].sort_values("t").groupby("day").tail(1)
    return sub.set_index("day")[col]


def build_daily(m):
    close = m.sort_values("t").groupby("day").tail(1).set_index("day")["close"]       # 15:59 bar close
    open_ = m.sort_values("t").groupby("day").head(1).set_index("day")["open"]        # 09:30 open
    p1000 = px_at(m, "09:59")                                                          # 10:00
    p1500 = px_at(m, "14:59")
    p1530 = px_at(m, "15:29")
    d = pd.DataFrame({"open": open_, "p1000": p1000, "p1500": p1500, "p1530": p1530, "close": close})
    d["prev_close"] = d["close"].shift(1)
    # drop half-days (no 15:30 bar distinct from close) and bad days
    full = m.groupby("day")["hm"].max() >= "15:55"
    d = d[full.reindex(d.index).fillna(False)]
    d["r_first"] = d["p1000"] / d["prev_close"] - 1
    d["r_open30"] = d["p1000"] / d["open"] - 1
    d["r_12"] = d["p1530"] / d["p1500"] - 1
    d["r_last"] = d["close"] / d["p1530"] - 1
    d["r_mid"] = d["p1530"] / d["p1000"] - 1
    d.index = pd.to_datetime(d.index)
    d = d.dropna(subset=["r_first", "r_last"])
    # conditioning data (all lagged: known before 15:30 of day t)
    vix = pd.read_csv(os.path.join(BASE, "outputs/markets/cache/VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
    d["vix_prev"] = vix.reindex(d.index, method="ffill").shift(1)
    gex = pd.read_csv(os.path.join(BASE, "outputs/markets/cache/spy_gex_daily.csv"), parse_dates=["date"]).set_index("date")["gex"]
    gq = gex.rolling(252, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    gq = (gq * 5).clip(0, 4.999).apply(np.floor) + 1          # 1 = most negative GEX
    d["gex_q"] = gq.reindex(d.index, method="ffill").shift(1)
    vt = pd.read_csv(os.path.join(BASE, "outputs/markets/cache/vix_term_history.csv"), parse_dates=["date"]).set_index("date")
    inv = (vt["vix9d"] > vt["vix3m"]).astype(float)
    d["term_inverted"] = inv.reindex(d.index, method="ffill").shift(1)
    d["abs_first_tercile"] = pd.qcut(d["r_first"].abs(), 3, labels=["small", "mid", "large"])
    d["half"] = np.where(d.index < pd.Timestamp("2024-01-01"), "2021-2023", "2024-2026")
    d["year"] = d.index.year
    return d


def nw_tstat(y, x, lags=5):
    """OLS slope with Newey-West (Bartlett) HAC standard error, no statsmodels dependency."""
    y = np.asarray(y, float); x = np.asarray(x, float)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ beta
    n = len(y)
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (X * u[:, None]).T @ (X * u[:, None])
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        G = (X[L:] * u[L:, None]).T @ (X[:-L] * u[:-L, None])
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    r2 = 1 - u.var() / y.var() if y.var() > 0 else 0.0
    return float(beta[1]), float(beta[1] / np.sqrt(V[1, 1])), float(r2)


def strat(d, pred, rt_cost_bps, long_only=False):
    sgn = np.sign(d[pred])
    if long_only:
        sgn = (sgn > 0).astype(float)
    pnl = sgn * d["r_last"] - (sgn != 0) * rt_cost_bps / 1e4
    return pnl


def summarize(pnl, label):
    pnl = pnl.dropna()
    act = pnl[pnl != 0]
    n = len(act)
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252)) if pnl.std() > 0 else np.nan
    hit = float((act > 0).mean()) if n else np.nan
    p_bin = float(stats.binomtest(int((act > 0).sum()), n, 0.5, alternative="greater").pvalue) if n else np.nan
    return {"cell": label, "n_days": n, "mean_bps": round(pnl.mean() * 1e4, 3), "sharpe": round(sharpe, 3),
            "hit_rate": round(hit, 4) if n else np.nan, "binom_p": round(p_bin, 4) if n else np.nan,
            "total_pct": round(pnl.sum() * 100, 2)}


def main():
    cost = json.load(open(COST_JSON))
    rt = cost["buckets"]["last 30m"]["recommended_per_side_bps"] + cost["buckets"]["last 5m"]["recommended_per_side_bps"]
    rt_cons = 0.5
    m = load_minutes()
    d = build_daily(m)
    d.to_csv(os.path.join(OUT_DIR, "intraday_momentum_daily.csv"))
    print(f"days {len(d)}  {d.index.min().date()} -> {d.index.max().date()}  round-trip cost {rt:.3f}bp (conservative {rt_cons}bp)")

    rows = []
    # 1. regressions
    for pred in ["r_first", "r_open30", "r_12"]:
        for name, sub in [("all", d), ("2021-2023", d[d.half == "2021-2023"]), ("2024-2026", d[d.half == "2024-2026"])]:
            b, t, r2 = nw_tstat(sub["r_last"], sub[pred])
            rows.append({"test": "ols", "pred": pred, "cell": name, "n_days": len(sub), "beta": round(b, 4),
                         "nw_t": round(t, 2), "r2": round(r2, 5)})
    # baseline: last-30m buy and hold
    for name, sub in [("all", d), ("2021-2023", d[d.half == "2021-2023"]), ("2024-2026", d[d.half == "2024-2026"])]:
        s = summarize(sub["r_last"], f"B&H last30m {name}"); s.update({"test": "baseline", "pred": "none", "cost_bps": 0})
        rows.append(s)
    # 2. sign strategy, both costs, both halves, long-only variant
    for pred in ["r_first", "r_open30", "r_12"]:
        for c in [rt, rt_cons]:
            for lo in [False, True]:
                for name, sub in [("all", d), ("2021-2023", d[d.half == "2021-2023"]), ("2024-2026", d[d.half == "2024-2026"])]:
                    s = summarize(strat(sub, pred, c, lo), name)
                    s.update({"test": "sign_long_only" if lo else "sign", "pred": pred, "cost_bps": round(c, 3)})
                    rows.append(s)
        # by year
        for y, sub in d.groupby("year"):
            s = summarize(strat(sub, pred, rt), str(y)); s.update({"test": "sign_year", "pred": pred, "cost_bps": round(rt, 3)})
            rows.append(s)
    # 3. conditioning (r_first only, measured cost)
    conds = {"vix_prev>=20": d.vix_prev >= 20, "vix_prev<20": d.vix_prev < 20,
             "term_inverted": d.term_inverted == 1, "term_normal": d.term_inverted == 0}
    for q in [1, 2, 3, 4, 5]:
        conds[f"gex_q{q}"] = d.gex_q == q
    for tc in ["small", "mid", "large"]:
        conds[f"|r_first| {tc}"] = d.abs_first_tercile == tc
    for name, mask in conds.items():
        sub = d[mask.fillna(False)]
        if len(sub) < 30:
            continue
        b, t, r2 = nw_tstat(sub["r_last"], sub["r_first"])
        s = summarize(strat(sub, "r_first", rt), name)
        s.update({"test": "cond", "pred": "r_first", "cost_bps": round(rt, 3), "beta": round(b, 4), "nw_t": round(t, 2)})
        rows.append(s)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT_DIR, "intraday_momentum_results.csv"), index=False)

    # pass rule
    ols_all = res[(res.test == "ols") & (res.pred == "r_first") & (res.cell == "all")].iloc[0]
    sg = res[(res.test == "sign") & (res.pred == "r_first") & (res.cost_bps == round(rt, 3))].set_index("cell")
    a = ols_all.nw_t >= 2.0
    b_ = (sg.loc["2021-2023", "sharpe"] > 0) and (sg.loc["2024-2026", "sharpe"] > 0)
    c_ = (sg.loc["all", "hit_rate"] > 0.5) and (sg.loc["all", "binom_p"] < 0.05)
    verdict = "PASS" if (a and b_ and c_) else "FAIL"

    pd.set_option("display.width", 200)
    print("\nOLS r_last ~ pred (NW t):")
    print(res[res.test == "ols"][["pred", "cell", "n_days", "beta", "nw_t", "r2"]].to_string(index=False))
    print("\nBaseline B&H last 30m:")
    print(res[res.test == "baseline"][["cell", "n_days", "mean_bps", "sharpe", "hit_rate"]].to_string(index=False))
    print(f"\nSign strategy (cost {rt:.3f}bp RT):")
    print(res[(res.test == "sign") & (res.cost_bps == round(rt, 3))][["pred", "cell", "n_days", "mean_bps", "sharpe", "hit_rate", "binom_p", "total_pct"]].to_string(index=False))
    print(f"\nSign strategy conservative ({rt_cons}bp RT):")
    print(res[(res.test == "sign") & (res.cost_bps == rt_cons)][["pred", "cell", "mean_bps", "sharpe", "hit_rate"]].to_string(index=False))
    print("\nLong-only variant (measured cost):")
    print(res[(res.test == "sign_long_only") & (res.cost_bps == round(rt, 3))][["pred", "cell", "mean_bps", "sharpe", "hit_rate"]].to_string(index=False))
    print("\nBy year (r_first):")
    print(res[(res.test == "sign_year") & (res.pred == "r_first")][["cell", "n_days", "mean_bps", "sharpe", "hit_rate"]].to_string(index=False))
    print("\nConditioning (r_first, measured cost):")
    print(res[res.test == "cond"][["cell", "n_days", "beta", "nw_t", "mean_bps", "sharpe", "hit_rate", "binom_p"]].to_string(index=False))
    print(f"\nPASS RULE: (a) NW t>=2: {a} (t={ols_all.nw_t})  (b) both halves Sharpe>0: {b_} "
          f"({sg.loc['2021-2023','sharpe']}, {sg.loc['2024-2026','sharpe']})  (c) hit>50% p<.05: {c_} "
          f"(hit={sg.loc['all','hit_rate']}, p={sg.loc['all','binom_p']})  ->  {verdict}")

    with open(os.path.join(OUT_DIR, "intraday_momentum_summary.md"), "w") as f:
        f.write(f"# Intraday momentum (first 30m -> last 30m), SPY 2021-2026\n\nVerdict: **{verdict}**\n\n")
        f.write(f"Days {len(d)}, round-trip cost {rt:.3f}bp (measured), conservative {rt_cons}bp.\n\n")
        f.write(f"Pass rule: (a) NW t>=2 -> {a} (t={ols_all.nw_t}); (b) both halves Sharpe>0 -> {b_}; (c) hit>50% binomial p<.05 -> {c_}.\n\n")
        f.write("## Sign strategy, measured cost\n\n" + res[(res.test == "sign") & (res.cost_bps == round(rt, 3))][["pred", "cell", "n_days", "mean_bps", "sharpe", "hit_rate", "binom_p"]].to_markdown(index=False))
        f.write("\n\n## Conditioning\n\n" + res[res.test == "cond"][["cell", "n_days", "beta", "nw_t", "mean_bps", "sharpe", "hit_rate", "binom_p"]].to_markdown(index=False))
        f.write("\n\n## OLS\n\n" + res[res.test == "ols"][["pred", "cell", "n_days", "beta", "nw_t", "r2"]].to_markdown(index=False) + "\n")

    # daily series for the plot
    out = pd.DataFrame({"date": d.index, "strat_r_first": strat(d, "r_first", rt).values,
                        "strat_r_12": strat(d, "r_12", rt).values, "bh_last30": d["r_last"].values,
                        "vix_prev": d["vix_prev"].values, "gex_q": d["gex_q"].values})
    out.to_csv(os.path.join(OUT_DIR, "intraday_momentum_daily_pnl.csv"), index=False)
    rp = os.path.join(BASE, "r/intradayMomentumPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-600:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
