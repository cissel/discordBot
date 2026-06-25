#!/usr/bin/env python3
"""
predictSpy.py
=============
Loads the best SPY models and generates today's signal.

Output (JSON to stdout):
{
  "date": "2026-06-16",
  "next_day": {
    "direction": "UP" | "DOWN",
    "probability": 0.547,        # model confidence (0.5 = coin flip)
    "confidence": "LOW"|"MED"|"HIGH",
    "model": "logistic"
  },
  "next_5d": {
    "direction": "UP" | "DOWN",
    "expected_ret": 0.0082,       # predicted 5-day return
    "probability": 0.612,
    "confidence": "LOW"|"MED"|"HIGH",
    "model": "gbm"
  },
  "macro_context": {
    "vix": 18.4,
    "vix_regime": "NORMAL",       # LOW/<15, NORMAL/15-20, ELEVATED/20-30, FEAR/>30
    "yield_curve": -0.12,
    "fedfunds": 4.25,
    "fomc_in_days": 12,
    "cpi_in_days": 3,
    "nfp_in_days": 18,
    "is_event_window": false
  },
  "top_signals": ["vix_z252 bearish", "gld_spy_corr_21 risk-off", ...],
  "warnings": []
}

Usage:
  venv/bin/python3 python/predictSpy.py
  venv/bin/python3 python/predictSpy.py --date 2026-06-16   # specific date
"""

import os
import sys
import json
import pickle
import datetime
import argparse
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

FEAT_PATH  = os.path.expanduser("~/discordBot/outputs/features/markets/spy_features.csv")
MODEL_DIR  = os.path.expanduser("~/discordBot/models/markets/spy")
CACHE_DIR  = os.path.expanduser("~/discordBot/outputs/markets/cache")

# Confidence thresholds on probability distance from 0.5
CONF_MED  = 0.03   # >53% or <47%
CONF_HIGH = 0.06   # >56% or <44%

VIX_REGIMES = [(15, "LOW"), (20, "NORMAL"), (30, "ELEVATED"), (999, "FEAR")]


def load_best_model(model_type, target):
    """Load the most recently saved model of a given type/target.
    Returns (model_or_models, features) where model_or_models is either
    a single model (logistic/gbm/ridge) or a list (gbm_ensemble).
    """
    pattern = f"spy_{model_type}_{target}_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")],
        reverse=True
    )
    if not candidates:
        return None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    # GBM ensemble stores list under "models", single models under "model"
    model = bundle.get("models") or bundle.get("model")
    return model, bundle.get("features", [])


def predict_ensemble(models_list, X):
    """Average predict_proba[:,1] across a list of models."""
    return float(np.mean([m.predict_proba(X)[:, 1] for m in models_list], axis=0)[0])


def load_regime_model(regime_name):
    """Load a regime-specific logistic model. Returns (model, feats, flip_probs, drop_from_blend, platt, gbm_chop_models) or
    (None, None, False, False, None, None).
    For 'chop': prefers dedicated chop model if available, then calibrated, then base.
    For 'bear': prefers calibrated version if available.
    Dedicated chop model bundle has a 'platt' key for calibration and optional 'gbm_chop_models' for chop-blend."""
    # Dedicated chop model takes priority
    if regime_name == "chop":
        ded_pattern = "spy_logistic_chop_dedicated_"
        ded_candidates = sorted(
            [f for f in os.listdir(MODEL_DIR) if f.startswith(ded_pattern) and f.endswith(".pkl")],
            reverse=True
        )
        if ded_candidates:
            path = os.path.join(MODEL_DIR, ded_candidates[0])
            with open(path, "rb") as f:
                bundle = pickle.load(f)
            return (bundle["model"], bundle.get("features", []),
                    bundle.get("flip_probs", False),
                    bundle.get("drop_from_blend", False),
                    bundle.get("platt", None),
                    bundle.get("gbm_chop_models", None))

    if regime_name in ("bear", "chop"):
        cal_pattern = f"spy_logistic_regime_{regime_name}_calibrated_"
        cal_candidates = sorted(
            [f for f in os.listdir(MODEL_DIR) if f.startswith(cal_pattern) and f.endswith(".pkl")],
            reverse=True
        )
        if cal_candidates:
            path = os.path.join(MODEL_DIR, cal_candidates[0])
            with open(path, "rb") as f:
                bundle = pickle.load(f)
            flip   = bundle.get("flip_probs", False)
            drop   = bundle.get("drop_from_blend", False)
            return bundle["model"], bundle.get("features", []), flip, drop, None, None
    pattern = f"spy_logistic_regime_{regime_name}_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")
         and "calibrated" not in f and "dedicated" not in f],
        reverse=True
    )
    if not candidates:
        return None, None, False, False, None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle.get("features", []), False, False, None, None


