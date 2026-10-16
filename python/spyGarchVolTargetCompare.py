#!/usr/bin/env python3
"""
spyGarchVolTargetCompare.py
=============================
Compares GARCH-forecast vol-targeting (1-day-ahead GJR-GARCH-t conditional
vol, outputs/features/markets/spy_garch_vol_forecast.csv, built by
r/buildSpyGarchVolForecast.R) against the existing realized-21d-vol
targeting (spyVolTargetWFCV.py) on the IDENTICAL WFCV OOS days / classifier
output, so Sharpe deltas are attributable purely to the vol-forecast input,
not to anything else changing.

Both overlays use the EXACT SAME sizing rule shape:
    raw_kelly_f = 0.50 * (2p - 1)
    scaled = raw_kelly_f * (target_ann_vol / vol_forecast)
    position = apply_gates(scaled)   # same kill-switch/chop-vix/bear-halving
The only difference is vol_forecast: realized_vol_21d (trailing, backward
looking) vs garch_vol_ann (GJR-GARCH-t 1-day-ahead forecast, zero
lookahead by construction in the R walk-forward roll).

Usage:
  venv/bin/python3 python/spyGarchVolTargetCompare.py
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

from spyVolTargetWFCV import (run_wfcv, attach_realized_vol, base_kelly_fraction,
                               apply_gates, backtest_positions, ann_sharpe, max_drawdown,
                               ANNUAL)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
GARCH_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_garch_vol_forecast.csv")
OUT_PLOT = os.path.expanduser("~/discordBot/outputs/markets/spy_garch_voltarget_compare.png")


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos = run_wfcv(df_raw)
    oos = attach_realized_vol(oos, df_raw)

    garch = pd.read_csv(GARCH_PATH, parse_dates=["date"])
    print(f"GARCH forecast: {len(garch)} rows, {garch['date'].min().date()} -> {garch['date'].max().date()}")

    oos = oos.merge(garch, on="date", how="left")
    n_missing = oos["garch_vol_ann"].isna().sum()
    print(f"OOS days: {len(oos)}, missing GARCH forecast: {n_missing}")
    if n_missing > 0:
        oos["garch_vol_ann"] = oos["garch_vol_ann"].ffill()
        print(f"  ffilled {n_missing} missing GARCH values")
    oos = oos.dropna(subset=["garch_vol_ann"]).reset_index(drop=True)
    print(f"Final comparable OOS window: {len(oos)} days, {oos['date'].min().date()} -> {oos['date'].max().date()}")

    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"target_ann_vol (full-sample median realized 21d vol): {target_ann_vol:.4f}")

    raw_kelly = base_kelly_fraction(oos["p"].values)

    # ── Baseline (fixed 50% Kelly, no vol-targeting) ─────────────────────
    pos_base = apply_gates(oos, raw_kelly)
    bt_base = backtest_positions(oos, pos_base)

    # ── Realized-21d vol-targeted (existing production approach) ────────
    vol_scalar_rv = target_ann_vol / oos["realized_vol_21d"].values
    vol_scalar_rv = np.nan_to_num(vol_scalar_rv, nan=1.0, posinf=1.0, neginf=1.0)
    pos_rv = apply_gates(oos, raw_kelly * vol_scalar_rv)
    bt_rv = backtest_positions(oos, pos_rv)

    # ── GARCH-forecast vol-targeted (candidate) ──────────────────────────
    vol_scalar_garch = target_ann_vol / oos["garch_vol_ann"].values
    vol_scalar_garch = np.nan_to_num(vol_scalar_garch, nan=1.0, posinf=1.0, neginf=1.0)
    pos_garch = apply_gates(oos, raw_kelly * vol_scalar_garch)
    bt_garch = backtest_positions(oos, pos_garch)

    def fmt(bt, label):
        print(f"\n--- {label} ---")
        print(f"  Sharpe (ann):     {bt['sharpe']:+.3f}")
        print(f"  Max drawdown:     {bt['max_dd']*100:+.2f}%")
        print(f"  Total return:     {bt['total_return']*100:+.2f}%")
        print(f"  Ann. return:      {bt['ann_return']*100:+.2f}%")
        print(f"  Turnover:         {bt['turnover']:.2f}")
        print(f"  N trades:         {bt['n_trades']}")

    print("\n" + "=" * 70)
    print("BASELINE vs REALIZED-21D-VOL-TARGETED vs GARCH-FORECAST-VOL-TARGETED")
    print("=" * 70)
    fmt(bt_base, "BASELINE (fixed 50% Kelly, no vol-target)")
    fmt(bt_rv, "REALIZED-21D vol-targeted (current production)")
    fmt(bt_garch, "GARCH-FORECAST (GJR-GARCH-t 1-day-ahead) vol-targeted")

    print(f"\nDelta GARCH vs realized-21d: Sharpe {bt_garch['sharpe']-bt_rv['sharpe']:+.3f}  "
          f"maxDD {(bt_garch['max_dd']-bt_rv['max_dd'])*100:+.2f}pp  "
          f"total_ret {(bt_garch['total_return']-bt_rv['total_return'])*100:+.2f}pp  "
          f"turnover {bt_garch['turnover']-bt_rv['turnover']:+.2f}")

    # vol forecast comparison stats (how different are the two inputs?)
    corr = np.corrcoef(oos["realized_vol_21d"].values, oos["garch_vol_ann"].values)[0, 1]
    print(f"\nVol-forecast correlation (realized_21d vs garch_ann): {corr:.3f}")
    print(f"  realized_21d: median={oos['realized_vol_21d'].median():.4f} "
          f"std={oos['realized_vol_21d'].std():.4f}")
    print(f"  garch_ann:    median={oos['garch_vol_ann'].median():.4f} "
          f"std={oos['garch_vol_ann'].std():.4f}")

    # per-fold breakdown
    print("\n" + "=" * 70)
    print("Per-fold Sharpe breakdown")
    print("=" * 70)
    for fid in sorted(oos["fold"].unique()):
        sub = oos[oos["fold"] == fid]
        rk = base_kelly_fraction(sub["p"].values)
        vs_rv = np.nan_to_num(target_ann_vol / sub["realized_vol_21d"].values, nan=1.0, posinf=1.0, neginf=1.0)
        vs_g = np.nan_to_num(target_ann_vol / sub["garch_vol_ann"].values, nan=1.0, posinf=1.0, neginf=1.0)
        bt_rv_f = backtest_positions(sub, apply_gates(sub, rk * vs_rv))
        bt_g_f = backtest_positions(sub, apply_gates(sub, rk * vs_g))
        print(f"  fold {fid}: {sub['date'].min().date()}-{sub['date'].max().date()}  n={len(sub)}  "
              f"realized-21d Sharpe={bt_rv_f['sharpe']:+.3f}  GARCH Sharpe={bt_g_f['sharpe']:+.3f}  "
              f"delta={bt_g_f['sharpe']-bt_rv_f['sharpe']:+.3f}")

    # ── Navy-themed equity curve plot ────────────────────────────────────
    NAVY, GRID = "#02233F", "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    eq_base = np.cumprod(1 + bt_base["daily_ret"])
    eq_rv = np.cumprod(1 + bt_rv["daily_ret"])
    eq_garch = np.cumprod(1 + bt_garch["daily_ret"])
    ax.plot(oos["date"], eq_base, color="#5DADE2", lw=1.4,
            label=f"Fixed 50% Kelly (Sharpe {bt_base['sharpe']:+.2f})")
    ax.plot(oos["date"], eq_rv, color="#F5B041", lw=1.6,
            label=f"Realized-21d vol-targeted (Sharpe {bt_rv['sharpe']:+.2f})")
    ax.plot(oos["date"], eq_garch, color="#2E9E4E", lw=1.8,
            label=f"GARCH-forecast vol-targeted (Sharpe {bt_garch['sharpe']:+.2f})")
    ax.set_title("SPY Position Sizing: Realized-Vol vs GARCH-Forecast Vol-Targeting\n"
                  "(pooled walk-forward OOS, zero lookahead, 4-fold WFCV)",
                  color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative return (x)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"target_ann_vol={target_ann_vol:.1%} | GARCH=GJR-GARCH(1,1)-t, 750d moving window, "
              f"refit every 21d, 1-day-ahead | OOS n={len(oos)} days "
              f"({oos['date'].min().date()}-{oos['date'].max().date()})",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(OUT_PLOT, facecolor=NAVY, dpi=140)
    print(f"\nSaved plot: {OUT_PLOT}")


if __name__ == "__main__":
    main()
