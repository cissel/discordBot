#!/usr/bin/env python3
"""
spyMetaLabelingWFCV.py
=======================
Meta-labeling secondary classifier for the SPY primary direction model
(Lopez de Prado style): a "should I act on this signal" filter trained on
REALIZED trade outcomes, layered on top of the existing primary model,
under strict walk-forward (zero-lookahead) methodology.

Pipeline
--------
1. Pooled walk-forward OOS primary signal, exactly mirroring predictSpy.py's
   live rule: 5 expanding folds of 252 trading days each (same construction
   as chopVixSubRegimeWFCV.py), fit strictly on prior data only, predict OOS
   on the next 252-day block. Primary probability = 50/50 blend of a
   Logistic (C=0.1, recency-weighted half-life=1260) and a GBM classifier,
   both built with trainSpyModel.build_model()/compute_sample_weights() for
   parity with production. (Simplification vs. production: single GBM, not
   the 15-seed ensemble; single universal model, not regime-specific — both
   acceptable per task instructions.)

2. Primary trading rule reproduced from predictSpy.py:
     threshold   = 0.51 if regime == "chop" else 0.53
     kelly_f     = clip(0.50 * (2p - 1), 0, 1)
     position    = kelly_f * (0.5 if regime == "bear" else 1.0)  if p >= threshold else 0
   (Simplification: the bear kill-switch and chop-VIX gate from live
   predictSpy.py are NOT reproduced here — task spec only calls out
   threshold + Kelly sizing as the baseline to reproduce.)

3. Meta-label: for every day where the primary rule fires a nonzero
   position, outcome=1 if that day's realized one-day trade
   (position*next_ret_1d - TX_COST) was net profitable, else 0.
   TX_COST is charged once per flagged day (approximates the cost of
   putting on that day's position), same TX_COST=0.0005 one-way constant
   used everywhere else in this codebase.

4. Meta-classifier: small Logistic Regression trained ONLY on meta-labeled
   (flagged) days, walk-forward: fold k's meta-model trains on pooled
   flagged-day outcomes from folds < k only (expanding window, zero
   lookahead w.r.t. both the primary signal AND the meta-label itself).
   Folds 1-2 have too little meta-training data to be reliable -> meta
   filtering is only applied/backtested from fold 3 onward (folds 1-2
   meta-training data IS still accumulated and used to train fold-3's
   meta-model). Features: |p-0.5| (primary conviction), regime dummies,
   vix_level, vix_z21, gex_sign, vol_regime, days_since_regime_change.

5. Three backtest variants over the fold-3..5 pooled OOS window:
     (a) primary-only baseline      — current live rule, no meta-filter
     (b) primary + meta hard gate   — trade only if meta_prob > 0.5
     (c) primary + meta soft scaler — position *= meta_prob
   Reports Sharpe, maxDD, total/annualized return, trade count, win rate.
   Produces ONE navy-themed cumulative-equity comparison plot.

Usage:
  venv/bin/python3 python/spyMetaLabelingWFCV.py
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
from trainSpyModel import (
    SPY_FEATURES, prep_df, build_model, compute_sample_weights,
)

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
OUT_DIR   = os.path.expanduser("~/discordBot/outputs/markets")
PLOT_PATH = os.path.join(OUT_DIR, "spy_meta_labeling_equity.png")

TX_COST    = 0.0005   # 5bps one-way, matches trainSpyModel.py / chopVixSubRegimeWFCV.py
ANNUAL     = 252
HALF_LIFE  = 1260     # recency-weighting half-life, production default
FOLD_SIZE  = 252
N_FOLDS    = 5
MIN_TRAIN_ROWS = 500
META_START_FOLD = 3    # meta-filtering backtest active from this fold onward (1-indexed)

NAVY_BG   = "#02233F"
GRID_COL  = "#274066"


# ── primary trading rule (mirrors predictSpy.py; no kill-switch/chop-vix-gate) ──

def primary_threshold(regime):
    return 0.51 if regime == "chop" else 0.53


def primary_position(p, regime):
    thresh = primary_threshold(regime)
    if p < thresh:
        return 0.0
    kelly_f = float(np.clip(0.50 * (2 * p - 1), 0.0, 1.0))
    bear_mult = 0.5 if regime == "bear" else 1.0
    return kelly_f * bear_mult


# ── metrics helpers ──────────────────────────────────────────────────────────

def ann_sharpe(daily_ret):
    daily_ret = np.asarray(daily_ret)
    if len(daily_ret) == 0 or daily_ret.std() == 0:
        return 0.0
    return float(daily_ret.mean() / daily_ret.std() * np.sqrt(ANNUAL))


def max_drawdown(daily_ret):
    cum = (1 + np.asarray(daily_ret)).cumprod()
    roll_max = np.maximum.accumulate(cum)
    dd = (cum - roll_max) / roll_max
    return float(dd.min())


def compute_daily_pnl(position, next_ret):
    """position, next_ret are arrays aligned in time. Cost charged on position CHANGE
    (consistent with the aggregate-level cost convention used elsewhere in this repo)."""
    pos = np.asarray(position, dtype=float)
    ret = np.asarray(next_ret, dtype=float)
    cost = np.abs(np.diff(np.append(0.0, pos))) * TX_COST
    return pos * ret - cost


def summarize(label, position, next_ret):
    daily = compute_daily_pnl(position, next_ret)
    cum = (1 + daily).cumprod()
    total_ret = float(cum[-1] - 1) if len(cum) else 0.0
    n_days = len(daily)
    ann_ret = float(daily.mean() * ANNUAL)
    sharpe = ann_sharpe(daily)
    mdd = max_drawdown(daily)
    trade_mask = np.asarray(position) != 0
    n_trades = int(trade_mask.sum())
    win_rate = float((daily[trade_mask] > 0).mean()) if n_trades > 0 else float("nan")
    return {
        "variant": label,
        "sharpe": round(sharpe, 3),
        "max_dd": round(mdd, 4),
        "total_return": round(total_ret, 4),
        "ann_return": round(ann_ret, 4),
        "n_trades": n_trades,
        "n_days": n_days,
        "win_rate": round(win_rate, 4) if not np.isnan(win_rate) else None,
    }, daily, cum


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    df_raw = pd.read_csv(FEAT_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df, avail = prep_df(df_raw, SPY_FEATURES, "next_dir_1d")
    print(f"[spyMetaLabelingWFCV] {len(df)} rows after prep_df, "
          f"{df['date'].min().date()} - {df['date'].max().date()}, {len(avail)} primary features")

    dates = sorted(df["date"].unique())
    n = len(dates)
    total_window = FOLD_SIZE * N_FOLDS
    train_start_idx = n - total_window - FOLD_SIZE
    if train_start_idx < 0:
        train_start_idx = 0

    oos_rows = []  # one row per OOS day with primary prob + context

    print("\n--- Stage 1: pooled walk-forward primary OOS probabilities (5 folds) ---")
    for i in range(N_FOLDS):
        fold_start_idx = train_start_idx + FOLD_SIZE + i * FOLD_SIZE
        fold_end_idx = min(train_start_idx + FOLD_SIZE + (i + 1) * FOLD_SIZE - 1, n - 1)
        if fold_start_idx >= n:
            print(f"  fold {i+1}: skipped (out of range)")
            continue
        fold_start = dates[fold_start_idx]
        fold_end = dates[fold_end_idx]
        tr = df[df["date"] < fold_start].copy()
        va = df[(df["date"] >= fold_start) & (df["date"] <= fold_end)].copy()
        if len(tr) < MIN_TRAIN_ROWS or len(va) < 20:
            print(f"  fold {i+1}: skipped (tr={len(tr)}, va={len(va)})")
            continue

        sw = compute_sample_weights(tr["date"], half_life_days=HALF_LIFE)
        m_log = build_model("logistic", is_clf=True)
        m_gbm = build_model("gbm", is_clf=True)
        X_tr, y_tr = tr[avail].values, tr["next_dir_1d"].values
        X_va = va[avail].values
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
        p_blend = (p_log + p_gbm) / 2.0

        for j in range(len(va)):
            row = va.iloc[j]
            oos_rows.append({
                "date": row["date"],
                "fold": i + 1,
                "p": p_blend[j],
                "regime": row.get("regime", "chop"),
                "vix_level": row.get("vix_level", np.nan),
                "vix_z21": row.get("vix_z21", np.nan),
                "gex_sign": row.get("gex_sign", np.nan),
                "vol_regime": row.get("vol_regime", np.nan),
                "days_since_regime_change": row.get("days_since_regime_change", np.nan),
                "next_ret_1d": row.get("next_ret_1d", np.nan),
                "next_dir_1d": row.get("next_dir_1d", np.nan),
            })
        print(f"  fold {i+1}: {fold_start.date()}-{fold_end.date()}  tr={len(tr)} va={len(va)}")

    oos = pd.DataFrame(oos_rows)
    oos = oos.dropna(subset=["next_ret_1d"]).reset_index(drop=True)
    # Impute missing context features with their pooled median (small # of NaN rows)
    for c in ["gex_sign", "vix_level", "vix_z21", "vol_regime", "days_since_regime_change"]:
        if oos[c].isna().any():
            oos[c] = oos[c].fillna(oos[c].median())
    print(f"\nTotal pooled OOS rows: {len(oos)} across folds {sorted(oos['fold'].unique())}")

    # ── primary position + meta-label ────────────────────────────────────────
    oos["position_primary"] = [primary_position(p, r) for p, r in zip(oos["p"], oos["regime"])]
    oos["flagged"] = oos["position_primary"] != 0.0
    oos["trade_pnl"] = oos["position_primary"] * oos["next_ret_1d"] - TX_COST  # per-flagged-day cost
    oos["meta_label"] = (oos["trade_pnl"] > 0).astype(int)

    print(f"Flagged (nonzero-position) days: {oos['flagged'].sum()} / {len(oos)}  "
          f"(meta-label positive rate among flagged: {oos.loc[oos['flagged'],'meta_label'].mean():.3f})")

    # ── Stage 2: walk-forward meta-classifier, trained on meta-labeled days only ──
    print("\n--- Stage 2: walk-forward meta-classifier (fold k trained on flagged days, folds < k) ---")

    meta_feature_cols = ["prim_dist", "is_bull", "is_bear", "is_chop",
                         "vix_level", "vix_z21", "gex_sign", "vol_regime",
                         "days_since_regime_change"]
    oos["prim_dist"] = (oos["p"] - 0.5).abs()
    oos["is_bull"] = (oos["regime"] == "bull").astype(float)
    oos["is_bear"] = (oos["regime"] == "bear").astype(float)
    oos["is_chop"] = (oos["regime"] == "chop").astype(float)

    oos["meta_prob"] = np.nan  # filled only for fold >= META_START_FOLD
    flagged = oos[oos["flagged"]].copy()

    for k in sorted(oos["fold"].unique()):
        if k < META_START_FOLD:
            continue
        train_flagged = flagged[flagged["fold"] < k]
        test_flagged = flagged[flagged["fold"] == k]
        if len(test_flagged) == 0:
            continue
        if len(train_flagged) < 30 or train_flagged["meta_label"].nunique() < 2:
            print(f"  fold {k}: insufficient meta-training data (n={len(train_flagged)}), skipping meta-filter")
            continue
        meta_m = Pipeline([("sc", StandardScaler()),
                           ("m", LogisticRegression(C=0.5, max_iter=500, random_state=42))])
        meta_m.fit(train_flagged[meta_feature_cols].values, train_flagged["meta_label"].values)
        meta_p = meta_m.predict_proba(test_flagged[meta_feature_cols].values)[:, 1]
        oos.loc[test_flagged.index, "meta_prob"] = meta_p
        y_true_k = test_flagged["meta_label"].values
        if len(np.unique(y_true_k)) > 1:
            from sklearn.metrics import roc_auc_score
            meta_auc = roc_auc_score(y_true_k, meta_p)
        else:
            meta_auc = float("nan")
        print(f"  fold {k}: meta-trained on {len(train_flagged)} flagged days from folds < {k}, "
              f"predicted meta_prob for {len(test_flagged)} flagged days in fold {k} "
              f"(mean meta_prob={meta_p.mean():.3f}, OOS meta-AUC={meta_auc:.3f}, "
              f"base rate={y_true_k.mean():.3f})")

    # ── Stage 3: backtest 3 variants over the meta-filterable period (folds >= META_START_FOLD) ──
    bt = oos[oos["fold"] >= META_START_FOLD].copy().sort_values("date").reset_index(drop=True)
    print(f"\n--- Stage 3: backtest over folds {META_START_FOLD}-{N_FOLDS} "
          f"({bt['date'].min().date()} to {bt['date'].max().date()}, n={len(bt)} days) ---")

    # (a) primary-only baseline: current rule, no meta-filter
    pos_a = bt["position_primary"].values

    # (b) hard gate: zero out flagged days where meta_prob <= 0.5 (unflagged days untouched, already 0)
    meta_prob_filled = bt["meta_prob"].fillna(1.0).values  # non-meta days (shouldn't be flagged) pass through
    pos_b = np.where(bt["flagged"].values & (meta_prob_filled <= 0.5), 0.0, pos_a)

    # (c) soft scaler: multiply flagged-day position by meta_prob; unflagged stays 0
    scaler = np.where(bt["flagged"].values, meta_prob_filled, 1.0)
    pos_c = pos_a * scaler

    next_ret = bt["next_ret_1d"].values

    res_a, daily_a, cum_a = summarize("(a) primary-only baseline", pos_a, next_ret)
    res_b, daily_b, cum_b = summarize("(b) primary + meta hard gate", pos_b, next_ret)
    res_c, daily_c, cum_c = summarize("(c) primary + meta soft scaler", pos_c, next_ret)

    results_df = pd.DataFrame([res_a, res_b, res_c])
    print("\n" + "=" * 100)
    print(results_df.to_string(index=False))
    print("=" * 100)

    out_csv = os.path.join(OUT_DIR, "spy_meta_labeling_results.csv")
    results_df.to_csv(out_csv, index=False)
    print(f"\nResults saved: {out_csv}")

    # ── plot: cumulative equity curves, navy theme ───────────────────────────
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor(NAVY_BG)
    ax.set_facecolor(NAVY_BG)

    dates_bt = bt["date"].values
    ax.plot(dates_bt, cum_a, label=f"(a) Primary-only (Sharpe {res_a['sharpe']:+.2f})",
            color="#FF6B6B", linewidth=1.8)
    ax.plot(dates_bt, cum_b, label=f"(b) + Meta hard gate (Sharpe {res_b['sharpe']:+.2f})",
            color="#4ECDC4", linewidth=1.8)
    ax.plot(dates_bt, cum_c, label=f"(c) + Meta soft scaler (Sharpe {res_c['sharpe']:+.2f})",
            color="#FFD166", linewidth=1.8)

    ax.set_title("SPY Meta-Labeling: Cumulative Equity (pooled WFCV OOS, folds 3-5)",
                 color="white", fontsize=13)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Cumulative growth of $1", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color(GRID_COL)
    ax.grid(True, color=GRID_COL, alpha=0.6)
    leg = ax.legend(loc="upper left", facecolor=NAVY_BG, edgecolor=GRID_COL)
    for text in leg.get_texts():
        text.set_color("white")

    fig.autofmt_xdate()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, facecolor=NAVY_BG, dpi=130)
    print(f"Plot saved: {PLOT_PATH}")

    # ── caveats summary ────────────────────────────────────────────────────
    print("\n--- Caveats ---")
    print(f"- Meta-filtering backtest only covers folds {META_START_FOLD}-{N_FOLDS} "
          f"(folds 1-2 reserved as meta-training-only warm-up, per task spec).")
    print(f"- Primary model is a universal logistic+GBM 50/50 blend (not regime-specific, "
          f"not the 15-seed ensemble) — simplification vs. live predictSpy.py.")
    print(f"- Baseline primary rule does NOT include predictSpy.py's bear kill-switch or "
          f"chop-VIX gate — task spec calls out only threshold+Kelly sizing as baseline.")
    print(f"- Meta-label cost convention: one TX_COST charge per flagged day (approximates "
          f"round-trip cost of that day's trade), vs. the aggregate backtest's diff-based "
          f"position-change cost used for the actual equity curves.")


if __name__ == "__main__":
    main()