def predict_tomorrow_regime(df, row_df):
    """
    Use the regime prediction model to forecast tomorrow's regime.
    Returns (predicted_regime, probabilities_dict) or (None, None) if model unavailable.
    """
    pattern = "spy_regime_pred_"
    candidates = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith(pattern) and f.endswith(".pkl")],
        reverse=True
    )
    if not candidates:
        return None, None
    path = os.path.join(MODEL_DIR, candidates[0])
    with open(path, "rb") as f:
        bundle = pickle.load(f)

    model     = bundle["model"]
    features  = bundle.get("features", [])
    class_map = bundle.get("class_map")   # None for logistic, dict for GBM

    # Build X row
    X_row = pd.DataFrame(index=row_df.index)
    for f in features:
        if f in df.columns:
            X_row[f] = row_df[f].values
        else:
            X_row[f] = 0.0
    for col in X_row.columns:
        if X_row[col].isna().any():
            med = df[col].median() if col in df.columns else 0.0
            X_row[col] = X_row[col].fillna(med if pd.notna(med) else 0.0)

    try:
        pred_raw  = model.predict(X_row.values)[0]
        # Convert int back to string if GBM (class_map provided)
        if class_map is not None:
            inv_map  = {v: k for k, v in class_map.items()}
            pred_str = inv_map.get(int(pred_raw), str(pred_raw))
        else:
            pred_str = str(pred_raw)

        # Probabilities
        probs_raw = model.predict_proba(X_row.values)[0]
        classes   = bundle.get("classes", ["bear", "bull", "chop"])
        probs     = {cls: round(float(p), 3) for cls, p in zip(classes, probs_raw)}
        return pred_str, probs
    except Exception:
        return None, None


def confidence_label(prob):
    dist = abs(prob - 0.5)
    if dist >= CONF_HIGH:
        return "HIGH"
    elif dist >= CONF_MED:
        return "MED"
    return "LOW"


def vix_regime(vix_val):
    for threshold, label in VIX_REGIMES:
        if vix_val < threshold:
            return label
    return "FEAR"


def days_to_event(date, event_dates):
    """Calendar days until next event on or after date."""
    evts = sorted(pd.to_datetime(event_dates))
    future = [e for e in evts if e.date() >= date]
    if not future:
        return 99
    return (future[0].date() - date).days


