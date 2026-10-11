#!/usr/bin/env python3
"""
spyModelWatchdog.py
====================
PROTOTYPE / ANALYSIS DELIVERABLE - not wired into cron, does not touch
production models or trade logs (read-only consumer of spy_trade_log.csv).

Live model-health watchdog for James's production SPY autopilot
(tradeSpy.py / predictSpy.py). Monitors trailing-N-trade hit-rate and
Sharpe computed from the live paper-trading log against the model's
walk-forward-validated (WFCV) expectations, and flags statistically
meaningful degradation using an SPC-style "N-of-M consecutive breaches"
rule (to avoid single-bad-day false alarms).

Conventions reused from spyPerformance.py (same repo):
  - trade log dedup by date, keep last row per date
  - hit := sign(realized next-day SPY return) matches signal_direction
  - strat_ret := position_size_kelly * realized next-day SPY return
  - annualized Sharpe := mean(strat_ret)/std(strat_ret) * sqrt(252)

Key design choice per task spec: the "expected" band (dir_acc_expected,
dir_acc_tolerance_band, sharpe_expected) is fully CONFIGURABLE via CLI
flags. The repo's historical WFCV Sharpe (~1.855) is the near-best-case
backtest number and is deliberately NOT hardcoded as the pass/fail bar -
it's surfaced as a labeled reference only. A parallel effort is computing
a deflated/haircut Sharpe for a more honest comparison; once available,
pass it via --sharpe-expected.

Usage (dry run, prints JSON + human-readable status):
  venv/bin/python3 python/spyModelWatchdog.py
  venv/bin/python3 python/spyModelWatchdog.py --windows 20,40 --consecutive 3
  venv/bin/python3 python/spyModelWatchdog.py --dir-acc-expected 0.55 \\
      --dir-acc-tolerance 0.03 --sharpe-floor 0.0 --min-trades 40
  venv/bin/python3 python/spyModelWatchdog.py --power-calc   # just print the
      statistical-power analysis (how many trades needed to detect drift)

Cron-ability (NOT wired up - recommendation only, see write-up):
  A weekly cron entry like:
    0 18 * * 1 venv/bin/python3 python/spyModelWatchdog.py --quiet || \\
        <alert mechanism, e.g. post to Discord ops channel>
  would be a reasonable cadence once the trade log has enough history
  (see --power-calc output / the "EARLIEST RELIABLE FIRING POINT" section).
"""

import os
import sys
import csv
import json
import math
import random
import argparse
import datetime

BASE = os.path.expanduser("~/discordBot")
TRADE_LOG = os.path.join(BASE, "outputs/markets/spy_trade_log.csv")
EXPERIMENT_LOG = os.path.join(BASE, "models/meta/spy_experiment_log.csv")

# ── Defaults (all overridable via CLI) ───────────────────────────────────────
DEFAULT_DIR_ACC_EXPECTED = 0.55     # mid of repo's typical WFCV dir_acc 0.55-0.59
DEFAULT_DIR_ACC_TOLERANCE = 0.03    # ~= mean wfcv_dir_acc_std (~0.027) in spy_experiment_log.csv
DEFAULT_SHARPE_EXPECTED = 1.855     # repo's quoted WFCV Sharpe - OPTIMISTIC UPPER BOUND, not a pass bar
DEFAULT_SHARPE_FLOOR = 0.0          # the actual "has any edge at all" bar used for the live breach test
DEFAULT_WINDOWS = [20, 40]          # trailing-N-trade windows to monitor in parallel
DEFAULT_CONSECUTIVE = 3             # require this many consecutive breached checks before firing
DEFAULT_MIN_TRADES = 40             # floor on total resolved trades before the watchdog will fire at all
DEFAULT_CONF_LEVEL = 0.90           # two-sided CI confidence level for both binomial + bootstrap tests
DEFAULT_BOOTSTRAP_ITERS = 2000
DEFAULT_SEED = 1337


# ── Trade-log loading (mirrors spyPerformance.py conventions) ───────────────

