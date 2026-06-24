#!/usr/bin/env python3
"""
buildRegimePredModel.py
=======================
Trains a 3-class classifier to predict TOMORROW's market regime (bull/bear/chop).
Converts the regime routing in predictSpy.py from reactive (today's regime label)
to predictive (what regime is tomorrow most likely to be in).

Target: tomorrow's regime = df["regime"].shift(-1)
Features: VIX dynamics, vol regime, macro trajectory, momentum — all available at close.
Model: Logistic (multinomial, C=0.5) + GBM (depth=2, regularised)

Output:
  models/markets/spy/spy_regime_pred_{date}.pkl
  Columns in pkl: model, features, classes, trained_on, val_accuracy

Usage:
  venv/bin/python3 python/buildRegimePredModel.py
"""

import os
import sys
import pickle
import datetime
import warnings
import numpy as np
import pandas as pd
from scipy import stats

from sklearn.linear_model  import LogisticRegression
from sklearn.ensemble      import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline      import Pipeline
from sklearn.metrics       import accuracy_score, classification_report

warnings.filterwarnings("ignore")

FEAT_PATH = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR = os.path.expanduser("~/discordBot/models/markets/spy")
TODAY     = datetime.date.today().isoformat()

os.makedirs(MODEL_DIR, exist_ok=True)

# Features that describe the current market environment — available at today's close.
# These are the inputs to predict TOMORROW's regime.
REGIME_PRED_FEATURES = [
    # Yesterday's regime — persistence baseline feature (regime autocorrelation = 89.5%)
    # Without this, model ignores the strongest single predictor of tomorrow's regime.
    "regime_lag1_bear",  # one-hot: was bear yesterday?
    "regime_lag1_bull",  # one-hot: was bull yesterday?
    # (chop is the omitted category)
    # Vol level and dynamics — most direct regime signal
    "vix_level",
    "vix_z21",
    "vix_z252",
    "vix_chg_1d",
    "vix_chg_5d",
    "vix_ma20_ratio",
    "vol_regime",
    "rv_21",
    "vix_rv_ratio",
    # VIX term structure
    "vix_term_slope",
    "vix_term_z21",
    "vvix",
    "vvix_z21",
    "vvix_chg_5d",
    # SPY momentum and drawdown
    "spy_ret_r5",
    "spy_ret_r21",
    "spy_ret_r63",
    "spy_drawdown_252",
    "spy_z21",
    "spy_rsi_14",
    "spy_rsi_3",
    # Macro
    "yield_curve",
    "yield_curve_z63",
    "yield_inverted",
    "fedfunds",
    "fedfunds_chg_21d",
    # Cross-asset
    "gld_spy_corr_21",
    "gld_ret_5d",
    "dxy_z21",
    "dxy_z63",
    # GEX / dealer positioning
    "gex_b",
    "gex_z21",
    "dix",
    "dix_z21",
    # Sector rotation
    "sector_rotation_r5",
    "sector_risk_off_r5",
    "xle_spy_rs_5d",
]

SPARSE_IN_PRED = [
    "vix_z252", "vvix", "vvix_z21", "vvix_chg_5d",
    "vix_term_slope", "vix_term_z21",
]


def prep_regime(df):
    """Build feature matrix and tomorrow-regime target."""
    df = df.copy()
    # Target: shift regime forward by 1 (tomorrow's regime)
    df["next_regime"] = df["regime"].shift(-1)
    df = df[df["next_regime"].notna() & df["regime"].notna()].copy()

    # Add one-hot lagged regime features (persistence baseline features)
    # Use yesterday's regime (lag 1) as explicit features so the model
    # can learn which transitions are predictable vs. random.
    df["regime_lag1_bear"] = (df["regime"] == "bear").astype(float)
    df["regime_lag1_bull"] = (df["regime"] == "bull").astype(float)
    # (chop is the omitted reference category)

    avail = [f for f in REGIME_PRED_FEATURES if f in df.columns]

    # Impute sparse features
    for col in SPARSE_IN_PRED:
        if col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

    core = [f for f in avail if f not in SPARSE_IN_PRED]
    df   = df.dropna(subset=core + ["next_regime"])
    df   = df.sort_values("date").reset_index(drop=True)
    return df, avail


def compute_weights(dates, half_life=756):
    days_ago = (dates.max() - dates).dt.days.values.astype(float)
    w = np.exp(-days_ago / half_life)
    return w / w.mean()


