#!/usr/bin/env python3
"""
buildOvernightModel.py
======================
Trains a LogisticRegression model to predict tomorrow's overnight gap
DIRECTION (up=1 / down=0).

Target:
  overnight_gap_dir = 1 if next-day overnight_gap > 0 else 0
  Built from spy_intraday_features.csv by shifting overnight_gap back one row.

Features:
  Daily (from spy_features.csv):
    vix_level, vix_chg_1d, yield_curve, fedfunds,
    spy_ret_r5, spy_vol_r5, spy_rsi_14,
    gld_ret_1d, uso_ret_1d, dxy_ret_1d,
    spy_z21, vix_z21, sector_rotation_r5

  Intraday (same-day, from spy_intraday_features.csv):
    last_hour_ret, vwap_dev_am, late_reversal_flag,
    premarket_ret, premarket_vol_ratio, overnight_gap

Model:
  sklearn LogisticRegression(C=0.1, max_iter=1000) with StandardScaler.

Split:
  Time-based: val = last 252 trading days.

Outputs:
  models/markets/overnight/overnight_dir_{YYYY-MM-DD}.pkl
  models/meta/overnight_experiment_log.csv

Usage:
  venv/bin/python3 python/buildOvernightModel.py [--notes "..."]
"""

import os
import sys
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd

from sklearn.linear_model  import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline      import Pipeline
from sklearn.metrics       import (accuracy_score, roc_auc_score,
                                   brier_score_loss, balanced_accuracy_score)

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR        = os.path.expanduser("~/discordBot")
DAILY_FEAT_PATH = os.path.join(BASE_DIR, "outputs", "features", "markets",
                                "spy_features.csv")
INTRA_FEAT_PATH = os.path.join(BASE_DIR, "outputs", "features", "markets",
                                "spy_intraday_features.csv")
MODEL_DIR       = os.path.join(BASE_DIR, "models", "markets", "overnight")
LOG_PATH        = os.path.join(BASE_DIR, "models", "meta",
                                "overnight_experiment_log.csv")

TODAY = datetime.date.today().isoformat()

# ---------------------------------------------------------------------------
# Feature lists
# ---------------------------------------------------------------------------
DAILY_FEATURES = [
    "vix_level",
    "vix_chg_1d",
    "yield_curve",
    "fedfunds",
    "spy_ret_r5",
    "spy_vol_r5",
    "spy_rsi_14",
    "gld_ret_1d",
    "uso_ret_1d",
    "dxy_ret_1d",
    "spy_z21",
    "vix_z21",
    "sector_rotation_r5",
]

INTRADAY_FEATURES = [
    "last_hour_ret",
    "vwap_dev_am",
    "late_reversal_flag",
    "premarket_ret",
    "premarket_vol_ratio",
    "overnight_gap",        # today's overnight gap (not the target)
]

ALL_FEATURES = DAILY_FEATURES + INTRADAY_FEATURES

TARGET = "overnight_gap_dir"

# ---------------------------------------------------------------------------
# Experiment log schema (mirrors spy_experiment_log.csv)
# ---------------------------------------------------------------------------
LOG_COLS = [
    "model_id", "target", "model_type", "train_date",
    "train_rows", "val_rows", "val_window_days",
    "val_spearman", "val_rmse", "val_mae", "val_r2",
    "val_dir_acc", "val_auc", "val_brier",
    "top5_features", "notes",
]


def load_log() -> pd.DataFrame:
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH)
    return pd.DataFrame(columns=LOG_COLS)