def read_trade_log(path=TRADE_LOG):
    """Read trade log rows, dedup by date (keep LAST row per date), sorted by date."""
    if not os.path.exists(path):
        return []
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            d = (r.get("date") or "").strip()
            if d:
                rows[d] = r
    return [rows[k] for k in sorted(rows)]


def build_daily_records(rows):
    """
    Derive one effective daily (signal, position, realized-return, hit, strat_ret)
    record per trading day present in the log.

    Next-day realized SPY return is approximated from the log's OWN spy_price
    column, lagged by one row (consecutive LOGGED days, not calendar days -
    this tolerates the gaps/downtime in the log per the task note). This makes
    the watchdog fully self-contained (no dependency on the separate features
    file), at the cost of being an approximation when there are multi-day gaps
    between logged rows (the "return" then spans the gap, not exactly 1 trading
    day). Gaps are reported separately so this caveat is visible.
    """
    daily = []
    n = len(rows)
    for i, r in enumerate(rows):
        d = r["date"]
        direction = (r.get("signal_direction") or "").upper()
        try:
            kelly = float(r.get("position_size_kelly") or 0.0)
        except ValueError:
            kelly = 0.0
        try:
            price_t = float(r.get("spy_price") or "nan")
        except ValueError:
            price_t = float("nan")

        realized = None
        next_date = None
        gap_days = None
        if i + 1 < n:
            nxt = rows[i + 1]
            try:
                price_t1 = float(nxt.get("spy_price") or "nan")
            except ValueError:
                price_t1 = float("nan")
            if price_t == price_t or price_t1 == price_t1:  # not NaN
                if price_t and price_t == price_t and price_t1 == price_t1:
                    realized = price_t1 / price_t - 1.0
                    next_date = nxt["date"]
                    gap_days = (datetime.date.fromisoformat(nxt["date"])
                                - datetime.date.fromisoformat(d)).days

        if realized is None:
            hit, strat_ret = None, None
        else:
            if direction == "UP":
                hit = 1 if realized > 0 else 0
            elif direction == "DOWN":
                hit = 1 if realized < 0 else 0
            else:
                hit = None
            strat_ret = kelly * realized

        daily.append({
            "date": d, "direction": direction, "kelly": kelly,
            "spy_price": price_t, "next_date": next_date,
            "realized_ret": realized, "strat_ret": strat_ret, "hit": hit,
            "gap_days": gap_days,
        })
    return daily


def data_quality_report(rows, daily):
    dates = [r["date"] for r in rows]
    gaps = []
    prev = None
    for ds in sorted(set(dates)):
        dt = datetime.date.fromisoformat(ds)
        if prev is not None:
            delta = (dt - prev).days
            if delta > 4:  # longer than an ordinary long weekend
                gaps.append({"from": prev.isoformat(), "to": ds, "days": delta})
        prev = dt
    multi_day_lagged_returns = sum(
        1 for d in daily if d["gap_days"] is not None and d["gap_days"] > 3
    )
    return {
        "raw_rows": len(rows) if isinstance(rows, list) else None,
        "unique_dates": len(set(dates)),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "downtime_gaps_gt_4d": gaps,
        "return_estimates_spanning_gt3d_gap": multi_day_lagged_returns,
    }


# ── Statistics ───────────────────────────────────────────────────────────────

def z_for_conf(conf_level):
    """Two-sided z critical value for common confidence levels (no scipy dependency)."""
    table = {0.80: 1.2816, 0.85: 1.4395, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}
    if conf_level in table:
        return table[conf_level]
    # fallback: linear nearest-neighbor interpolation across the table
    keys = sorted(table)
    if conf_level <= keys[0]:
        return table[keys[0]]
    if conf_level >= keys[-1]:
        return table[keys[-1]]
    for a, b in zip(keys, keys[1:]):
        if a <= conf_level <= b:
            fa, fb = table[a], table[b]
            frac = (conf_level - a) / (b - a)
            return fa + frac * (fb - fa)
    return 1.6449


def wilson_ci(successes, n, z):
    """Wilson score interval for a binomial proportion - robust at small n / extreme p."""
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    lo = (center - half) / denom
    hi = (center + half) / denom
    return (max(0.0, lo), min(1.0, hi))


