#!/usr/bin/env python3
"""
spyRegimeHysteresisWFCV.py
===========================
Tests whether regime-transition HYSTERESIS (requiring N consecutive raw-regime
days to agree before the live routing's "effective regime" flips) beats
James's current production routing in predictSpy.py, which is an instant
single-day switch: `regime` (bull/bear/chop) flips the entry threshold
(0.51 chop / 0.53 bull-bear) the moment the precomputed `regime` column
changes value, with zero persistence requirement.

Zero-lookahead discipline (same pattern as chopVixSubRegimeWFCV.py):
  - 5 expanding walk-forward folds, each trained ONLY on rows strictly before
    the fold's 252-day validation window, predicting forward on that window.
  - The 5 folds are stacked back-to-back and cover the most recent 1260
    trading days (~5yr) of spy_features.csv. OOS predictions are POOLED
    across all 5 folds before any hysteresis/threshold logic is applied, so
    every variant (baseline + all three N values) is scored on the exact
    same pooled OOS date range -> a fair apples-to-apples comparison.
  - `regime` itself is a precomputed, no-lookahead column already in
    spy_features.csv (per task context) -- used as-is for raw-regime labels
    on each OOS day; no regime model is fit here.

SIMPLIFICATION (explicitly in scope per task): rather than reproducing
predictSpy.py's full model zoo (dedicated chop model, calibrated bear model,
GBM-chop blend, Kelly sizing, kill switch, chop-VIX gate...), this script
fits ONE universal model per fold -- a 50/50 blend of a Logistic Regression
and a Gradient Boosting classifier on next_dir_1d, both using
trainSpyModel.SPY_FEATURES with the same recency-weighting scheme
(half-life 1260 days) -- and layers regime-conditional THRESHOLDS on top of
that single blended probability. The hysteresis question under test is
*which regime label gates the threshold each day*, not which model is used,
so collapsing the per-regime model zoo to one universal blend isolates the
variable being tested while preserving the same zero-lookahead WFCV
discipline. Position is a simple binary signal (1 if blend prob > regime
threshold, else 0) -- no Kelly sizing / kill switch / chop-VIX gate, since
those are sizing overlays orthogonal to the regime-routing question.

Hysteresis rule tested (N = 0/2/3/5):
  - N=0 is the baseline: effective_regime[t] = raw_regime[t] (instant switch,
    reproduces production behaviour exactly).
  - N>0: effective_regime only changes to a new raw regime after that new
    regime has appeared on N CONSECUTIVE days in a row. Until confirmed, the
    PREVIOUS effective regime (and its threshold) stays in force. A broken
    streak (a day that disagrees with the pending new regime) resets the
    counter to 0 for that pending candidate.

Usage:
  venv/bin/python3 python/spyRegimeHysteresisWFCV.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from trainSpyModel import SPY_FEATURES, SPARSE_FEATURES, prep_df, compute_sample_weights, build_model

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
PLOT_PATH = os.path.expanduser("~/discordBot/outputs/markets/spy_regime_hysteresis_equity.png")

TX_COST    = 0.0005   # 5bps one-way, matches production backtests
ANNUAL     = 252
HALF_LIFE  = 1260     # production recency-weighting default
N_FOLDS    = 5
FOLD_SIZE  = 252

# Production thresholds (predictSpy.py: "Chop-lower (Run 35): threshold=0.51
# in chop regime, 0.53 in bull/bear")
THRESH_CHOP    = 0.51
THRESH_OTHER   = 0.53

HYSTERESIS_NS = [0, 2, 3, 5]

NAVY_BG   = "#02233F"
GRID_CLR  = "#274066"


def thresh_for_regime(regime):
    return THRESH_CHOP if regime == "chop" else THRESH_OTHER


def ann_sharpe(returns):
    returns = np.asarray(returns)
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(ANNUAL))


def max_drawdown(returns):
    """Returns (max_dd_fraction, equity_curve) from a daily-return series."""
    eq = np.cumprod(1 + np.asarray(returns))
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(dd.min()), eq


def run_walk_forward(df_raw):
    """Fit per-fold universal Logistic+GBM blend, strictly on prior data,
    predict forward on the next 252-day block. Pool OOS rows across 5 folds
    (same structural pattern as chopVixSubRegimeWFCV.py, applied to the full
    universal feature set instead of a chop-filtered subset)."""
    df, avail = prep_df(df_raw, SPY_FEATURES, "next_dir_1d")
    print(f"[spyRegimeHysteresisWFCV] {len(df)} usable rows after prep_df "
          f"({len(avail)}/{len(SPY_FEATURES)} features available), "
          f"{df.date.min().date()}-{df.date.max().date()}")

    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = FOLD_SIZE * N_FOLDS
    train_start_idx = n - total_window - FOLD_SIZE
    min_train_rows = 500

    oos_rows = []
    for i in range(N_FOLDS):
        fold_start = dates[train_start_idx + FOLD_SIZE + i * FOLD_SIZE]
        fold_end_idx = min(train_start_idx + FOLD_SIZE + (i + 1) * FOLD_SIZE - 1, n - 1)
        fold_end = dates[fold_end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < min_train_rows or len(va) < 20:
            print(f"  fold {i+1}: skipped (tr={len(tr)}, va={len(va)})")
            continue

        sw = compute_sample_weights(tr["date"], half_life_days=HALF_LIFE)
        X_tr, y_tr = tr[avail].values, tr["next_dir_1d"].values
        X_va = va[avail].values

        m_log = build_model("logistic", is_clf=True)
        m_gbm = build_model("gbm", is_clf=True)
        try:
            m_log.fit(X_tr, y_tr, **{"m__sample_weight": sw})
        except TypeError:
            m_log.fit(X_tr, y_tr)
        try:
            m_gbm.fit(X_tr, y_tr, **{"m__sample_weight": sw})
        except TypeError:
            m_gbm.fit(X_tr, y_tr)

        p_log = m_log.predict_proba(X_va)[:, 1]
        p_gbm = m_gbm.predict_proba(X_va)[:, 1]
        p_blend = 0.5 * p_log + 0.5 * p_gbm

        for j in range(len(va)):
            oos_rows.append({
                "date": va["date"].iloc[j],
                "p": p_blend[j],
                "y": va["next_dir_1d"].iloc[j],
                "ret": va["next_ret_1d"].iloc[j],
                "regime": va["regime"].iloc[j],
                "fold": i + 1,
            })
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  "
              f"tr={len(tr)} va={len(va)}  pooled OOS rows so far: {len(oos_rows)}")

    oos = pd.DataFrame(oos_rows).sort_values("date").reset_index(drop=True)
    oos = oos.dropna(subset=["ret"])  # drop any trailing day with no realized next-day return
    return oos


def apply_hysteresis(raw_regimes, n_confirm):
    """
    raw_regimes: list/array of raw regime labels in chronological order.
    n_confirm: 0 = instant switch (effective == raw always).
               N>0: effective only switches to a new regime once that regime
               has shown up N consecutive days in a row; a broken streak
               resets the pending-candidate counter.
    Returns (effective_regimes list, n_switches int, disagreement_days int).
    """
    raw_regimes = list(raw_regimes)
    if n_confirm <= 0:
        eff = raw_regimes[:]
        n_switches = sum(1 for i in range(1, len(eff)) if eff[i] != eff[i - 1])
        return eff, n_switches, 0

    eff = []
    effective = raw_regimes[0]
    pending_regime = None
    pending_count = 0
    n_switches = 0
    disagreement_days = 0

    for i, r in enumerate(raw_regimes):
        if i == 0:
            eff.append(effective)
            continue
        if r == effective:
            pending_regime = None
            pending_count = 0
        else:
            if r == pending_regime:
                pending_count += 1
            else:
                pending_regime = r
                pending_count = 1
            if pending_count >= n_confirm:
                effective = r
                n_switches += 1
                pending_regime = None
                pending_count = 0
        eff.append(effective)
        if raw_regimes[i] != effective:
            disagreement_days += 1

    return eff, n_switches, disagreement_days


def backtest_variant(oos, n_confirm):
    eff, n_switches, disagreement_days = apply_hysteresis(oos["regime"].values, n_confirm)
    oos = oos.copy()
    oos["effective_regime"] = eff
    thresh = oos["effective_regime"].map(thresh_for_regime).values
    pos = (oos["p"].values > thresh).astype(float)

    turns = np.abs(np.diff(np.append(0.0, pos)))
    turnover = float(turns.sum())
    daily_ret = pos * oos["ret"].values - turns * TX_COST

    sharpe = ann_sharpe(daily_ret)
    mdd, eq = max_drawdown(daily_ret)
    total_return = float(eq[-1] - 1.0)
    n_days = len(daily_ret)
    ann_return = float((1.0 + total_return) ** (ANNUAL / n_days) - 1.0) if n_days > 0 else 0.0
    n_trades = int(np.sum(turns > 0))        # position flips (entries + exits)
    n_signals = int(pos.sum())               # active (long) days

    return {
        "n_confirm": n_confirm,
        "sharpe": sharpe,
        "max_dd": mdd,
        "total_return": total_return,
        "ann_return": ann_return,
        "n_switches": n_switches,
        "n_trades": n_trades,
        "n_signals": n_signals,
        "turnover": turnover,
        "disagreement_days": disagreement_days,
        "n_days": n_days,
        "daily_ret": daily_ret,
        "equity": eq,
        "dates": oos["date"].values,
    }


def make_plot(baseline_res, best_res, best_n):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor(NAVY_BG)
    ax.set_facecolor(NAVY_BG)

    dates_b = pd.to_datetime(baseline_res["dates"])
    dates_h = pd.to_datetime(best_res["dates"])

    ax.plot(dates_b, baseline_res["equity"], color="#FF6B6B", linewidth=1.8,
            label=f"Baseline (N=0, instant switch)  Sharpe={baseline_res['sharpe']:.2f}")
    ax.plot(dates_h, best_res["equity"], color="#4ECB71", linewidth=1.8,
            label=f"Hysteresis N={best_n}  Sharpe={best_res['sharpe']:.2f}")

    ax.set_title("SPY Regime Routing: Instant-Switch vs Hysteresis -- Pooled WFCV OOS Equity",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative growth of $1 (net of 5bps TX cost)", color="white")

    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID_CLR)
    ax.grid(True, color=GRID_CLR, alpha=0.6, linewidth=0.6)

    leg = ax.legend(facecolor=NAVY_BG, edgecolor=GRID_CLR, labelcolor="white", loc="upper left")

    caption = ("Zero-lookahead walk-forward pooled OOS (5 folds x 252d); universal\n"
               "Logistic+GBM blend per fold; regime-conditional thresholds 0.51 chop / 0.53 bull-bear;\n"
               "hysteresis = N consecutive raw-regime days required before the effective (routing) regime flips.")
    fig.text(0.5, -0.02, caption, color="white", fontsize=8.5, ha="center", va="top")

    fig.tight_layout()
    fig.savefig(PLOT_PATH, facecolor=NAVY_BG, dpi=150, bbox_inches="tight")
    print(f"\nSaved plot: {PLOT_PATH}")


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos = run_walk_forward(df_raw)
    print(f"\nTotal pooled OOS rows (zero lookahead): {len(oos)}  "
          f"{pd.to_datetime(oos['date'].min()).date()} - {pd.to_datetime(oos['date'].max()).date()}")
    print("Raw regime composition of pooled OOS set:")
    print(oos["regime"].value_counts())

    results = {}
    for n_confirm in HYSTERESIS_NS:
        results[n_confirm] = backtest_variant(oos, n_confirm)

    print("\n" + "=" * 100)
    print("REGIME-ROUTING HYSTERESIS COMPARISON (same pooled OOS dates for every variant)")
    print("=" * 100)
    hdr = (f"{'variant':>18} {'Sharpe':>8} {'MaxDD':>8} {'TotalRet':>9} {'AnnRet':>8} "
           f"{'#switch':>8} {'#trades':>8} {'#signals':>9} {'turnover':>9} {'disagree_d':>11}")
    print(hdr)
    for n_confirm in HYSTERESIS_NS:
        r = results[n_confirm]
        label = "baseline(N=0)" if n_confirm == 0 else f"N={n_confirm}"
        print(f"{label:>18} {r['sharpe']:>8.3f} {r['max_dd']*100:>7.2f}% {r['total_return']*100:>8.2f}% "
              f"{r['ann_return']*100:>7.2f}% {r['n_switches']:>8d} {r['n_trades']:>8d} {r['n_signals']:>9d} "
              f"{r['turnover']:>9.1f} {r['disagreement_days']:>11d}")

    baseline = results[0]
    hyst_variants = {n: results[n] for n in HYSTERESIS_NS if n != 0}
    best_n = max(hyst_variants, key=lambda n: hyst_variants[n]["sharpe"])
    best = hyst_variants[best_n]

    sharpe_delta = best["sharpe"] - baseline["sharpe"]
    ret_delta = best["total_return"] - baseline["total_return"]
    dd_delta = best["max_dd"] - baseline["max_dd"]  # negative = worse (more negative), so improvement = dd_delta>0

    print("\n" + "-" * 100)
    print(f"Best hysteresis variant by Sharpe: N={best_n}")
    print(f"  Sharpe:      {baseline['sharpe']:+.3f} (baseline) -> {best['sharpe']:+.3f} (N={best_n})  "
          f"delta={sharpe_delta:+.3f}")
    print(f"  Total return:{baseline['total_return']*100:+.2f}% (baseline) -> {best['total_return']*100:+.2f}% "
          f"(N={best_n})  delta={ret_delta*100:+.2f}pp")
    print(f"  Max drawdown:{baseline['max_dd']*100:+.2f}% (baseline) -> {best['max_dd']*100:+.2f}% (N={best_n})  "
          f"delta={dd_delta*100:+.2f}pp ({'improved (shallower)' if dd_delta>0 else 'worse (deeper)'})")
    verdict = "NET IMPROVEMENT" if sharpe_delta > 0 and ret_delta >= 0 else (
        "MIXED" if sharpe_delta > 0 or ret_delta > 0 else "NO IMPROVEMENT")
    print(f"  Verdict: {verdict}")

    make_plot(baseline, best, best_n)


if __name__ == "__main__":
    main()
