#!/usr/bin/env python3
"""
volbook_mechanics_study.py  (pre-registered, Oct 20 2026)
==========================================================
Two tests on the MECHANICS of the live SPY short-vol book (python/volBook.py):
the exit rules (study A) and the sizing law (study B). Both use a synthetic
daily mark-to-market variance swap so the path-dependent exits can be
evaluated on 2011-2026 SPX data without an options history.

SYNTHETIC MARK-TO-MARKET MODEL
  Entry on day t: short a 30-calendar-day (21 trading day) variance swap at
  strike K = VIX_t^2 (vol-pt^2 units). On day t+k, k = 1..21, with r_j the
  SPX log return on day t+j:
     accrued_k   = (252e4 / 21) * sum_{j<=k} r_j^2
     remaining_k = (21 - k) / 21 * VIX_{t+k}^2
     V_k         = accrued_k + remaining_k
     PnL_k       = (K - V_k) / (2 * VIX_t)        vol points per unit vega
  At k = 21 remaining = 0 and PnL_21 = (VIX_t^2 - RV_fwd_var) / (2 VIX_t),
  exactly the hold-to-expiry P&L of vrp_regime_study / holdout_2011_2016_study.
  The script prints a verification line showing this reproduces the reference
  unconditional means on the same SPX file (holdout_2011_2016_daily.csv: 2.16
  vol pts on 2011-2016, 1.70 on 2016-2026; the vrp_regime_study figure of 1.9
  was on SPY returns, a different underlying).

CREDIT PROXY (stated plainly: the condor credit is PROXIED, not modelled)
  C_t = median of the hold-to-expiry P&L of the trailing 252 entries whose
  outcome is already known at t (entries t-273 .. t-22), expanding from a
  minimum of 63 known entries, floored at 0.5 vol pts so a negative or tiny
  median cannot make the triggers degenerate. Exit triggers in study A:
     profit take : PnL_k >= 0.5 * C_t            (live: mark <= 50% of credit)
     time exit   : k = 12 (21 DTE left of a 30 DTE position = 9 trading days
                   before expiry)                  (live: dte <= 21)
     stop        : PnL_k <= -3 * C_t  (as specified for this study; the live
                   rule, mark >= 3x credit, is a loss of 2x credit; a -2C
                   sensitivity is reported but is NOT the pre-registered arm)
  Same-day precedence: profit take, then stop, then time.
  Cost: 0.05 vol pts per entry if held to expiry (one round trip), 0.10 if
  exited early (two round trips).

ENTRIES
  Every day with the term structure NOT inverted (VIX9D <= VIX3M from the CBOE
  history files, lagged one day), overlapping, as in the reference studies.
  Ineligible days carry 0 so the stride-21 monthly series stays regular.

PERIODS
  holdout 2011-05-02 .. 2016-06-23 (effective start ~2011-09 because the
  credit proxy needs 63 known entries), H1 2016-06-24 .. 2021-06-30,
  H2 2021-07-01 .. last complete 21-day window.

METRICS (per policy/arm x period)
  mean per entry (eligible entries, net of cost), hit rate, monthly Sharpe
  (every 21st day, three offsets averaged, sqrt(12)), worst calendar-month
  mean of the daily series, NW t with 21 lags on the daily series.

STUDY A  exit policies
  (1) hold to expiry  (2) 50% PT only  (3) time exit k=12 only
  (4) PT + time (live without stop)  (5) PT + time + stop (LIVE)  (6) stop only
  PASS (pre-registered) for (5): monthly Sharpe > (1) AND worst calendar
  month > (1) in ALL THREE periods. Also reported: (5) vs (4) per period
  (does the stop help), exit-reason mix (PT / time / stop / expiry).

STUDY B  sizing law, same entries, hold-to-expiry P&L, size in vega units
  (a) constant 1
  (b) 20 / VIX_t                        (size 1 at VIX 20)
  (c) clip(0.11 / (VIX_t/100 * RATIO_BY_Q[gex_q]), 0.35, 1.25)  with
      RATIO_BY_Q and the trailing-252 GEX quintile (0-based) imported from
      python/spyVolRegime.py, rebuilt from spy_gex_daily.csv (available from
      about 2011-11 after 126 days of GEX history; scalar 1.0 before that,
      which is spyVolRegime's own no-data behaviour)
  (d) clip(0.11 / trailing-20d realised vol (annualised, decimal), 0.35, 1.25)
  Every arm is rescaled by one full-sample constant so its mean size over
  eligible days equals 1 (makes worst month comparable across arms; Sharpe is
  unaffected). Cost 0.05 vol pts x size per entry.
  PASS (pre-registered) for (b) over (a): worst calendar month better in all
  three periods AND monthly Sharpe >= Sharpe(a) - 0.1 in each period.
  Secondary: does (c) beat (b) or (d) anywhere (Sharpe and worst month both)?
  If not, the GEX lookup adds nothing to plain 1/VIX sizing.

CAVEATS  No skew (a 16-delta condor is not a variance swap), no gamma path
  dependence of a real condor mark, credit proxied, SPX cash index not SPY.

Outputs
  outputs/research/structural/volbook_mechanics_exits.csv
  outputs/research/structural/volbook_mechanics_sizing.csv
  outputs/research/structural/volbook_mechanics_reasons.csv
  outputs/research/structural/volbook_mechanics_summary.md
  outputs/research/structural/volbook_mechanics.png  (r/volbookMechanicsPlot.R)
"""
import os
import sys
import subprocess