def sharpe(returns):
    n = len(returns)
    if n < 2:
        return None
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (mu / sd) * math.sqrt(252)


def bootstrap_sharpe_ci(returns, conf_level, iters, rng):
    """Percentile bootstrap CI for annualized Sharpe of a short daily-return series."""
    n = len(returns)
    if n < 5:
        return (None, None, None)
    point = sharpe(returns)
    alpha = 1 - conf_level
    samples = []
    for _ in range(iters):
        resample = [returns[rng.randrange(n)] for _ in range(n)]
        s = sharpe(resample)
        if s is not None:
            samples.append(s)
    if not samples:
        return (point, None, None)
    samples.sort()
    lo_idx = max(0, int((alpha / 2) * len(samples)))
    hi_idx = min(len(samples) - 1, int((1 - alpha / 2) * len(samples)) - 1)
    return (point, samples[lo_idx], samples[hi_idx])


# ── Power calculation: trades needed to reliably detect drift ───────────────

def required_n_one_sample_proportion(p0, p1, alpha=0.05, power=0.80):
    """
    One-sample one-sided binomial-proportion test power calculation (normal
    approximation): trades needed to detect a true drop from p0 (expected
    hit rate) to p1 (degraded hit rate) at the given alpha/power.
      n = [ z_alpha*sqrt(p0(1-p0)) + z_beta*sqrt(p1(1-p1)) ]^2 / (p0-p1)^2
    """
    z_alpha = z_for_one_sided(alpha)
    z_beta = z_for_one_sided(1 - power)  # z such that P(Z > z_beta) = power... see helper
    num = z_alpha * math.sqrt(p0 * (1 - p0)) + z_beta * math.sqrt(p1 * (1 - p1))
    n = (num / (p0 - p1)) ** 2
    return math.ceil(n)


def z_for_one_sided(alpha):
    """One-sided z critical value via a small lookup + interpolation (no scipy)."""
    table = {0.20: 0.8416, 0.10: 1.2816, 0.05: 1.6449, 0.025: 1.9600,
             0.01: 2.3263, 0.005: 2.5758, 0.001: 3.0902}
    if alpha in table:
        return table[alpha]
    keys = sorted(table, reverse=True)
    if alpha >= keys[0]:
        return table[keys[0]]
    if alpha <= keys[-1]:
        return table[keys[-1]]
    for a, b in zip(keys, keys[1:]):
        if b <= alpha <= a:
            fa, fb = table[a], table[b]
            frac = (a - alpha) / (a - b)
            return fa + frac * (fb - fa)
    return 1.6449


def power_calc_report(p0=0.55):
    scenarios = []
    for p1, label in [(0.50, "drop to 50% (coin-flip)"),
                       (0.52, "drop to 52% (mild degradation)"),
                       (0.48, "drop to 48% (adverse/negative edge)")]:
        for alpha, power in [(0.05, 0.80), (0.05, 0.90), (0.01, 0.80)]:
            n = required_n_one_sample_proportion(p0, p1, alpha=alpha, power=power)
            scenarios.append({
                "expected_hit_rate": p0, "degraded_hit_rate": p1, "scenario": label,
                "alpha_one_sided": alpha, "power": power, "trades_needed": n,
            })
    return scenarios


# ── Core watchdog logic ─────────────────────────────────────────────────────