def get_top_signals(row, features):
    """Extract human-readable top signals from the feature row."""
    signals = []
    checks = [
        ("vix_z252",       lambda v: "VIX historically elevated" if v > 1.5 else ("VIX historically low" if v < -1.0 else None)),
        ("vix_z21",        lambda v: "VIX spiking short-term" if v > 2.0 else ("VIX compressing" if v < -1.5 else None)),
        ("vix9d_z21",      lambda v: "VIX9D elevated (near-term fear)" if v > 1.5 else ("VIX9D low (near-term calm)" if v < -1.5 else None)),
        ("skew_chg_1d",    lambda v: f"SKEW surged +{v:.1f} (tail hedging up)" if v > 3.0 else (f"SKEW dropped {v:.1f} (tail risk unwinding)" if v < -3.0 else None)),
        ("spy_ret_r5",     lambda v: f"SPY up {v*100:.1f}% past week" if v > 0.02 else (f"SPY down {abs(v)*100:.1f}% past week" if v < -0.02 else None)),
        ("spy_ret_r63",    lambda v: f"SPY up {v*100:.0f}% past qtr" if v > 0.08 else (f"SPY down {abs(v)*100:.0f}% past qtr" if v < -0.05 else None)),
        ("spy_drawdown_252", lambda v: f"SPY {abs(v)*100:.0f}% off 52w high" if v < -0.08 else None),
        ("gld_spy_corr_21", lambda v: "Gold/SPY correlation positive (risk-off)" if v > 0.3 else ("Gold/SPY correlation negative (risk-on)" if v < -0.2 else None)),
        ("yield_curve",    lambda v: "Yield curve inverted" if v < 0 else (f"Yield curve steep +{v:.2f}pp" if v > 0.5 else None)),
        ("sector_rotation_r5", lambda v: "Risk-on sector leadership" if v > 0.002 else ("Risk-off sector leadership" if v < -0.002 else None)),
        ("spy_rsi_14",     lambda v: "RSI overbought (>70)" if v > 70 else ("RSI oversold (<30)" if v < 30 else None)),
        ("vix_rv_ratio",   lambda v: "VIX premium high (expensive options)" if v > 1.3 else ("VIX discount (cheap options)" if v < 0.8 else None)),
    ]
    for col, fn in checks:
        if col in row.index and pd.notna(row[col]):
            sig = fn(float(row[col]))
            if sig:
                signals.append(sig)
    return signals[:5]