import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/discordBot")
CACHE = os.path.join(BASE, "outputs/markets/cache")
OUT = os.path.join(BASE, "outputs/research/structural")
sys.path.insert(0, os.path.join(BASE, "python/research/structural"))
sys.path.insert(0, os.path.join(BASE, "python"))
from vrp_regime_study import nw_t  # noqa: E402
import spyVolRegime  # noqa: E402

H = 21
K_TIME = 12
PT_FRAC = 0.5
STOP_MULT = 3.0
STOP_MULT_LIVE = 2.0          # sensitivity only
COST_RT = 0.05
CREDIT_FLOOR = 0.5
CREDIT_WIN, CREDIT_MIN = 252, 63
PERIODS = [("holdout", "2011-05-02", "2016-06-23"), ("H1", "2016-06-24", "2021-06-30"), ("H2", "2021-07-01", "2099-12-31")]
POLICIES = {"1 hold": (False, False, False), "2 PT": (True, False, False), "3 time": (False, True, False),
            "4 PT+time": (True, True, False), "5 PT+time+stop (live)": (True, True, True), "6 stop": (False, False, True), "7 time+stop (-2x)": (False, True, True)}


def build():
    g = pd.read_csv(os.path.join(CACHE, "spy_gex_daily.csv"), parse_dates=["date"]).set_index("date").sort_index()
    vix = pd.read_csv(os.path.join(CACHE, "VIX.csv"), parse_dates=["date"]).set_index("date")["VIXCLS_val"]
    d = pd.DataFrame({"px": g["price"], "gex": g["gex"]})
    d["vix"] = vix.reindex(d.index, method="ffill")
    d["lr"] = np.log(d["px"]).diff()

    def cboe(name):
        c = pd.read_csv(os.path.join(CACHE, f"{name}_cboe_history.csv"))
        c["date"] = pd.to_datetime(c["DATE"]); return c.set_index("date")["CLOSE"]
    v9, v3 = cboe("VIX9D"), cboe("VIX3M")
    d["inverted"] = (v9.reindex(d.index, method="ffill") > v3.reindex(d.index, method="ffill")).astype(float).shift(1)
    # GEX quintile exactly as spyVolRegime._load (0-based, trailing 252, min 126), lagged one day
    rank = d["gex"].rolling(spyVolRegime.RANK_WINDOW, min_periods=126).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    d["gex_q"] = np.floor(rank * 5).clip(0, 4).shift(1)
    d["rv20"] = d["lr"].rolling(20).std() * np.sqrt(252)
    return d


