#!/usr/bin/env python3
"""
spyVolTargetWFCV.py
====================
LEAN re-run of the vol-targeting position-sizing overlay backtest.

A prior attempt at this exact task TIMED OUT (>600s/16 calls) because it
trained a Logistic + 8-seed GBM-ensemble blend per fold (5 folds). This
version is deliberately scoped down per the task brief:
  - ONE sklearn LogisticRegression per fold (liblinear), no GBM, no
    ensembles, no RFECV, no hyperparameter search, no sample weighting.
  - 4 expanding walk-forward folds of 252 trading days each (not 5).
  - Uses trainSpyModel.py's SPY_FEATURES list and prep_df() directly
    (imported, not reimplemented) — this is a position-sizing-overlay
    question, not a model-quality question, so a single reasonably-good
    classifier run walk-forward is sufficient signal.

Baseline sizing reproduced EXACTLY from predictSpy.py:
  position_size = clip(0.50*(2*prob-1), 0, 1)
  halved if regime == 'bear'
  zeroed if prob < threshold (0.51 chop, 0.53 bull/bear)
  zeroed if kill-switch: regime=='bear' and gex_sign<=0 and vix_z21>1.5
  zeroed if elevated-chop: regime=='chop' and vix_level>=22

Vol-targeted overlay: scales the RAW (pre-clip) 50%-Kelly fraction by
(target_ann_vol / realized_ann_vol_21d) BEFORE the same [0,1] clip and all
the same bear/chop/kill-switch zeroing rules are re-applied on top.

Usage:
  venv/bin/python3 python/spyVolTargetWFCV.py
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from trainSpyModel import SPY_FEATURES, prep_df

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_PLOT  = os.path.expanduser("~/discordBot/outputs/markets/spy_voltarget_equity.png")

TX_COST   = 0.0005   # one-way
ANNUAL    = 252
N_FOLDS   = 4         # scoped down from 5 to cut one full retrain
FOLD_SIZE = 252

VIX_BUCKETS = [(0, 16, "calm"), (16, 22, "normal"), (22, 999, "elevated")]

# ── live sizing constants (exact copy of predictSpy.py rules) ────────────────
KELLY_FRACTION       = 0.50
BEAR_MULT            = 0.5
CHOP_THRESH          = 0.51
BULLBEAR_THRESH      = 0.53
CHOP_VIX_GATE_THRESH = 22.0
KILL_VIX_Z21_THRESH  = 1.5


def ann_sharpe(returns):
    returns = np.asarray(returns, dtype=float)
    if len(returns) == 0 or np.nanstd(returns) == 0:
        return 0.0
    return float(np.nanmean(returns) / np.nanstd(returns) * np.sqrt(ANNUAL))


def max_drawdown(returns):
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min())


# ── 1. Pooled walk-forward OOS predictions (single logistic per fold) ───────

def run_wfcv(df_raw):
    df, avail = prep_df(df_raw, SPY_FEATURES, "next_dir_1d")
    print(f"[spyVolTargetWFCV] {len(df)} prepped rows, {len(avail)} features, "
          f"{df.date.min().date()}-{df.date.max().date()}")

    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = FOLD_SIZE * N_FOLDS
    train_start_idx = n - total_window - FOLD_SIZE

    oos_rows = []
    for i in range(N_FOLDS):
        fold_start = dates[train_start_idx + FOLD_SIZE + i * FOLD_SIZE]
        fold_end_idx = min(train_start_idx + FOLD_SIZE + (i + 1) * FOLD_SIZE - 1, n - 1)
        fold_end = dates[fold_end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < 500 or len(va) < 20:
            print(f"  fold {i+1}: skipped (tr={len(tr)}, va={len(va)})")
            continue

        model = Pipeline([("sc", StandardScaler()),
                           ("m", LogisticRegression(solver="liblinear", max_iter=500,
                                                     random_state=42))])
        model.fit(tr[avail].values, tr["next_dir_1d"].values)
        p = model.predict_proba(va[avail].values)[:, 1]

        for j in range(len(va)):
            row = va.iloc[j]
            oos_rows.append({
                "date": row["date"], "p": p[j], "y": row["next_dir_1d"],
                "next_ret_1d": row["next_ret_1d"],
                "regime": row.get("regime"),
                "vix_level": row.get("vix_level"),
                "vix_z21": row.get("vix_z21"),
                "gex_sign": row.get("gex_sign"),
                "fold": i + 1,
            })
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  tr={len(tr)} va={len(va)}  "
              f"pooled OOS so far: {len(oos_rows)}")

    oos = pd.DataFrame(oos_rows).sort_values("date").reset_index(drop=True)
    n_before = len(oos)
    oos = oos.dropna(subset=["next_ret_1d"]).reset_index(drop=True)
    if len(oos) < n_before:
        print(f"  dropped {n_before - len(oos)} OOS row(s) with unresolved next_ret_1d")
    return oos


# ── 2. Realized vol (21d, annualized), zero-lookahead ────────────────────────

def attach_realized_vol(oos, df_raw):
    spy = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy["realized_vol_21d"] = spy["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    oos = oos.merge(spy[["date", "realized_vol_21d"]], on="date", how="left")
    oos["realized_vol_21d"] = oos["realized_vol_21d"].ffill()
    return oos


# ── 3. Sizing rules ──────────────────────────────────────────────────────────

def base_kelly_fraction(p):
    return np.clip(KELLY_FRACTION * (2 * p - 1), 0.0, 1.0)


def apply_gates(oos, raw_kelly_f):
    """Apply predictSpy.py's gating (threshold / kill switch / chop-vix gate /
    bear halving) on top of a raw (pre-clip) kelly fraction array."""
    regime = oos["regime"].values
    vix_level = oos["vix_level"].values
    vix_z21 = oos["vix_z21"].values
    gex_sign = oos["gex_sign"].fillna(1.0).values
    p = oos["p"].values

    pos = np.zeros(len(oos))
    for i in range(len(oos)):
        r = regime[i]
        kill = (r == "bear") and (gex_sign[i] <= 0) and (vix_z21[i] > KILL_VIX_Z21_THRESH)
        chop_vix_gate = (r == "chop") and pd.notna(vix_level[i]) and (vix_level[i] >= CHOP_VIX_GATE_THRESH)
        live_thresh = CHOP_THRESH if r == "chop" else BULLBEAR_THRESH
        if kill or chop_vix_gate or p[i] < live_thresh:
            pos[i] = 0.0
        else:
            bear_mult = BEAR_MULT if r == "bear" else 1.0
            pos[i] = float(np.clip(raw_kelly_f[i], 0.0, 1.0)) * bear_mult
    return pos


def backtest_positions(oos, positions, tx_cost=TX_COST):
    positions = np.asarray(positions, dtype=float)
    prev = np.concatenate([[0.0], positions[:-1]])
    turnover_per_day = np.abs(positions - prev)
    daily_ret = positions * oos["next_ret_1d"].values - turnover_per_day * tx_cost
    n_trades = int(np.sum(turnover_per_day > 1e-9))
    return {
        "daily_ret": daily_ret,
        "turnover": float(turnover_per_day.sum()),
        "n_trades": n_trades,
        "sharpe": ann_sharpe(daily_ret),
        "max_dd": max_drawdown(daily_ret),
        "total_return": float(np.prod(1 + daily_ret) - 1),
        "ann_return": float(np.mean(daily_ret) * ANNUAL),
        "n_days": len(daily_ret),
    }


def bucket_sharpe(oos, daily_ret):
    out = {}
    vix = oos["vix_level"].values
    for lo, hi, label in VIX_BUCKETS:
        mask = (vix >= lo) & (vix < hi)
        out[label] = {"n": int(mask.sum()), "sharpe": ann_sharpe(daily_ret[mask])}
    return out


def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    oos = run_wfcv(df_raw)
    oos = attach_realized_vol(oos, df_raw)

    print(f"\nTotal pooled OOS rows (true walk-forward, zero lookahead): {len(oos)}")
    print(f"OOS window: {oos['date'].min().date()} -> {oos['date'].max().date()}")

    # target_ann_vol: full-sample median of realized_ann_vol_21d (as instructed)
    spy_hist = df_raw[["date", "SPY_ret"]].dropna().sort_values("date").reset_index(drop=True)
    spy_hist["realized_vol_21d"] = spy_hist["SPY_ret"].rolling(21).std() * np.sqrt(ANNUAL)
    target_ann_vol = float(spy_hist["realized_vol_21d"].median())
    print(f"\ntarget_ann_vol (full-sample median realized 21d vol, "
          f"{spy_hist['date'].min().date()}-{spy_hist['date'].max().date()}): "
          f"{target_ann_vol:.4f} ({target_ann_vol*100:.1f}% annualized)")
    print(f"For reference, OOS-period realized vol stats: "
          f"median={oos['realized_vol_21d'].median():.4f}, "
          f"mean={oos['realized_vol_21d'].mean():.4f}, "
          f"min={oos['realized_vol_21d'].min():.4f}, max={oos['realized_vol_21d'].max():.4f}")

    # ── Baseline (fixed 50% Kelly, exact live rule) ──────────────────────────
    raw_kelly_base = base_kelly_fraction(oos["p"].values)
    pos_base = apply_gates(oos, raw_kelly_base)
    bt_base = backtest_positions(oos, pos_base)

    # ── Vol-targeted overlay ─────────────────────────────────────────────────
    vol_scalar = target_ann_vol / oos["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    raw_kelly_vt = raw_kelly_base * vol_scalar   # clip happens inside apply_gates
    pos_vt = apply_gates(oos, raw_kelly_vt)
    bt_vt = backtest_positions(oos, pos_vt)

    def fmt(bt, label):
        print(f"\n--- {label} ---")
        print(f"  Sharpe (ann):     {bt['sharpe']:+.3f}")
        print(f"  Max drawdown:     {bt['max_dd']*100:+.2f}%")
        print(f"  Total return:     {bt['total_return']*100:+.2f}%")
        print(f"  Ann. return:      {bt['ann_return']*100:+.2f}%")
        print(f"  Turnover (sum|dpos|): {bt['turnover']:.2f}")
        print(f"  N trades:         {bt['n_trades']}")
        print(f"  N days:           {bt['n_days']}")

    print("\n" + "=" * 70)
    print("BASELINE vs VOL-TARGETED - pooled walk-forward OOS backtest")
    print("=" * 70)
    fmt(bt_base, "BASELINE (fixed 50% Kelly, live rule)")
    fmt(bt_vt, f"VOL-TARGETED (target_ann_vol={target_ann_vol:.3f})")

    print(f"\nDelta: Sharpe {bt_vt['sharpe']-bt_base['sharpe']:+.3f}  "
          f"maxDD {(bt_vt['max_dd']-bt_base['max_dd'])*100:+.2f}pp  "
          f"total_ret {(bt_vt['total_return']-bt_base['total_return'])*100:+.2f}pp  "
          f"turnover {bt_vt['turnover']-bt_base['turnover']:+.2f}  "
          f"n_trades {bt_vt['n_trades']-bt_base['n_trades']:+d}")

    # ── VIX-regime bucket breakdown ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Sharpe by VIX bucket (fixed, pre-decided buckets: <16 / 16-22 / >=22)")
    print("=" * 70)
    bb = bucket_sharpe(oos, bt_base["daily_ret"])
    bv = bucket_sharpe(oos, bt_vt["daily_ret"])
    print(f"{'bucket':>10} {'n':>6} {'Sharpe_base':>12} {'Sharpe_voltgt':>14} {'delta':>8}")
    for lo, hi, label in VIX_BUCKETS:
        print(f"{label:>10} {bb[label]['n']:>6} {bb[label]['sharpe']:>+12.3f} "
              f"{bv[label]['sharpe']:>+14.3f} {bv[label]['sharpe']-bb[label]['sharpe']:>+8.3f}")

    # ── Plot: navy themed equity curve ───────────────────────────────────────
    NAVY = "#02233F"
    GRID = "#274066"
    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=NAVY)
    ax.set_facecolor(NAVY)

    eq_base = np.cumprod(1 + bt_base["daily_ret"])
    eq_vt = np.cumprod(1 + bt_vt["daily_ret"])
    ax.plot(oos["date"], eq_base, color="#5DADE2", lw=1.6,
            label=f"Fixed 50% Kelly (Sharpe {bt_base['sharpe']:+.2f})")
    ax.plot(oos["date"], eq_vt, color="#F5B041", lw=1.6,
            label=f"Vol-targeted (Sharpe {bt_vt['sharpe']:+.2f})")

    ax.set_title("SPY Position Sizing: Fixed Kelly vs Vol-Targeted Overlay\n"
                  "(pooled walk-forward OOS, zero lookahead, 4-fold lean WFCV)",
                  color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative return (x)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)
    leg = ax.legend(facecolor=NAVY, edgecolor=GRID, labelcolor="white", loc="upper left")
    fig.text(0.5, 0.01,
              f"target_ann_vol={target_ann_vol:.1%} (full-sample median realized 21d vol) | "
              f"TX_COST={TX_COST} one-way | OOS n={len(oos)} days "
              f"({oos['date'].min().date()}-{oos['date'].max().date()})",
              ha="center", color="white", fontsize=8.5)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(OUT_PLOT, facecolor=NAVY, dpi=140)
    print(f"\nSaved plot: {OUT_PLOT}")


if __name__ == "__main__":
    main()