def predict(target_date=None):
    # Load features
    if not os.path.exists(FEAT_PATH):
        return {"error": f"Feature file not found: {FEAT_PATH}. Run buildSpyFeatures.py first."}

    df = pd.read_csv(FEAT_PATH, parse_dates=["date"])
    df = df.sort_values("date")

    if target_date:
        row_df = df[df["date"].dt.date == target_date]
        if row_df.empty:
            # Use last available row and note the date difference
            row_df = df.tail(1)
    else:
        row_df = df.tail(1)

    row      = row_df.iloc[-1]
    row_date = row["date"].date()
    warnings_list = []

    if target_date and row_date != target_date:
        warnings_list.append(f"No data for {target_date}, using most recent: {row_date}")

    # ── detect current regime ─────────────────────────────────────────────────
    # Regime is pre-computed in spy_features.csv (bull/bear/chop).
    # Used to (a) route to a regime-specific model, (b) add context to output.
    current_regime = str(row["regime"]) if "regime" in row.index and pd.notna(row.get("regime")) else None

    # ── predict tomorrow's regime (informational only) ────────────────────────
    # NOTE: The regime pred model scores 81.4% but the persistence baseline
    # (tomorrow == today) scores 89.5% — meaning the model actively degrades
    # routing accuracy. We keep it for the probability output in the embed
    # (useful to know P(bear) is rising) but route on TODAY's regime, not predicted.
    pred_regime, regime_probs = predict_tomorrow_regime(df, row_df)
    routing_regime = current_regime   # route on current, not predicted

    # ── 1-day direction model ─────────────────────────────────────────────────
    # Use regime-specific Logistic for TODAY's regime.
    # Fallback: universal Logistic if regime model unavailable.
    regime_model, regime_feats, regime_flip, regime_drop, regime_platt, regime_gbm_chop = (
        load_regime_model(routing_regime)
        if routing_regime else (None, None, False, False, None, None)
    )
    if regime_model and regime_feats and not regime_drop:
        logistic_model  = regime_model
        logistic_feats  = regime_feats
        regime_platt_live = regime_platt
        regime_gbm_chop_live = regime_gbm_chop
        if routing_regime == "chop" and regime_platt is not None:
            model_label_1d = "logistic_chop_dedicated"
        else:
            model_label_1d = f"logistic_regime_{routing_regime}"
    else:
        logistic_model, logistic_feats = load_best_model("logistic", "next_dir_1d")
        regime_platt_live = None
        regime_gbm_chop_live = None
        model_label_1d = "logistic"
        if routing_regime and not regime_model:
            warnings_list.append(f"Regime model '{routing_regime}' not found, using universal logistic")
        elif regime_drop:
            warnings_list.append(f"Regime model '{routing_regime}' dropped (noise after flip), using universal logistic")

    # ── GBM ensemble ──────────────────────────────────────────────────────────
    gbm_ens_models, gbm_ens_feats = load_best_model("gbm_ensemble", "next_dir_1d")

    def build_X(model_feats):
        """Build a 1-row feature DataFrame for inference, imputing NaNs with column medians."""
        X = pd.DataFrame(index=row_df.index)
        missing = []
        for f in model_feats:
            if f in df.columns:
                X[f] = row_df[f].values
            else:
                X[f] = 0.0
                missing.append(f)
        if missing:
            warnings_list.append(f"{len(missing)} features missing from data (imputed 0)")
        for col in X.columns:
            if X[col].isna().any():
                med = df[col].median() if col in df.columns else 0.0
                X[col] = X[col].fillna(med if pd.notna(med) else 0.0)
        return X

    # ── Logistic prob ─────────────────────────────────────────────────────────
    prob_logistic = None
    if logistic_model and logistic_feats:
        try:
            X_log = build_X(logistic_feats)
            p_raw = float(logistic_model.predict_proba(X_log.values)[0][1])
            if regime_flip:
                p_raw = 1.0 - p_raw
            # Apply Platt calibration if available (dedicated chop model)
            if regime_platt_live is not None:
                logit_p = np.log(np.clip(p_raw, 1e-6, 1-1e-6) / (1 - np.clip(p_raw, 1e-6, 1-1e-6)))
                prob_logistic = float(regime_platt_live.predict_proba(
                    np.array([[logit_p]])
                )[0][1])
            else:
                prob_logistic = p_raw
        except Exception as e:
            warnings_list.append(f"Logistic inference error: {e}")
    else:
        warnings_list.append("Logistic model not found")

    # ── GBM ensemble prob ─────────────────────────────────────────────────────
    prob_gbm_ens = None
    if gbm_ens_models and gbm_ens_feats:
        try:
            X_ens = build_X(gbm_ens_feats)
            prob_gbm_ens = predict_ensemble(gbm_ens_models, X_ens.values)
        except Exception as e:
            warnings_list.append(f"GBM ensemble inference error: {e}")
    else:
        warnings_list.append("GBM ensemble model not found")

    # ── GBM chop ensemble prob (chop regime only) ─────────────────────────────
    prob_gbm_chop = None
    if regime_gbm_chop_live and routing_regime == "chop" and logistic_feats:
        try:
            X_chop = build_X(logistic_feats)   # same feature set as dedicated logistic
            prob_gbm_chop = float(np.mean(
                [m.predict_proba(X_chop.values)[:, 1] for m in regime_gbm_chop_live],
                axis=0
            )[0])
        except Exception as e:
            warnings_list.append(f"GBM chop inference error: {e}")

    # ── Blend: 50/50 logistic + GBM ──────────────────────────────────────────
    # In chop: prefer dedicated chop GBM over universal GBM ensemble if available.
    # Matches the strategy tested in WFCV (Sharpe 1.855 universal; chop-blend ~1.939).
    # Falls back to whichever model is available.
    if routing_regime == "chop" and prob_logistic is not None and prob_gbm_chop is not None:
        prob_blend  = (prob_logistic + prob_gbm_chop) / 2.0
        blend_label = "blend_chop_log_gbm"
    elif prob_logistic is not None and prob_gbm_ens is not None:
        prob_blend    = (prob_logistic + prob_gbm_ens) / 2.0
        blend_label   = "blend_50_50"
    elif prob_logistic is not None:
        prob_blend    = prob_logistic
        blend_label   = model_label_1d + "_only"
        warnings_list.append("GBM ensemble unavailable, using logistic only")
    elif prob_gbm_ens is not None:
        prob_blend    = prob_gbm_ens
        blend_label   = "gbm_ensemble_only"
        warnings_list.append("Logistic unavailable, using GBM ensemble only")
    else:
        prob_blend    = None
        blend_label   = "none"

    next_day_result = None
    if prob_blend is not None:
        direction = "UP" if prob_blend >= 0.5 else "DOWN"
        next_day_result = {
            "direction":    direction,
            "probability":  round(prob_blend, 4),
            "prob_logistic": round(prob_logistic, 4) if prob_logistic is not None else None,
            "prob_gbm_ens":  round(prob_gbm_ens,  4) if prob_gbm_ens  is not None else None,
            "prob_gbm_chop": round(prob_gbm_chop, 4) if prob_gbm_chop is not None else None,
            "confidence":   confidence_label(prob_blend),
            "model":        blend_label,
            "regime":       current_regime,
            "regime_pred":  routing_regime,
        }

    # ── 5-day return model (GBM) ──────────────────────────────────────────────
    gbm_model, gbm_feats = load_best_model("gbm", "next_ret_5d")
    next_5d_result = None
    if gbm_model and gbm_feats:
        avail5 = [f for f in gbm_feats if f in df.columns]
        missing5 = [f for f in gbm_feats if f not in df.columns]
        if missing5:
            warnings_list.append(f"5d GBM: {len(missing5)} features missing (will impute with 0)")
        X_row5 = pd.DataFrame(index=row_df.index)
        for f in gbm_feats:
            if f in df.columns:
                X_row5[f] = row_df[f].values
            else:
                X_row5[f] = 0.0
        for col in X_row5.columns:
            if X_row5[col].isna().any():
                med = df[col].median() if col in df.columns else 0.0
                X_row5[col] = X_row5[col].fillna(med if pd.notna(med) else 0.0)
        try:
            pred_ret  = float(gbm_model.predict(X_row5.values)[0])
            direction5 = "UP" if pred_ret >= 0 else "DOWN"
            # Convert return to rough probability using historical distribution
            hist_std  = df["next_ret_5d"].std()
            z_score   = pred_ret / hist_std if hist_std > 0 else 0
            from scipy.stats import norm
            prob_up5  = float(norm.cdf(z_score))
            next_5d_result = {
                "direction":    direction5,
                "expected_ret": round(pred_ret, 5),
                "expected_ret_pct": round(pred_ret * 100, 2),
                "probability":  round(prob_up5, 4),
                "confidence":   confidence_label(prob_up5),
                "model":        "gbm",
            }
        except Exception as e:
            warnings_list.append(f"5d GBM inference error: {e}")
    else:
        warnings_list.append("5d GBM model not found")

    # ── macro context ─────────────────────────────────────────────────────────
    vix_val   = float(row["vix_level"])  if "vix_level"   in row.index and pd.notna(row["vix_level"])   else None
    yc_val    = float(row["yield_curve"]) if "yield_curve" in row.index and pd.notna(row["yield_curve"]) else None
    ff_val    = float(row["fedfunds"])    if "fedfunds"    in row.index and pd.notna(row["fedfunds"])    else None

    # Load event calendars for days-to
    try:
        import importlib.util
        cal_path = os.path.expanduser("~/discordBot/python/macro_event_calendars.py")
        spec = importlib.util.spec_from_file_location("macro_event_calendars", cal_path)
        cal  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cal)
        fomc_in = days_to_event(row_date, cal.FOMC_DATES)
        cpi_in  = days_to_event(row_date, cal.CPI_DATES)
        nfp_in  = days_to_event(row_date, cal.NFP_DATES)
        is_event_window = min(fomc_in, cpi_in, nfp_in) <= 1
    except Exception:
        fomc_in = cpi_in = nfp_in = None
        is_event_window = False

    macro_context = {
        "vix":             round(vix_val, 2) if vix_val is not None else None,
        "vix_regime":      vix_regime(vix_val) if vix_val is not None else None,
        "yield_curve":     round(yc_val, 3) if yc_val is not None else None,
        "fedfunds":        round(ff_val, 2) if ff_val is not None else None,
        "fomc_in_days":    fomc_in,
        "cpi_in_days":     cpi_in,
        "nfp_in_days":     nfp_in,
        "is_event_window": is_event_window,
    }

    top_signals = get_top_signals(row, logistic_feats or gbm_ens_feats or [])

    # ── position sizing (live rule — Frac-Kelly 50%, chop-lower t=0.51, kill switch) ──
    # Kelly(50%): pos = clip(0.50 * (2p - 1), 0, 1), halved in bear regime.
    # Kill switch: regime=bear AND gex_sign<=0 AND vix_z21>1.5 -> flat.
    # Chop-lower (Run 35): threshold=0.51 in chop regime, 0.53 in bull/bear.
    # Signal: 50/50 blend of logistic + GBM ensemble (matches WFCV Sharpe 1.855).
    position_size   = 0.0
    sizing_reason   = "no signal"
    if next_day_result and prob_blend is not None:
        gex_sign_live = float(row["gex_sign"]) if "gex_sign" in row.index and pd.notna(row.get("gex_sign")) else 1.0
        vix_z21_live  = float(row["vix_z21"])  if "vix_z21"  in row.index and pd.notna(row.get("vix_z21"))  else 0.0
        kill = (current_regime == "bear") and (gex_sign_live <= 0) and (vix_z21_live > 1.5)
        # Regime-conditional threshold: lower bar in chop, standard in bull/bear
        live_thresh = 0.51 if current_regime == "chop" else 0.53
        if kill:
            position_size = 0.0
            sizing_reason = f"kill switch (bear + GEX negative + VIX z21={vix_z21_live:.2f})"
        elif prob_blend < live_thresh:
            position_size = 0.0
            sizing_reason = f"no signal (blend={prob_blend:.3f} < {live_thresh:.2f}, regime={current_regime})"
        else:
            kelly_f = float(np.clip(0.50 * (2 * prob_blend - 1), 0.0, 1.0))
            bear_mult = 0.5 if current_regime == "bear" else 1.0
            position_size = round(kelly_f * bear_mult, 4)
            sizing_reason = (
                f"Kelly(50%) blend size={position_size:.3f} "
                f"(blend={prob_blend:.3f} log={prob_logistic:.3f} "
                + (f"gbm_chop={prob_gbm_chop:.3f}" if prob_gbm_chop is not None else f"gbm={prob_gbm_ens:.3f}" if prob_gbm_ens is not None else "gbm=n/a")
                + f", regime={current_regime}"
                + (", half-bear" if current_regime == "bear" else "")
                + (f", chop-lower t={live_thresh}" if current_regime == "chop" else "") + ")"
            )

    result = {
        "date":          str(row_date),
        "next_day":      next_day_result,
        "next_5d":       next_5d_result,
        "macro_context": macro_context,
        "top_signals":   top_signals,
        "regime_probs":  regime_probs,   # P(bear/bull/chop) for tomorrow
        "position_size": position_size,
        "sizing_reason": sizing_reason,
        "warnings":      warnings_list,
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    target = datetime.date.fromisoformat(args.date) if args.date else None
    result = predict(target)
    print(json.dumps(result, indent=2))