def mtm_paths(d):
    """PnL_k matrix (N x 21) for every entry day, plus the hold-to-expiry check."""
    n = len(d); lr = d["lr"].values; vix = d["vix"].values
    R = np.full((n, H + 1), np.nan); VF = np.full((n, H + 1), np.nan)
    for k in range(1, H + 1):
        R[:n - k, k] = lr[k:]; VF[:n - k, k] = vix[k:]
    accrued = (252e4 / H) * np.nancumsum(np.where(np.isnan(R), 0, R ** 2), axis=1)
    accrued[np.isnan(R)] = np.nan
    ks = np.arange(H + 1)
    remaining = (H - ks)[None, :] / H * VF ** 2
    V = accrued + remaining
    K = (vix ** 2)[:, None]
    pnl = (K - V) / (2 * vix[:, None])
    return pnl[:, 1:]                    # columns k = 1..21


def credit_proxy(final):
    """Trailing-252 median of final P&L over entries whose outcome is known at t."""
    known = pd.Series(final).shift(H + 1)        # entry t-22 settles at t-1
    c = known.rolling(CREDIT_WIN, min_periods=CREDIT_MIN).median()
    return np.maximum(c.values, CREDIT_FLOOR)


def apply_policy(pnl, credit, use_pt, use_time, use_stop, stop_mult=STOP_MULT):
    n = pnl.shape[0]
    out = np.full(n, np.nan); reason = np.array(["none"] * n, dtype=object); kexit = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(credit[i]) or np.isnan(pnl[i, -1]):
            continue
        c = credit[i]; r = "expiry"; k_hit = H; p = pnl[i, -1]
        for k in range(1, H + 1):
            v = pnl[i, k - 1]
            if use_pt and v >= PT_FRAC * c:
                r, k_hit, p = "PT", k, v; break
            if use_stop and v <= -stop_mult * c:
                r, k_hit, p = "stop", k, v; break
            if use_time and k == K_TIME:
                r, k_hit, p = "time", k, v; break
        cost = COST_RT if r == "expiry" else 2 * COST_RT
        out[i] = p - cost; reason[i] = r; kexit[i] = k_hit
    return out, reason, kexit


def msharpe(s):
    vals = [s.iloc[k::H].mean() / s.iloc[k::H].std() * np.sqrt(12) for k in range(3) if s.iloc[k::H].std() > 0]
    return float(np.mean(vals)) if vals else np.nan


def metrics(s_active, elig):
    """s_active: P&L on eligible entry days (NaN elsewhere); elig: bool mask."""
    s = s_active.where(elig, 0.0).fillna(0.0)
    act = s_active[elig].dropna()
    cal = s.groupby(s.index.to_period("M")).mean()
    return {"n": int(len(act)), "mean_per_entry": round(float(act.mean()), 3), "hit": round(float((act > 0).mean()), 3),
            "monthly_sharpe": round(msharpe(s), 3), "worst_month": round(float(cal.min()), 2),
            "worst_month_date": str(cal.idxmin()), "nw_t": round(nw_t(s.values), 2)}


def period_slices(d):
    for name, a, b in PERIODS:
        m = (d.index >= pd.Timestamp(a)) & (d.index <= pd.Timestamp(b))
        yield name, m