def rolling_checks(daily, window, dir_acc_expected, dir_acc_tolerance,
                    sharpe_floor, conf_level, bootstrap_iters, rng):
    """
    Walk forward over the resolved daily records, computing a trailing-`window`
    hit-rate + Sharpe check at every point where a full window is available.
    Returns the list of per-check dicts (one SPC "sample" per check), in order.
    """
    resolved = [d for d in daily if d["hit"] is not None and d["strat_ret"] is not None]
    z = z_for_conf(conf_level)
    checks = []
    for end in range(window, len(resolved) + 1):
        w = resolved[end - window:end]
        hits = [d["hit"] for d in w]
        rets = [d["strat_ret"] for d in w]
        n_hits = sum(hits)
        obs_hit = n_hits / window
        hit_lo, hit_hi = wilson_ci(n_hits, window, z)
        lower_acceptable = dir_acc_expected - dir_acc_tolerance
        breach_hit = hit_hi < lower_acceptable

        point_s, s_lo, s_hi = bootstrap_sharpe_ci(rets, conf_level, bootstrap_iters, rng)
        breach_sharpe = (s_hi is not None) and (s_hi < sharpe_floor)

        checks.append({
            "as_of_date": w[-1]["date"],
            "window": window,
            "observed_hit_rate": round(obs_hit, 4),
            "hit_rate_ci": [round(hit_lo, 4), round(hit_hi, 4)],
            "expected_hit_floor": round(lower_acceptable, 4),
            "breach_hit_rate": breach_hit,
            "observed_sharpe": None if point_s is None else round(point_s, 3),
            "sharpe_ci": [None if s_lo is None else round(s_lo, 3),
                          None if s_hi is None else round(s_hi, 3)],
            "sharpe_floor": sharpe_floor,
            "breach_sharpe": breach_sharpe,
            "breach": bool(breach_hit or breach_sharpe),
        })
    return checks


def evaluate_window(daily, window, args, rng):
    resolved_n = sum(1 for d in daily if d["hit"] is not None)
    if resolved_n < window:
        return {
            "window": window, "status": "INSUFFICIENT_DATA",
            "resolved_trades": resolved_n, "needed_for_one_check": window,
            "checks_available": 0, "consecutive_required": args.consecutive,
            "detail": f"need >= {window} resolved trades for a single trailing-{window} "
                      f"check; have {resolved_n}.",
        }
    checks = rolling_checks(daily, window, args.dir_acc_expected, args.dir_acc_tolerance,
                             args.sharpe_floor, args.conf_level, args.bootstrap_iters, rng)
    if len(checks) < args.consecutive:
        return {
            "window": window, "status": "INSUFFICIENT_DATA",
            "resolved_trades": resolved_n, "checks_available": len(checks),
            "consecutive_required": args.consecutive,
            "detail": f"only {len(checks)} trailing-{window} check(s) computable; "
                      f"need {args.consecutive} consecutive breaches to fire, "
                      f"so need {args.consecutive} checks available "
                      f"(>= {window + args.consecutive - 1} resolved trades).",
            "latest_check": checks[-1] if checks else None,
        }
    tail = checks[-args.consecutive:]
    fired = all(c["breach"] for c in tail)
    status = "WARNING" if fired else "OK"
    return {
        "window": window, "status": status,
        "resolved_trades": resolved_n, "checks_available": len(checks),
        "consecutive_required": args.consecutive,
        "consecutive_breaches_in_tail": sum(1 for c in tail if c["breach"]),
        "latest_check": checks[-1],
        "tail_checks": tail,
    }


def run_watchdog(args):
    rng = random.Random(args.seed)
    rows = read_trade_log(args.trade_log)
    daily = build_daily_records(rows)
    dq = data_quality_report(rows, daily)

    resolved = [d for d in daily if d["hit"] is not None]
    overall_hits = [d["hit"] for d in resolved]
    overall_rets = [d["strat_ret"] for d in resolved]
    overall_hit_rate = (sum(overall_hits) / len(overall_hits)) if overall_hits else None
    overall_sharpe = sharpe(overall_rets) if len(overall_rets) >= 2 else None

    window_results = [evaluate_window(daily, w, args, rng) for w in args.windows]

    gate_ok = len(resolved) >= args.min_trades
    statuses = [w["status"] for w in window_results]
    if any(s == "WARNING" for s in statuses) and gate_ok:
        overall_status = "WARNING"
    elif all(s == "INSUFFICIENT_DATA" for s in statuses) or not gate_ok:
        overall_status = "INSUFFICIENT_DATA"
    else:
        overall_status = "OK"

    power = power_calc_report(p0=args.dir_acc_expected)
    min_power_n = min(s["trades_needed"] for s in power
                       if s["degraded_hit_rate"] == 0.50 and s["power"] == 0.80
                       and s["alpha_one_sided"] == 0.05)

    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "config": {
            "windows": args.windows, "consecutive_required": args.consecutive,
            "min_trades_before_firing": args.min_trades,
            "dir_acc_expected": args.dir_acc_expected,
            "dir_acc_tolerance_band": args.dir_acc_tolerance,
            "sharpe_expected_optimistic_upper_bound": args.sharpe_expected,
            "sharpe_floor_breach_threshold": args.sharpe_floor,
            "conf_level": args.conf_level,
            "bootstrap_iters": args.bootstrap_iters,
        },
        "data_quality": dq,
        "live_performance_so_far": {
            "resolved_trades": len(resolved),
            "overall_hit_rate": round(overall_hit_rate, 4) if overall_hit_rate is not None else None,
            "overall_sharpe": round(overall_sharpe, 3) if overall_sharpe is not None else None,
            "caveat": ("Sharpe computed from ~"
                       f"{len(resolved)} daily observations with small average "
                       "position sizing (Kelly-scaled) is extremely high-variance "
                       "and not a reliable estimate on its own - see power_calculation."),
        },
        "min_trades_gate_met": gate_ok,
        "window_results": window_results,
        "overall_status": overall_status,
        "power_calculation": {
            "note": ("Trades needed to reliably (one-sided binomial power calc) "
                     "distinguish a real hit-rate drop from sampling noise, given "
                     f"expected hit rate p0={args.dir_acc_expected}."),
            "scenarios": power,
            "headline_trades_needed_55_to_50pct_alpha05_power80": min_power_n,
        },
    }
    return report


