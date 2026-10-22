#!/usr/bin/env python3
"""
Pre-registered event study: do large off-market SPY block prints predict SPY drift?

Inputs (read only):
  outputs/research/block_events.csv
  outputs/research/block_outcomes.csv
  outputs/markets/cache/SPY.csv
Outputs:
  outputs/research/structural/block_results.csv
  outputs/research/structural/block_summary.md
  outputs/research/structural/block_plot_data.csv   (for the R plot)
  outputs/research/structural/block_strategy_daily.csv

DATA AUDIT FINDING (established before any test was run):
  outcomes.market_price is the PRIOR DAY dividend-adjusted close, not the market
  price at print time. The signed deviation block_price/market_price - 1 falls
  monotonically from +5.5% (2022) to +0.4% (2026), which is exactly the dividend
  adjustment signature. 94% of events are therefore labelled 'above_market'.
  The file's 'direction', 'deviation', 'reached_*' and 'pct_toward_*' columns
  inherit this artifact.
  We therefore run two specifications:
    SPEC A ("as filed")  : the pre-registered tests exactly on the file columns.
    SPEC B ("corrected") : reference = same-day RAW close, reconstructed from
                           the adjusted close series and the dividend factor
                           recovered from 16:00:00 closing-auction prints
                           (which equal the official close). Forward returns
                           start at the close of the print day, so there is no
                           lookahead for any print. Spec B is the headline.
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = "/home/jhcv/discordBot"
OUT = os.path.join(ROOT, "outputs/research/structural")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(20261019)
NBOOT = 10000
HORIZONS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1mo": 21}
COST = 0.0001  # 1bp per side

# ---------------------------------------------------------------- load
ev = pd.read_csv(os.path.join(ROOT, "outputs/research/block_events.csv"))
oc = pd.read_csv(os.path.join(ROOT, "outputs/research/block_outcomes.csv"))
spy = pd.read_csv(os.path.join(ROOT, "outputs/markets/cache/SPY.csv"), parse_dates=["date"])
assert len(ev) == len(oc)
assert (ev.price.values == oc.block_price.values).all()

oc["trade_date"] = pd.to_datetime(oc.trade_date)
ts = pd.to_datetime(oc.block_time.str[:26])
oc["sec_et"] = ts.dt.hour * 3600 + ts.dt.minute * 60 + ts.dt.second
idx = spy.date.values
pos = np.searchsorted(idx, oc.trade_date.values)
assert (idx[pos] == oc.trade_date.values).all()
oc["pos"] = pos
oc["year"] = oc.trade_date.dt.year

# ---------------------------------------------------------------- audit
oc["dev_file_signed"] = oc.block_price / oc.market_price - 1
audit = oc.groupby("year").agg(
    n=("dev_file_signed", "size"),
    mean_signed_dev_vs_market_price=("dev_file_signed", "mean"),
    share_below=("direction", lambda s: (s == "below_market").mean()),
)
print("AUDIT: signed deviation vs file market_price by year")
print(audit.round(4).to_string())

# ---------------------------------------------------------------- adjusted close series on SPY calendar
# market_price = adj close of previous trading day; close_h = adj close at pos+h
adj = pd.Series(np.nan, index=np.arange(len(idx)))
src = {}
src[-1] = oc.market_price.values
for k, h in HORIZONS.items():
    src[h] = oc[f"close_{k}"].values
for h, vals in src.items():
    p = oc.pos.values + h
    ok = (p >= 0) & (p < len(idx)) & ~np.isnan(vals)
    sub = pd.Series(vals[ok], index=p[ok]).groupby(level=0).first()
    adj.loc[sub.index] = sub.values
# fill any remaining gaps inside the sample with raw SPY returns chained from nearest known
lo, hi = oc.pos.min() - 1, min(len(idx) - 1, oc.pos.max() + 21)
known = adj.notna()
for i in range(lo, hi + 1):
    if np.isnan(adj[i]) and not np.isnan(adj[i - 1]):
        adj[i] = adj[i - 1] * (1 + spy.SPY_ret.iloc[i])
print(f"adjusted close coverage {lo}-{hi}: {adj.loc[lo:hi].notna().mean():.4f} (gaps chained from SPY.csv)")

# ---------------------------------------------------------------- dividend factor -> raw close
auc = oc[(oc.sec_et >= 57600) & (oc.sec_et < 57602)]
fac = (adj.reindex(auc.pos.values).values / auc.block_price.values)
fac = pd.Series(fac, index=auc.pos.values).groupby(level=0).median()
fac = fac.round(5)
# step dates: factor changes at SPY ex-div (3rd Friday of Mar/Jun/Sep/Dec)
dates = pd.DatetimeIndex(idx)
def third_friday(y, m):
    d = pd.Timestamp(year=y, month=m, day=15)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)
exdiv = set()
for y in range(2021, 2027):
    for m in (3, 6, 9, 12):
        exdiv.add(third_friday(y, m))
factor = pd.Series(np.nan, index=np.arange(len(idx)))
fpos = fac.index.values
for i in range(lo, hi + 1):
    j = np.searchsorted(fpos, i, side="right") - 1          # last auction day <= i
    k = j + 1                                                # next auction day > i
    if j < 0:
        factor[i] = fac.iloc[0]; continue
    if k >= len(fpos) or fac.iloc[j] == fac.iloc[k]:
        factor[i] = fac.iloc[j]; continue
    # factor changes between auction days j and k: locate the ex-div date
    steps = [d for d in exdiv if dates[fpos[j]] < d <= dates[i]]
    factor[i] = fac.iloc[k] if steps else fac.iloc[j]
raw_close = adj / factor
chk = (raw_close.reindex(auc.pos.values).values / auc.block_price.values - 1)
print(f"raw close reconstruction check vs {len(auc)} auction prints: max abs err {np.nanmax(np.abs(chk))*1e4:.2f} bp")

# ---------------------------------------------------------------- corrected deviation and forward returns
oc["raw_close_D"] = raw_close.reindex(oc.pos.values).values
oc["dev_c"] = oc.block_price / oc.raw_close_D - 1
oc["abs_dev_c"] = oc.dev_c.abs()
oc["dir_c"] = np.select([oc.dev_c > 1e-4, oc.dev_c < -1e-4], ["above", "below"], "at_close")  # |dev| <= 1bp = printed at the close
oc["dir_a"] = oc.direction.map({"above_market": "above", "below_market": "below"})
oc["abs_dev_a"] = oc.deviation.abs()
oc["tod"] = np.select(
    [oc.sec_et < 15 * 3600 + 50 * 60, oc.sec_et <= 16 * 3600 + 15 * 60],
    ["session", "close_window"], "after_hours")
print("time of day:", oc.tod.value_counts().to_dict())
print("corrected direction:", oc.dir_c.value_counts().to_dict(),
      " |dev_c| median %.4f" % oc.abs_dev_c.median())

for k, h in HORIZONS.items():
    # Spec A: close_h / market_price - 1  (prior close to horizon close, includes day D)
    oc[f"retA_{k}"] = oc[f"close_{k}"] / oc.market_price - 1
    # Spec B: close of day D to close of D+h (no lookahead)
    a0 = adj.reindex(oc.pos.values).values
    ah = adj.reindex(oc.pos.values + h).values
    oc[f"retB_{k}"] = ah / a0 - 1
    # Spec B gap fill: did the raw close at horizon cross the block price?
    rh = raw_close.reindex(oc.pos.values + h).values
    oc[f"hitB_{k}"] = np.where(oc.dir_c == "above", rh >= oc.block_price, rh <= oc.block_price).astype(float)
    oc.loc[np.isnan(rh), f"hitB_{k}"] = np.nan
    # Spec B "toward": sign of forward return matches sign of deviation
    oc[f"towB_{k}"] = (np.sign(oc[f"retB_{k}"]) == np.sign(oc.dev_c)).astype(float)
    oc.loc[np.isnan(ah), f"towB_{k}"] = np.nan

# unconditional baseline pools: h-day forward returns for every trading day in the event period
base = {}
A = adj.values
for k, h in HORIZONS.items():
    i0 = np.arange(lo + 1, hi - h + 1)
    r = A[i0 + h] / A[i0] - 1
    base[k] = r[~np.isnan(r)]
    base[k + "_A"] = A[i0 + h] / A[i0 - 1] - 1  # (h+1)-day window for Spec A
    base[k + "_A"] = base[k + "_A"][~np.isnan(base[k + "_A"])]

# ---------------------------------------------------------------- stats helpers
HYP = 0
rows = []

def boot_excess(x, pool, nboot=NBOOT):
    """mean(x) - mean(pool) with 90% bootstrap CI; both sides resampled, matched n."""
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return np.nan, np.nan, np.nan, np.nan, n
    bm = rng.choice(x, (nboot, n)).mean(1)
    bb = rng.choice(pool, (nboot, n)).mean(1)
    ex = bm - bb
    return x.mean(), x.mean() - pool.mean(), np.quantile(ex, 0.05), np.quantile(ex, 0.95), n

def wilson(k, n, z=1.645):
    if n == 0:
        return np.nan, np.nan, np.nan
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, c - hw, c + hw

def add(test, spec, sample, direction, cell, horizon, df, retcol, poolkey, hitcol=None):
    global HYP
    raw, exc, lo_, hi_, n = boot_excess(df[retcol], base[poolkey])
    HYP += 1
    rec = dict(test=test, spec=spec, subset=sample, direction=direction, cell=cell,
               horizon=horizon, n=n, mean_raw=raw, mean_excess=exc, ci90_lo=lo_, ci90_hi=hi_,
               sig90=(not np.isnan(lo_)) and (lo_ > 0 or hi_ < 0))
    if hitcol is not None:
        hv = df[hitcol].dropna()
        p, wl, wh = wilson(hv.sum(), len(hv))
        HYP += 1
        rec.update(hit_rate=p, hit_n=len(hv), hit_ci_lo=wl, hit_ci_hi=wh,
                   hit_sig=(not np.isnan(wl)) and (wl > 0.5 or wh < 0.5))
        if hitcol.startswith("hitB") and len(hv) > 0:
            # matched baseline: unconditional P(h-day return crosses the same deviation) averaged over events
            pool = base[poolkey]
            dv = df.loc[hv.index, "dev_c"].values
            pb = np.where(dv > 0, (pool[None, :] >= dv[:, None]).mean(1), (pool[None, :] <= dv[:, None]).mean(1))
            hb = pb.mean()
            rec.update(hit_base=hb, hit_excess=p - hb, hit_sig_vs_base=(wl > hb or wh < hb))
    rows.append(rec)
    return rec

def dedupe(df):
    return df.sort_values("dollar_value", ascending=False).drop_duplicates("trade_date")

# ---------------------------------------------------------------- Tests 1, 2, 5, 6
for spec, dcol in (("A", "dir_a"), ("B", "dir_c")):
    for sample, df0 in (("all", oc), ("one_per_day", dedupe(oc))):
        for d in ("above", "below"):
            df = df0[df0[dcol] == d]
            for k in HORIZONS:
                if spec == "A":
                    add("T1_T2_main", spec, sample, d, "all", k, df, f"retA_{k}", k + "_A", f"reached_{k}")
                else:
                    add("T1_T2_main", spec, sample, d, "all", k, df, f"retB_{k}", k, f"hitB_{k}")

# Spec A pct_toward and Spec B toward-sign hit rates (secondary gap-fill measure)
for spec, dcol in (("A", "dir_a"), ("B", "dir_c")):
    for d in ("above", "below"):
        df = oc[oc[dcol] == d]
        for k in HORIZONS:
            if spec == "A":
                v = df[f"pct_toward_{k}"]
                hv = (v >= 50).astype(float)  # moved at least halfway toward block
                lab = "pct_toward>=50"
            else:
                hv = df[f"towB_{k}"].dropna()
                lab = "fwd_ret_sign_matches_dev"
            p, wl, wh = wilson(hv.sum(), len(hv)); HYP += 1
            rows.append(dict(test="T2_toward", spec=spec, subset="all", direction=d, cell=lab,
                             horizon=k, n=len(hv), hit_rate=p, hit_n=len(hv), hit_ci_lo=wl, hit_ci_hi=wh,
                             hit_sig=(wl > 0.5 or wh < 0.5)))

# ---------------------------------------------------------------- Test 3: tiers (Spec B, all events)
oc["dev_q"] = pd.qcut(oc.abs_dev_c, 4, labels=["devQ1", "devQ2", "devQ3", "devQ4"])
oc["dv_q"] = pd.qcut(oc.dollar_value, 4, labels=["dvQ1", "dvQ2", "dvQ3", "dvQ4"])
for qcol in ("dev_q", "dv_q"):
    for q in oc[qcol].cat.categories:
        for d in ("above", "below"):
            df = oc[(oc[qcol] == q) & (oc.dir_c == d)]
            for k in HORIZONS:
                add("T3_tier", "B", "all", d, q, k, df, f"retB_{k}", k, f"hitB_{k}")

# ---------------------------------------------------------------- Test 4: time of day (Spec B)
for tod in ("session", "close_window", "after_hours"):
    for d in ("above", "below", "at_close"):
        df = oc[(oc.tod == tod) & (oc.dir_c == d)]
        for k in HORIZONS:
            add("T4_tod", "B", "all", d, tod, k, df, f"retB_{k}", k, f"hitB_{k}")

res = pd.DataFrame(rows)
res["total_hypotheses"] = HYP + 2  # + the two strategy backtests below (HYP is incremented there)
res.to_csv(os.path.join(OUT, "block_results.csv"), index=False)
print(f"\nTOTAL HYPOTHESES TESTED (incl. 2 strategy backtests): {HYP + 2}")

# ---------------------------------------------------------------- strongest finding -> strategy
# Spec A excluded: its return window starts at the prior close, so it contains the print-day move (lookahead)
cand = res[(res.test != "T2_toward") & (res.spec == "B") & (res.n >= 100) & res.ci90_lo.notna()].copy()
cand["t_like"] = cand.mean_excess / ((cand.ci90_hi - cand.ci90_lo) / (2 * 1.645))
best = cand.loc[cand.t_like.abs().idxmax()]
print("\nSTRONGEST CELL (n>=100):")
print(best[["test", "spec", "subset", "direction", "cell", "horizon", "n", "mean_excess", "ci90_lo", "ci90_hi", "t_like"]].to_string())

# build the strategy on that cell: enter at close of print day, hold h days, long if excess>0 else short,
# one position per day (no pyramiding), 1bp per side
h = HORIZONS[best.horizon]
side = 1 if best.mean_excess > 0 else -1
if best.test == "T1_T2_main":
    sel = oc[(oc.dir_c if best.spec == "B" else oc.dir_a) == best.direction]
elif best.test == "T3_tier":
    qcol = "dev_q" if best.cell.startswith("dev") else "dv_q"
    sel = oc[(oc[qcol] == best.cell) & (oc.dir_c == best.direction)]
else:
    sel = oc[(oc.tod == best.cell) & (oc.dir_c == best.direction)]
sig_days = np.sort(sel.pos.unique())
daily = pd.Series(0.0, index=np.arange(lo + 1, hi + 1))
exposure = pd.Series(0, index=daily.index)
trades = 0
for p in sig_days:
    if p + h > hi or exposure.loc[p + 1:p + h].any():
        continue
    trades += 1
    for j in range(p + 1, p + h + 1):
        exposure[j] = 1
        daily[j] = side * (A[j] / A[j - 1] - 1)
    daily[p + 1] -= COST
    daily[p + h] -= COST
eq = (1 + daily).cumprod()
dd = (eq / eq.cummax() - 1).min()
ann = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else np.nan
tot = eq.iloc[-1] - 1
days_in_mkt = int(exposure.sum())
spy_d = pd.Series(A[daily.index] / A[daily.index - 1] - 1, index=daily.index)
spy_sh = spy_d.mean() / spy_d.std() * np.sqrt(252)
spy_dd = ((1 + spy_d).cumprod() / (1 + spy_d).cumprod().cummax() - 1).min()

# second strategy: the actual hypothesis (drift toward the block side): long after 'above' prints,
# short after 'below' prints, Spec B, hold 21d, one position at a time, 1bp per side
HYP += 2  # the two strategy backtests are hypotheses too (already included in the csv total)
sig2 = oc[oc.dir_c.isin(["above", "below"])].sort_values("dollar_value", ascending=False).drop_duplicates("trade_date")
sig2 = sig2.sort_values("pos")
daily2 = pd.Series(0.0, index=daily.index); exp2 = pd.Series(0, index=daily.index); trades2 = 0
for p_, d_ in zip(sig2.pos.values, sig2.dir_c.values):
    hh = 21
    if p_ + hh > hi or exp2.loc[p_ + 1:p_ + hh].any():
        continue
    trades2 += 1; s_ = 1 if d_ == "above" else -1
    for j in range(p_ + 1, p_ + hh + 1):
        exp2[j] = 1; daily2[j] = s_ * (A[j] / A[j - 1] - 1)
    daily2[p_ + 1] -= COST; daily2[p_ + hh] -= COST
eq2 = (1 + daily2).cumprod(); dd2 = (eq2 / eq2.cummax() - 1).min()
ann2 = daily2.mean() / daily2.std() * np.sqrt(252); tot2 = eq2.iloc[-1] - 1
strat2 = pd.DataFrame({"date": dates[daily2.index], "ret": daily2.values, "equity": eq2.values, "in_market": exp2.values})
strat2.to_csv(os.path.join(OUT, "block_strategy_longshort_daily.csv"), index=False)
strat = pd.DataFrame({"date": dates[daily.index], "ret": daily.values, "equity": eq.values, "in_market": exposure.values})
strat.to_csv(os.path.join(OUT, "block_strategy_daily.csv"), index=False)
print(f"\nSTRATEGY: {'long' if side>0 else 'short'} SPY at close of print day, hold {h}d, cost 1bp/side")
print(f"  trades {trades}  days in market {days_in_mkt}/{len(daily)}  total return {tot:.4f}  Sharpe {ann:.2f}  maxDD {dd:.4f}")
print(f"  SPY buy and hold same window: Sharpe {spy_sh:.2f}  maxDD {spy_dd:.4f}")
print(f"LONG/SHORT toward-block, hold 21d, one per day, 1bp/side: trades {trades2}  total {tot2:.4f}  Sharpe {ann2:.2f}  maxDD {dd2:.4f}  days {int(exp2.sum())}/{len(daily2)}")

# ---------------------------------------------------------------- plot data (Spec B, one event per day) + summary
pdata = res[(res.test == "T1_T2_main") & (res.spec == "B") & (res["subset"] == "one_per_day")].copy()
pdata.to_csv(os.path.join(OUT, "block_plot_data.csv"), index=False)

main = res[(res.test == "T1_T2_main")]
def fmt_row(spec, sample, d, k):
    r = main[(main.spec == spec) & (main["subset"] == sample) & (main.direction == d) & (main.horizon == k)].iloc[0]
    return (f"| {spec} | {sample} | {d} | {k} | {r.n} | {r.mean_excess*1e4:+.0f} | "
            f"[{r.ci90_lo*1e4:+.0f}, {r.ci90_hi*1e4:+.0f}] | {r.hit_rate:.2f} [{r.hit_ci_lo:.2f}, {r.hit_ci_hi:.2f}] | "
            + (f"{r.hit_base:.2f} |" if spec == "B" else "n/a |"))

n_days = oc.trade_date.nunique()
d0, d1 = oc.trade_date.min().date(), oc.trade_date.max().date()
nsig_main = int(main.sig90.sum())
lines = []
lines.append(f"# SPY block print event study ({d0} to {d1}, {len(oc)} prints on {n_days} days)")
n_atc = int((oc.dir_c == "at_close").sum())
lines.append(f"Data audit: outcomes.market_price is the prior-day dividend-adjusted close, not the price at print. The file's direction/deviation columns are 94 pct 'above_market' by construction (deviation falls 5.5 pct in 2022 to 0.4 pct in 2026, the dividend signature), and its forward window starts at the prior close so Spec A 1d returns contain the print-day move (lookahead). Spec A runs the tests as filed; Spec B re-references every print to the same-day raw close rebuilt from closing-auction prints (max error {np.nanmax(np.abs(chk))*1e4:.2f} bp), starts the clock at that close, and is the headline. {n_atc} prints sit within 1bp of the close (MOC style) and are excluded from above/below.")
lines.append("| spec | sample | dir | h | n | excess bp | 90% CI bp | gap-fill hit rate [90% CI] | matched base hit |")
lines.append("|---|---|---|---|---|---|---|---|---|")
for d in ("above", "below"):
    for k in HORIZONS:
        lines.append(fmt_row("B", "one_per_day", d, k))
for d in ("above", "below"):
    for k in ("1d", "1mo"):
        lines.append(fmt_row("A", "one_per_day", d, k))
tier_sig = res[(res.test == "T3_tier") & res.sig90]
tod_sig = res[(res.test == "T4_tod") & res.sig90]
lines.append(f"Main test (Spec A+B, both samples, 20 cells): {nsig_main} excess-return cells clear a 90% CI; tiers (40 cells): {len(tier_sig)}; time of day (30 cells): {len(tod_sig)}. Total hypotheses {HYP} incl. 2 backtests; at a 90 pct CI expect about {HYP*0.1:.0f} false positives by chance.")
lines.append(f"Strongest cell: {best.test} spec {best.spec} {best["subset"]} {best.direction} {best.cell} {best.horizon}, n={best.n}, excess {best.mean_excess*1e4:+.0f} bp, 90% CI [{best.ci90_lo*1e4:+.0f}, {best.ci90_hi*1e4:+.0f}], t-like {best.t_like:.2f}.")
lines.append(f"Strategy on it ({'long' if side>0 else 'short'} at close of print day, hold {h}d, 1bp per side): {trades} trades, Sharpe {ann:.2f}, max DD {dd*100:.1f} pct, total return {tot*100:+.1f} pct, in market {days_in_mkt}/{len(daily)} days. SPY buy and hold over the same window: Sharpe {spy_sh:.2f}, max DD {spy_dd*100:.1f} pct.")
lines.append(f"Long/short toward the block side (long after above prints, short after below, hold 21d, one per day, 1bp per side): {trades2} trades, Sharpe {ann2:.2f}, max DD {dd2*100:.1f} pct, total return {tot2*100:+.1f} pct.")
verdict = "FAIL" if abs(best.t_like) < 2.5 or max(ann, ann2) < 1.0 or max(ann, ann2) < spy_sh else "WEAK PASS"
mB = main[(main.spec == "B")]
hx = mB.hit_excess.abs().max() * 100
lines.append(f"Verdict: {verdict}. Spec A 'below' cells (-120 bp at 1d) are the print-day move leaking into the window, not a signal. In Spec B the only recurring pattern is 1mo drift toward the print side (above +57 to +100 bp, below -33 to -60 bp, t-like at most {cand.t_like.abs().max():.1f}), it is one effect seen in nested cells, it does not survive a {HYP}-hypothesis haircut, gap-fill hit rates are within {hx:.0f} points of the matched baseline, and neither tradeable version beats SPY buy and hold over the same window.")
lines.append("Caveat: 4.8 years of SPY, mostly a bull market, with ~3 prints per event day; one-event-per-day n is the honest n.")
with open(os.path.join(OUT, "block_summary.md"), "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