def main():
    d = build()
    pnl = mtm_paths(d)
    final = pnl[:, -1]
    d["final"] = final
    # verification against the reference hold-to-expiry formula
    lr = d["lr"]
    rv_fwd_var = (lr.shift(-1) ** 2).rolling(H).mean().shift(-(H - 1)) * 252 * 1e4
    ref = (d["vix"] ** 2 - rv_fwd_var) / (2 * d["vix"])
    err = float(np.nanmax(np.abs(ref.values - final)))
    m_hold = float(ref[d.index < pd.Timestamp("2016-06-24")].mean()); m_is = float(ref[d.index >= pd.Timestamp("2016-06-24")].mean())
    print(f"VERIFY k=21 MTM == reference hold-to-expiry: max abs diff {err:.2e}; unconditional mean 2011-05..2016-06 {m_hold:.2f}, "
          f"2016-06..2026 {m_is:.2f} vol pts (reference holdout_2011_2016_daily.csv on the same SPX file: 2.16 / 1.70)")
    credit = credit_proxy(final)
    d["credit"] = credit
    complete = ~np.isnan(final)
    elig = (d["inverted"] == 0) & complete & ~np.isnan(credit)
    print(f"entries: {int(elig.sum())} eligible of {int(complete.sum())} complete windows; first entry with credit proxy {d.index[elig.values][0].date()}; "
          f"last complete window {d.index[complete][-1].date()}; credit proxy median {np.nanmedian(credit):.2f} vol pts")

    # ---------------- STUDY A ----------------
    rows, reasons, series = [], [], {}
    pol_runs = dict(POLICIES)
    pol_runs["5b PT+time+stop2x (sensitivity)"] = (True, True, True)
    for pname, (pt, tm, st) in pol_runs.items():
        sm = STOP_MULT_LIVE if (pname.startswith("5b") or pname.startswith("7")) else STOP_MULT
        p, r, kx = apply_policy(pnl, credit, pt, tm, st, sm)
        s_act = pd.Series(p, index=d.index)
        series[pname] = s_act.where(elig, 0.0).fillna(0.0)
        for per, m in period_slices(d):
            e = elig & m
            row = {"policy": pname, "period": per, **metrics(s_act, e)}
            rr = pd.Series(r[e.values]); mix = rr.value_counts(normalize=True)
            for key in ["PT", "time", "stop", "expiry"]:
                row[f"frac_{key}"] = round(float(mix.get(key, 0.0)), 3)
            row["mean_k_exit"] = round(float(np.nanmean(kx[e.values])), 1)
            rows.append(row)
            reasons.append({"policy": pname, "period": per, "PT": row["frac_PT"], "time": row["frac_time"], "stop": row["frac_stop"], "expiry": row["frac_expiry"]})
    ex = pd.DataFrame(rows)
    ex.to_csv(os.path.join(OUT, "volbook_mechanics_exits.csv"), index=False)
    pd.DataFrame(reasons).to_csv(os.path.join(OUT, "volbook_mechanics_reasons.csv"), index=False)
    pd.DataFrame(series).to_csv(os.path.join(OUT, "volbook_mechanics_exits_daily.csv"))

    def get(pol, per, col):
        return float(ex[(ex.policy == pol) & (ex.period == per)].iloc[0][col])
    a_checks = {}
    for per, _ in period_slices(d):
        a_checks[per] = {"sharpe_5_gt_1": get("5 PT+time+stop (live)", per, "monthly_sharpe") > get("1 hold", per, "monthly_sharpe"),
                         "worst_5_gt_1": get("5 PT+time+stop (live)", per, "worst_month") > get("1 hold", per, "worst_month")}
    a_pass = all(all(v.values()) for v in a_checks.values())
    stop_effect = {per: {"sharpe_5_minus_4": round(get("5 PT+time+stop (live)", per, "monthly_sharpe") - get("4 PT+time", per, "monthly_sharpe"), 3),
                         "worst_5_minus_4": round(get("5 PT+time+stop (live)", per, "worst_month") - get("4 PT+time", per, "worst_month"), 2)}
                   for per, _ in period_slices(d)}

    # ---------------- STUDY B ----------------
    vix = d["vix"]
    ratio = d["gex_q"].map(spyVolRegime.RATIO_BY_Q)
    exp_vol = vix / 100.0 * ratio
    sz = {"a const": pd.Series(1.0, index=d.index),
          "b 20/VIX": 20.0 / vix,
          "c regime lookup": (spyVolRegime.TARGET_VOL / exp_vol).clip(spyVolRegime.SCALAR_MIN, spyVolRegime.SCALAR_MAX).fillna(1.0),
          "d 0.11/RV20": (spyVolRegime.TARGET_VOL / d["rv20"]).clip(spyVolRegime.SCALAR_MIN, spyVolRegime.SCALAR_MAX)}
    n_lookup_missing = int((d["gex_q"].isna() & elig).sum())
    print(f"study B: regime lookup unavailable (scalar 1.0) on {n_lookup_missing} eligible entries, first available {d.index[d['gex_q'].notna().values][0].date()}")
    brows, bseries = [], {}
    hold_net = pd.Series(final, index=d.index) - COST_RT
    for arm, s in sz.items():
        s = s / float(s[elig].mean())           # one full-sample constant per arm
        p = hold_net * s
        bseries[arm] = p.where(elig, 0.0).fillna(0.0)
        for per, m in period_slices(d):
            e = elig & m
            brows.append({"arm": arm, "period": per, "mean_size": round(float(s[e].mean()), 3), "size_p10": round(float(s[e].quantile(0.1)), 3),
                          "size_p90": round(float(s[e].quantile(0.9)), 3), **metrics(p, e)})
    sb = pd.DataFrame(brows)
    sb.to_csv(os.path.join(OUT, "volbook_mechanics_sizing.csv"), index=False)
    pd.DataFrame(bseries).to_csv(os.path.join(OUT, "volbook_mechanics_sizing_daily.csv"))

    def getb(arm, per, col):
        return float(sb[(sb.arm == arm) & (sb.period == per)].iloc[0][col])
    b_checks = {per: {"worst_b_gt_a": getb("b 20/VIX", per, "worst_month") > getb("a const", per, "worst_month"),
                      "sharpe_b_within": getb("b 20/VIX", per, "monthly_sharpe") >= getb("a const", per, "monthly_sharpe") - 0.1}
                for per, _ in period_slices(d)}
    b_pass = all(all(v.values()) for v in b_checks.values())
    c_beats = {per: {"c_beats_b": getb("c regime lookup", per, "monthly_sharpe") > getb("b 20/VIX", per, "monthly_sharpe") and getb("c regime lookup", per, "worst_month") > getb("b 20/VIX", per, "worst_month"),
                     "c_beats_d": getb("c regime lookup", per, "monthly_sharpe") > getb("d 0.11/RV20", per, "monthly_sharpe") and getb("c regime lookup", per, "worst_month") > getb("d 0.11/RV20", per, "worst_month")}
               for per, _ in period_slices(d)}

    pd.set_option("display.width", 250)
    print("\nSTUDY A  exit policies (vol pts per unit vega, net of cost)")
    print(ex.drop(columns=["worst_month_date"]).to_string(index=False))
    print(f"\nA checks: {a_checks}\nA PASS RULE (live 5 beats hold 1 on Sharpe AND worst month in all three): {'PASS' if a_pass else 'FAIL'}")
    print(f"stop effect (5 minus 4): {stop_effect}")
    print("\nSTUDY B  sizing arms (hold to expiry, mean size normalised to 1)")
    print(sb.drop(columns=["worst_month_date"]).to_string(index=False))
    print(f"\nB checks: {b_checks}\nB PASS RULE (b over a: worst month better all three AND Sharpe within 0.1): {'PASS' if b_pass else 'FAIL'}")
    print(f"secondary, c beats b / d (Sharpe and worst month both): {c_beats}")

    with open(os.path.join(OUT, "volbook_mechanics_summary.md"), "w") as f:
        f.write("# Vol book mechanics: exit rules and sizing law (synthetic daily MTM variance swap)\n\n")
        f.write(f"Verification: k=21 MTM equals reference hold-to-expiry (max abs diff {err:.1e}); unconditional mean {m_hold:.2f} (2011-2016) / {m_is:.2f} (2016-2026) vol pts.\n\n")
        f.write(f"Study A PASS rule (live exits beat hold on Sharpe AND worst month in holdout, H1, H2): **{'PASS' if a_pass else 'FAIL'}** {a_checks}\n\n")
        f.write(f"Stop effect (5 minus 4): {stop_effect}\n\n")
        f.write(f"Study B PASS rule (20/VIX over constant: worst month better in all three AND Sharpe within 0.1): **{'PASS' if b_pass else 'FAIL'}** {b_checks}\n\n")
        f.write(f"Secondary (regime lookup beats 20/VIX or RV20 on both Sharpe and worst month): {c_beats}\n\n")
        f.write("## Study A exits\n\n" + ex.to_markdown(index=False) + "\n\n## Study B sizing\n\n" + sb.to_markdown(index=False) + "\n\n")
        f.write("Caveats: no skew, no gamma path dependence of a real condor mark, credit proxied by the trailing-252 median hold-to-expiry P&L (floor 0.5 vol pts), SPX cash index, study stop is -3x credit (live mark 3x credit is a 2x loss; row 5b is that sensitivity).\n")
    rp = os.path.join(BASE, "r/volbookMechanicsPlot.R")
    if os.path.exists(rp):
        r = subprocess.run(["Rscript", rp], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