def print_human_summary(report):
    print("=" * 78)
    print("SPY MODEL-HEALTH WATCHDOG")
    print("=" * 78)
    dq = report["data_quality"]
    print(f"Trade log: {dq['unique_dates']} unique dates, "
          f"{dq['first_date']} -> {dq['last_date']}")
    if dq["downtime_gaps_gt_4d"]:
        print(f"  Downtime gaps (>4d) detected: {dq['downtime_gaps_gt_4d']}")
    if dq["return_estimates_spanning_gt3d_gap"]:
        print(f"  WARNING: {dq['return_estimates_spanning_gt3d_gap']} lagged-return "
              f"estimate(s) span a >3-day gap between logged rows (approximation "
              f"degrades across downtime).")

    lp = report["live_performance_so_far"]
    print(f"\nLive performance so far ({lp['resolved_trades']} resolved trades):")
    print(f"  Hit rate : {lp['overall_hit_rate']}")
    print(f"  Sharpe   : {lp['overall_sharpe']}  <-- {lp['caveat']}")

    print(f"\nConfig: dir_acc_expected={report['config']['dir_acc_expected']} "
          f"+/- {report['config']['dir_acc_tolerance_band']}, "
          f"sharpe_floor={report['config']['sharpe_floor_breach_threshold']}, "
          f"windows={report['config']['windows']}, "
          f"consecutive_required={report['config']['consecutive_required']}, "
          f"min_trades_before_firing={report['config']['min_trades_before_firing']}")

    print(f"\nMin-trades gate met: {report['min_trades_gate_met']}")
    for wr in report["window_results"]:
        print(f"\n--- trailing-{wr['window']}-trade window: {wr['status']} ---")
        if wr["status"] == "INSUFFICIENT_DATA":
            print(f"  {wr['detail']}")
            if wr.get("latest_check"):
                lc = wr["latest_check"]
                print(f"  (most recent partial check, as_of={lc['as_of_date']}: "
                      f"hit_rate={lc['observed_hit_rate']} CI={lc['hit_rate_ci']}, "
                      f"sharpe={lc['observed_sharpe']} CI={lc['sharpe_ci']}, "
                      f"breach={lc['breach']})")
        else:
            lc = wr["latest_check"]
            print(f"  resolved_trades={wr['resolved_trades']}, "
                  f"checks_available={wr['checks_available']}, "
                  f"breaches_in_last_{wr['consecutive_required']}_checks="
                  f"{wr['consecutive_breaches_in_tail']}")
            print(f"  latest check (as_of={lc['as_of_date']}): "
                  f"hit_rate={lc['observed_hit_rate']} (CI {lc['hit_rate_ci']}, "
                  f"floor={lc['expected_hit_floor']}), "
                  f"sharpe={lc['observed_sharpe']} (CI {lc['sharpe_ci']}, "
                  f"floor={lc['sharpe_floor']})")

    print(f"\n{'='*78}\nOVERALL STATUS: {report['overall_status']}\n{'='*78}")

    pc = report["power_calculation"]
    print("\nStatistical power - trades needed to reliably detect degradation:")
    print(f"  {pc['note']}")
    for s in pc["scenarios"]:
        print(f"  p0={s['expected_hit_rate']} -> p1={s['degraded_hit_rate']} "
              f"({s['scenario']}), alpha(1-sided)={s['alpha_one_sided']}, "
              f"power={s['power']}: n >= {s['trades_needed']} trades")
    print(f"\n  HEADLINE: at the current live trade-log pace, you need roughly "
          f"{pc['headline_trades_needed_55_to_50pct_alpha05_power80']} resolved "
          f"trades (~{pc['headline_trades_needed_55_to_50pct_alpha05_power80']} "
          f"trading days, since this is a daily-signal model) before this watchdog "
          f"has real power to tell 'model degraded from 55% to 50% hit rate' apart "
          f"from ordinary sampling noise, at alpha=0.05 / power=0.80.")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trade-log", default=TRADE_LOG, help="Path to spy_trade_log.csv (read-only)")
    p.add_argument("--windows", default=",".join(str(w) for w in DEFAULT_WINDOWS),
                    help="Comma-separated trailing-N-trade window sizes, e.g. 20,40")
    p.add_argument("--consecutive", type=int, default=DEFAULT_CONSECUTIVE,
                    help="Consecutive breached checks required before firing WARNING")
    p.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES,
                    help="Minimum total resolved trades before the watchdog will fire at all")
    p.add_argument("--dir-acc-expected", type=float, default=DEFAULT_DIR_ACC_EXPECTED,
                    help="Expected directional hit rate (from WFCV); CONFIGURABLE, "
                         "not hardcoded to any single run")
    p.add_argument("--dir-acc-tolerance", type=float, default=DEFAULT_DIR_ACC_TOLERANCE,
                    help="Tolerance band width around dir_acc_expected (normal sampling noise)")
    p.add_argument("--sharpe-expected", type=float, default=DEFAULT_SHARPE_EXPECTED,
                    help="Reference WFCV Sharpe (OPTIMISTIC upper bound; informational only, "
                         "swap in a deflated/haircut estimate when available)")
    p.add_argument("--sharpe-floor", type=float, default=DEFAULT_SHARPE_FLOOR,
                    help="Breach threshold for bootstrap Sharpe CI upper bound (default 0.0 "
                         "= 'cannot rule out zero/negative edge')")
    p.add_argument("--conf-level", type=float, default=DEFAULT_CONF_LEVEL,
                    help="Two-sided confidence level for both CIs (e.g. 0.90, 0.95)")
    p.add_argument("--bootstrap-iters", type=int, default=DEFAULT_BOOTSTRAP_ITERS)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--json", action="store_true", help="Print raw JSON report only")
    p.add_argument("--quiet", action="store_true",
                    help="Suppress human summary; exit code communicates status "
                         "(0=OK/INSUFFICIENT_DATA, 1=WARNING) - for cron use")
    p.add_argument("--power-calc", action="store_true",
                    help="Print only the statistical power analysis and exit")
    args = p.parse_args(argv)
    args.windows = [int(x) for x in args.windows.split(",") if x.strip()]
    return args


def main(argv=None):
    args = parse_args(argv)

    if args.power_calc:
        scenarios = power_calc_report(p0=args.dir_acc_expected)
        print(json.dumps(scenarios, indent=2))
        return 0

    report = run_watchdog(args)

    if args.json:
        print(json.dumps(report, indent=2))
    elif not args.quiet:
        print_human_summary(report)
        print("\n--- JSON report ---")
        print(json.dumps(report, indent=2))
    else:
        print(f"SPY_WATCHDOG_STATUS={report['overall_status']}")

    return 1 if report["overall_status"] == "WARNING" else 0


if __name__ == "__main__":
    sys.exit(main())