def append_log(row: dict):
    log = load_log()
    log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    """
    Join daily features and intraday features on date, build the target
    column (next day's overnight_gap direction), return the merged frame.
    """
    # -- Daily features -------------------------------------------------------
    if not os.path.exists(DAILY_FEAT_PATH):
        raise FileNotFoundError(f"Daily features not found: {DAILY_FEAT_PATH}")

    daily = pd.read_csv(DAILY_FEAT_PATH, parse_dates=["date"])
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()

    # -- Intraday features ----------------------------------------------------
    if not os.path.exists(INTRA_FEAT_PATH):
        raise FileNotFoundError(
            f"Intraday features not found: {INTRA_FEAT_PATH}\n"
            "Run buildIntradayFeatures.py first."
        )

    intra = pd.read_csv(INTRA_FEAT_PATH, parse_dates=["date"])
    intra["date"] = pd.to_datetime(intra["date"]).dt.normalize()

    # Build target: next day's overnight_gap direction.
    # Sort by date, shift overnight_gap back by 1 row.
    intra = intra.sort_values("date").reset_index(drop=True)
    intra["next_overnight_gap"] = intra["overnight_gap"].shift(-1)
    intra[TARGET] = (intra["next_overnight_gap"] > 0).astype(float)
    # Last row has no target
    intra = intra[intra["next_overnight_gap"].notna()].copy()

    # -- Merge ----------------------------------------------------------------
    # BUG FIX (Aug 4 2026): spy_features.csv (daily) already carries its own copies
    # of the INTRADAY_FEATURES columns (buildSpyFeatures.py reindexes them straight
    # from spy_intraday_features.csv - see buildSpyFeatures.py's INTRADAY_COLS
    # block). Merging `daily` and `intra` without dropping that overlap made
    # pandas silently rename every colliding column to `_x`/`_y` (no `suffixes`
    # was passed), so the plain names this script looks for (`last_hour_ret`,
    # `vwap_dev_am`, etc.) never existed post-merge - all 6 intraday features
    # were skipped on every run since this script was written, confirmed via
    # weekly retrain cron warnings. Verified the two copies are effectively
    # identical (differ in <1/1367 rows, boundary/reindex noise only), so it's
    # safe to just drop daily's duplicate copies before merging and let intra's
    # copies (the authoritative source) survive under their real names.
    daily_dedup = daily.drop(columns=[c for c in INTRADAY_FEATURES if c in daily.columns])

    intra_cols = ["date"] + INTRADAY_FEATURES + [TARGET]
    # Keep only columns that exist in intra
    intra_cols = [c for c in intra_cols if c in intra.columns]
    merged = daily_dedup.merge(intra[intra_cols], on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)

    missing_after_merge = [f for f in INTRADAY_FEATURES if f not in merged.columns]
    if missing_after_merge:
        print(f"  [WARN] Intraday features still missing after merge (unexpected): {missing_after_merge}")

    return merged


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(df: pd.DataFrame, val_days: int = 252, notes: str = "") -> dict:
    """
    Time-based train/val split.  Val = last `val_days` rows.
    Returns a dict with model bundle + metrics.
    """
    df = df.copy()

    # Available features (handle partial column availability gracefully)
    avail = [f for f in ALL_FEATURES if f in df.columns]
    missing = [f for f in ALL_FEATURES if f not in df.columns]
    if missing:
        print(f"  [WARN] Features not found in merged data, skipped: {missing}")

    # Drop rows where target is NaN
    df = df.dropna(subset=[TARGET])

    # Impute feature NaNs with column median (avoids leakage, no forward fill)
    for col in avail:
        med = df[col].median()
        df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

    # Drop any remaining NaN rows in features
    df = df.dropna(subset=avail + [TARGET])

    if len(df) < 100:
        raise ValueError(
            f"Only {len(df)} rows after NaN removal. "
            "Check that intraday features have sufficient coverage."
        )

    df = df.sort_values("date").reset_index(drop=True)

    split = len(df) - val_days
    if split < 50:
        split = int(len(df) * 0.8)

    train_df = df.iloc[:split]
    val_df   = df.iloc[split:]

    X_train = train_df[avail].values
    y_train = train_df[TARGET].values
    X_val   = val_df[avail].values
    y_val   = val_df[TARGET].values

    # -- Model ----------------------------------------------------------------
    # class_weight="balanced" (Aug 4 2026 fix): SPY overnight gaps are up
    # ~75% of trading days historically (real base rate, not a data bug -
    # confirmed year-by-year 2021-2026, all years 62-83% positive). An
    # unweighted classifier on this target just learns "always predict UP"
    # and scores ~76% "accuracy" for free while having zero real skill -
    # confirmed on the pre-fix model: it predicted DOWN exactly once in 252
    # validation days, and its raw accuracy (0.758) was actually BELOW the
    # naive always-UP baseline (0.762). class_weight="balanced" forces the
    # model to actually try to separate the classes instead of exploiting
    # the imbalance. Judge real skill via balanced_accuracy vs the baseline
    # below and AUC, NOT raw accuracy - raw accuracy is misleading on this
    # target and always will be until the base rate stops being ~75/25.
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("model",  LogisticRegression(C=0.1, max_iter=1000,
                                       random_state=42, solver="lbfgs",
                                       class_weight="balanced")),
    ])
    pipe.fit(X_train, y_train)

    # -- Validation metrics ---------------------------------------------------
    y_pred_proba = pipe.predict_proba(X_val)[:, 1]
    y_pred_class = pipe.predict(X_val)

    val_dir_acc     = accuracy_score(y_val, y_pred_class)
    val_balanced_acc = balanced_accuracy_score(y_val, y_pred_class)
    # Naive baseline: always predict the majority class seen in TRAIN (point-in-time
    # correct - val's own majority isn't known in advance in live use).
    majority_class = int(round(y_train.mean()))
    baseline_preds = np.full_like(y_val, majority_class)
    always_majority_acc = accuracy_score(y_val, baseline_preds)
    n_down_preds = int((y_pred_class == 0).sum())

    try:
        val_auc = roc_auc_score(y_val, y_pred_proba)
    except ValueError:
        val_auc = np.nan

    try:
        val_brier = brier_score_loss(y_val, y_pred_proba)
    except ValueError:
        val_brier = np.nan

    # -- Feature importances (logistic coefficients after scaling) -----------
    coefs     = pipe.named_steps["model"].coef_[0]
    abs_coefs = np.abs(coefs)
    ranked    = sorted(zip(avail, abs_coefs), key=lambda x: x[1], reverse=True)
    top5_str  = "; ".join(f"{n}={v:.3f}" for n, v in ranked[:5])

    # -- Bundle ---------------------------------------------------------------
    model_id = f"overnight_logistic_{TARGET}_{TODAY}"
    bundle   = {
        "model":      pipe,
        "features":   avail,
        "trained_on": TODAY,
        "metrics": {
            "val_dir_acc":       round(val_dir_acc, 4),
            "val_balanced_acc":  round(val_balanced_acc, 4),
            "val_auc":           round(float(val_auc), 4) if not np.isnan(val_auc) else None,
            "val_brier":         round(float(val_brier), 4) if not np.isnan(val_brier) else None,
            "always_majority_acc": round(always_majority_acc, 4),
            "n_down_preds":      n_down_preds,
            "n_val_rows":        len(val_df),
            "train_rows":        len(train_df),
            "val_rows":          len(val_df),
        },
    }

    log_row = {
        "model_id":        model_id,
        "target":          TARGET,
        "model_type":      "logistic_balanced",
        "train_date":      TODAY,
        "train_rows":      len(train_df),
        "val_rows":        len(val_df),
        "val_window_days": val_days,
        "val_spearman":    "",
        "val_rmse":        "",
        "val_mae":         "",
        "val_r2":          "",
        "val_dir_acc":     round(val_dir_acc, 4),
        "val_auc":         round(float(val_auc), 4) if not np.isnan(val_auc) else "",
        "val_brier":       round(float(val_brier), 4) if not np.isnan(val_brier) else "",
        "top5_features":   top5_str,
        "notes":           (
            f"balanced_acc={val_balanced_acc:.4f}; "
            f"always_majority_baseline_acc={always_majority_acc:.4f}; "
            f"n_down_preds={n_down_preds}/{len(val_df)}; " + notes
        ),
    }

    return bundle, log_row, val_df["date"].min(), val_df["date"].max()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Train overnight gap direction model for SPY."
    )
    parser.add_argument("--notes", default="", help="Experiment notes")
    args = parser.parse_args()

    print("=" * 60)
    print("buildOvernightModel.py")
    print("=" * 60)

    # -- Load data ------------------------------------------------------------
    print("\nLoading and merging features...")
    df = load_data()
    print(f"  Merged rows (after inner join + target build): {len(df):,}")
    print(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")

    avail_feats = [f for f in ALL_FEATURES if f in df.columns]
    print(f"\nFeatures used ({len(avail_feats)}):")
    print("  Daily    :", [f for f in DAILY_FEATURES if f in df.columns])
    print("  Intraday :", [f for f in INTRADAY_FEATURES if f in df.columns])

    # Target balance
    pos_pct = df[TARGET].mean() * 100
    print(f"\nTarget balance: {pos_pct:.1f}% positive (overnight gap up)")

    # -- Train ----------------------------------------------------------------
    print("\nTraining LogisticRegression (C=0.1, StandardScaler)...")
    val_days = 252
    bundle, log_row, val_start, val_end = train(df, val_days=val_days,
                                                 notes=args.notes)

    # -- Print results --------------------------------------------------------
    m = bundle["metrics"]
    print()
    print("-" * 40)
    print(f"Train rows   : {m['train_rows']:,}")
    print(f"Val rows     : {m['val_rows']:,}  "
          f"({val_start.date() if hasattr(val_start, 'date') else val_start} "
          f"to {val_end.date() if hasattr(val_end, 'date') else val_end})")
    print(f"Val dir acc         : {m['val_dir_acc']:.4f}  <- MISLEADING on this ~75/25 imbalanced target, do not judge skill by this alone")
    print(f"Always-majority acc : {m['always_majority_acc']:.4f}  (naive baseline: always predict train's majority class)")
    print(f"Val balanced acc    : {m['val_balanced_acc']:.4f}  <- use THIS vs 0.50 to judge real skill")
    print(f"Val AUC             : {m['val_auc']}")
    print(f"Val Brier           : {m['val_brier']}")
    print(f"# DOWN predictions  : {m['n_down_preds']} / {m['n_val_rows']} val rows  <- if this is ~0, model is still degenerate")
    print("-" * 40)
    if m['val_balanced_acc'] <= 0.52 or m['n_down_preds'] == 0:
        print("\n[WARN] Model still shows no real skill after class-weight fix")
        print("       (balanced_acc near 0.50 and/or never predicts DOWN).")
        print("       Do NOT wire this into the live autopilot loop as-is.")

    # -- Save model -----------------------------------------------------------
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    model_fname = f"overnight_dir_{TODAY}.pkl"
    model_path  = os.path.join(MODEL_DIR, model_fname)

    with open(model_path, "wb") as fh:
        pickle.dump(bundle, fh)
    print(f"\nModel saved  -> {model_path}")

    # -- Append experiment log ------------------------------------------------
    append_log(log_row)
    print(f"Log updated  -> {LOG_PATH}")
    print("\nDone.")


if __name__ == "__main__":
    main()