def main():
    print("[buildRegimePredModel] loading features...")
    if not os.path.exists(FEAT_PATH):
        print(f"  ERROR: {FEAT_PATH} not found"); sys.exit(1)

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    if "regime" not in df.columns:
        print("  ERROR: 'regime' column missing — run buildSpyFeatures.py first"); sys.exit(1)

    df_p, avail = prep_regime(df)
    print(f"  {len(df_p)} rows after prep | features: {len(avail)}")
    print(f"  Regime distribution: {dict(df_p['next_regime'].value_counts())}")

    # Train / val split — last 252 rows as val
    split = max(len(df_p) - 252, int(len(df_p) * 0.8))
    tr    = df_p.iloc[:split]
    va    = df_p.iloc[split:]
    sw    = compute_weights(tr["date"])

    X_tr = tr[avail].values
    y_tr = tr["next_regime"].values
    X_va = va[avail].values
    y_va = va["next_regime"].values

    print(f"\n  Train: {len(tr)} rows | Val: {len(va)} rows")
    print(f"  Val regime dist: {dict(pd.Series(y_va).value_counts())}")

    # ── Model 1: Logistic (multinomial) ──────────────────────────────────────
    log_m = Pipeline([
        ("sc", StandardScaler()),
        ("m", LogisticRegression(
            C=0.5, solver="lbfgs",
            max_iter=1000, random_state=42
        ))
    ])
    log_m.fit(X_tr, y_tr, **{"m__sample_weight": sw})
    log_pred = log_m.predict(X_va)
    log_acc  = accuracy_score(y_va, log_pred)
    print(f"\n  Logistic accuracy: {log_acc:.4f}")
    print(classification_report(y_va, log_pred, target_names=["bear","bull","chop"],
                                 zero_division=0))

    # ── Model 2: GBM (multiclass) ─────────────────────────────────────────────
    # Convert string labels to int for GBM
    class_map = {"bear": 0, "bull": 1, "chop": 2}
    inv_map   = {v: k for k, v in class_map.items()}
    y_tr_int  = np.array([class_map[c] for c in y_tr])
    y_va_int  = np.array([class_map[c] for c in y_va])

    gbm_m = Pipeline([
        ("sc", StandardScaler()),
        ("m", GradientBoostingClassifier(
            n_estimators=200, learning_rate=0.05,
            max_depth=2, min_samples_leaf=30,
            subsample=0.8, random_state=42
        ))
    ])
    gbm_m.fit(X_tr, y_tr_int, **{"m__sample_weight": sw})
    gbm_pred_int = gbm_m.predict(X_va)
    gbm_pred     = np.array([inv_map[i] for i in gbm_pred_int])
    gbm_acc      = accuracy_score(y_va, gbm_pred)
    print(f"  GBM accuracy: {gbm_acc:.4f}")
    print(classification_report(y_va, gbm_pred, target_names=["bear","bull","chop"],
                                 zero_division=0))

    # ── Naive baseline: predict most common training regime ────────────────────
    most_common = pd.Series(y_tr).mode()[0]
    naive_acc   = accuracy_score(y_va, [most_common] * len(y_va))

    # ── Persistence baseline: predict tomorrow = today ────────────────────────
    # regime_lag1_bear/bull encode today's regime — can reconstruct "today" from val df
    persistence_pred = []
    for i in range(len(y_va)):
        row_i = va.iloc[i]
        if row_i.get("regime_lag1_bear", 0) == 1:
            persistence_pred.append("bear")
        elif row_i.get("regime_lag1_bull", 0) == 1:
            persistence_pred.append("bull")
        else:
            persistence_pred.append("chop")
    persist_acc = accuracy_score(y_va, persistence_pred)
    print(f"  Naive baseline (always '{most_common}'): {naive_acc:.4f}")
    print(f"  Persistence baseline (same as today):    {persist_acc:.4f}  <-- real bar to beat")

    # ── Save best model ────────────────────────────────────────────────────────
    best_model  = log_m if log_acc >= gbm_acc else gbm_m
    best_type   = "logistic" if log_acc >= gbm_acc else "gbm"
    best_acc    = max(log_acc, gbm_acc)
    needs_intmap = best_type == "gbm"

    out_path = os.path.join(MODEL_DIR, f"spy_regime_pred_{TODAY}.pkl")
    with open(out_path, "wb") as f:
        pickle.dump({
            "model":        best_model,
            "model_type":   best_type,
            "features":     avail,
            "classes":      ["bear", "bull", "chop"],
            "class_map":    class_map if needs_intmap else None,
            "trained_on":   TODAY,
            "val_accuracy": round(best_acc, 4),
            "naive_acc":    round(naive_acc, 4),
            "persist_acc":  round(persist_acc, 4),
        }, f)
    print(f"\n  Saved {best_type} model -> {out_path}")
    print(f"  Val accuracy:        {best_acc:.4f}")
    print(f"  Persistence baseline:{persist_acc:.4f}  (must beat this to be useful for routing)")
    print(f"  Naive baseline:      {naive_acc:.4f}")
    lift_over_persist = (best_acc - persist_acc) * 100
    print(f"  Lift over persistence: {lift_over_persist:+.1f}pp")

    # ── Quick sanity: what does it predict for today? ─────────────────────────
    latest = df_p.iloc[-1:]
    X_today = latest[avail].values
    today_regime_actual  = latest["regime"].values[0]
    today_regime_predict = log_m.predict(X_today)[0]
    print(f"\n  Today's actual regime: {today_regime_actual}")
    print(f"  Model prediction for tomorrow: {today_regime_predict}")
    log_probs = log_m.predict_proba(X_today)[0]
    for cls, p in zip(log_m.classes_, log_probs):
        print(f"    P({cls}) = {p:.3f}")


if __name__ == "__main__":
    main()
