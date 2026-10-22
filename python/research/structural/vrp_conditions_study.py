#!/usr/bin/env python3
"""
vrp_conditions_study.py  (pre-registered, Oct 19 2026)
=======================================================
Three timing hypotheses for the SPY variance risk premium, tested on the
same synthetic 30-day variance swap as vrp_regime_study.py (short at strike
VIX^2, settle on next-21-day realised variance, P&L in vol points per vega).
Sample 2016-06 to 2026-09. All conditioning variables are known at entry.

 V1 Scheduled-event premium
    H: entries whose 21-day window contains NO FOMC / CPI / NFP earn more
    per entry than windows containing events, and entries on the day AFTER
    an event (IV crushed) earn more than entries 1-2 days BEFORE one.
    Cells: window_has_fomc / window_has_cpi / clean window; entry timing
    rel. to nearest FOMC: pre (t-2..t-1), post (t+1..t+2), other.
 V2 Post-selloff premium
    H: after a 5-day SPY drawdown (trailing 5d return bottom tercile) with the
    term structure normal, IV stays rich while RV collapses -> best entries.
    Cells: trailing 5d return tercile x inverted/normal; days since the last
    inversion ended (0-5, 6-20, >20, never-in-60).
 V3 OpEx / month cycle
    H: premium differs by week of month and by distance to monthly OpEx (3rd
    Friday). Cells: week-of-month 1..5; days-to-OpEx buckets (0-2, 3-7, 8-14,
    15+); month-end entries (last 3 trading days).

Inference: cell mean, Newey-West t (21 lags) on the cell's daily P&L vs the
complement, both halves (H1 < 2021-07-01, H2 after). PASS for a hypothesis
requires: (a) the predicted ordering holds in BOTH halves and (b) NW t of the
difference >= 2.0 pooled. Everything else is reported as FAIL/PARTIAL. No
cell is promoted to the vol book without a pass here first.

Out: outputs/research/structural/vrp_conditions_results.csv, _summary.md,
     vrp_conditions.png (r/vrpConditionsPlot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/discordBot/python"))
from vrp_regime_study import load, track_a, nw_t  # noqa: E402
import macro_event_calendars as M  # noqa: E402

OUT = os.path.expanduser("~/discordBot/outputs/research/structural")
H = 21


def third_friday(y, m):
    d = pd.Timestamp(year=y, month=m, day=15)
    while d.weekday() != 4:
        d += pd.Timedelta(days=1)
    return d


def enrich(a):
    idx = a.index
    fomc = pd.to_datetime(M.FOMC_DATES); cpi = pd.to_datetime(M.CPI_DATES); nfp = pd.to_datetime(M.NFP_DATES)
    tds = pd.Series(np.arange(len(idx)), index=idx)      # trading-day ordinal
    def window_has(dates):
        pos = np.searchsorted(idx.values, dates.values)   # index of first trading day >= event
        ev_ord = set(p for p in pos if p < len(idx))
        out = np.zeros(len(idx), bool)
        for i in range(len(idx)):
            out[i] = any((i + 1) <= e <= (i + H) for e in ev_ord)
        return out
    a["win_fomc"] = window_has(fomc); a["win_cpi"] = window_has(cpi); a["win_nfp"] = window_has(nfp)
    a["win_clean"] = ~(a["win_fomc"] | a["win_cpi"])
    # entry timing relative to nearest FOMC (trading days)
    fpos = np.searchsorted(idx.values, fomc.values); fpos = fpos[fpos < len(idx)]
    rel = np.full(len(idx), 99)
    for i in range(len(idx)):
        d = fpos - i
        j = np.argmin(np.abs(d)); rel[i] = d[j]
    a["fomc_rel"] = rel   # negative = event already happened |rel| days ago; positive = event in rel days
    a["fomc_timing"] = np.select([(rel >= 1) & (rel <= 2), (rel <= -1) & (rel >= -2), rel == 0], ["pre", "post", "event_day"], "other")
    # V2
    r = a["spy_ret"]
    a["ret5"] = (1 + r).rolling(5).apply(np.prod, raw=True) - 1
    a["ret5_tercile"] = pd.qcut(a["ret5"], 3, labels=["down", "flat", "up"])
    inv = a["inverted"].fillna(0).astype(int)
    last_inv = pd.Series(np.where(inv == 1, np.arange(len(inv)), np.nan), index=idx).ffill()
    a["days_since_inv"] = np.arange(len(inv)) - last_inv.values
    a["since_inv_bucket"] = pd.cut(a["days_since_inv"], [-1, 0, 5, 20, 60, 1e9], labels=["inverted_now", "0-5", "6-20", "21-60", ">60/never"])
    # V3
    a["week_of_month"] = ((idx.day - 1) // 7 + 1)
    opex = [third_friday(d.year, d.month) for d in idx]
    nxt = []
    for d, o in zip(idx, opex):
        if d > o:
            m2 = d.month % 12 + 1; y2 = d.year + (d.month == 12); o = third_friday(y2, m2)
        nxt.append((o - d).days)
    a["days_to_opex"] = nxt
    a["opex_bucket"] = pd.cut(a["days_to_opex"], [-1, 2, 7, 14, 40], labels=["0-2", "3-7", "8-14", "15+"])
    mon = idx.to_period("M"); pos_in_month = pd.Series(idx).groupby(mon.values).cumcount(ascending=False).values
    a["month_end3"] = pos_in_month < 3
    return a


def cell_stats(a, mask, label, hyp, pred_sign):
    s = a["pnl_vol"]; m = mask.fillna(False).astype(bool)
    inn, out = s[m], s[~m]
    diff = s.where(m, 0) * 0  # placeholder
    # NW t of difference in means via regression on dummy: equivalent to NW t of (x - mean) weighted; use simple approach:
    d = (s * np.where(m, 1.0, 0.0) / max(m.mean(), 1e-9)) - (s * np.where(~m, 1.0, 0.0) / max((~m).mean(), 1e-9))
    t = nw_t(d.values)
    h1 = a["half"] == "H1"
    o1 = inn[h1[m]].mean() - out[h1[~m]].mean(); o2 = inn[~h1[m]].mean() - out[~h1[~m]].mean()
    return {"hyp": hyp, "cell": label, "n": int(m.sum()), "mean_in": round(inn.mean(), 3), "mean_out": round(out.mean(), 3),
            "diff": round(inn.mean() - out.mean(), 3), "nw_t": round(t, 2), "hit_in": round((inn > 0).mean(), 3),
            "diff_H1": round(o1, 3), "diff_H2": round(o2, 3), "pred_sign": pred_sign,
            "ordering_both_halves": bool(np.sign(o1) == pred_sign and np.sign(o2) == pred_sign)}


def main():
    d = load()
    _, _, a = track_a(d)
    a = enrich(a)
    rows = []
    # V1
    rows.append(cell_stats(a, a["win_clean"], "window clean (no FOMC/CPI)", "V1", +1))
    rows.append(cell_stats(a, a["win_fomc"], "window has FOMC", "V1", -1))
    rows.append(cell_stats(a, a["win_cpi"], "window has CPI", "V1", -1))
    rows.append(cell_stats(a, a["fomc_timing"] == "pre", "entry 1-2d before FOMC", "V1", -1))
    rows.append(cell_stats(a, a["fomc_timing"] == "post", "entry 1-2d after FOMC", "V1", +1))
    # V2
    for tc, sgn in [("down", +1), ("flat", 0), ("up", -1)]:
        rows.append(cell_stats(a, a["ret5_tercile"] == tc, f"trailing 5d {tc}", "V2", sgn))
    rows.append(cell_stats(a, (a["ret5_tercile"] == "down") & (a["inverted"] == 0), "5d down AND term normal", "V2", +1))
    for b, sgn in [("inverted_now", -1), ("0-5", +1), ("6-20", +1), ("21-60", 0), (">60/never", 0)]:
        rows.append(cell_stats(a, a["since_inv_bucket"] == b, f"days since inversion {b}", "V2", sgn))
    # V3
    for w in [1, 2, 3, 4, 5]:
        rows.append(cell_stats(a, a["week_of_month"] == w, f"week of month {w}", "V3", 0))
    for b in ["0-2", "3-7", "8-14", "15+"]:
        rows.append(cell_stats(a, a["opex_bucket"] == b, f"days to OpEx {b}", "V3", 0))
    rows.append(cell_stats(a, a["month_end3"], "month-end (last 3 td)", "V3", 0))
    res = pd.DataFrame(rows)
    res["pass"] = (res["nw_t"].abs() >= 2.0) & res["ordering_both_halves"] & (res["pred_sign"] != 0)
    res["note"] = np.where(res["pred_sign"] == 0, "exploratory (no predicted sign)", "")
    res.to_csv(os.path.join(OUT, "vrp_conditions_results.csv"), index=False)
    pd.set_option("display.width", 220)
    print(f"days {len(a)} {a.index.min().date()} -> {a.index.max().date()}  overall mean {a['pnl_vol'].mean():.3f} vol pts/entry")
    print(res.drop(columns=["note"]).to_string(index=False))
    verdict = {}
    for hyp in ["V1", "V2", "V3"]:
        sub = res[(res.hyp == hyp) & (res.pred_sign != 0)]
        verdict[hyp] = "PASS" if (len(sub) and sub["pass"].all()) else ("PARTIAL" if sub["pass"].any() else "FAIL")
    if "V3" in verdict:
        ex = res[res.hyp == "V3"]
        verdict["V3"] = "exploratory: " + ("strong cell(s) " + ", ".join(ex[ex.nw_t.abs() >= 2.5]["cell"]) if (ex.nw_t.abs() >= 2.5).any() else "nothing beyond noise (|t|<2.5 everywhere)")
    print("\nVERDICTS:", verdict)
    with open(os.path.join(OUT, "vrp_conditions_summary.md"), "w") as f:
        f.write("# VRP timing conditions (V1 events, V2 post-selloff, V3 OpEx/month)\n\n")
        for k, v in verdict.items():
            f.write(f"- {k}: {v}\n")
        f.write("\n" + res.to_markdown(index=False) + "\n")
    a[["pnl_vol", "half", "win_clean", "fomc_timing", "ret5_tercile", "since_inv_bucket", "week_of_month", "opex_bucket"]].to_csv(
        os.path.join(OUT, "vrp_conditions_daily.csv"))
    rp = os.path.expanduser("~/discordBot/r/vrpConditionsPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-600:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
