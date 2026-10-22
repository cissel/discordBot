#!/usr/bin/env python3
"""
clvGate.py
==========
Closing-line-value gate for the HardRock +EV bet pipelines (NFL + MLB).

Why: a model's +EV flags only mean something if the market subsequently
moves toward our side (positive CLV). Win/loss records need thousands of
bets to separate skill from noise; CLV separates them in weeks. So before a
flagged bet is surfaced as a recommendation (/nfl bets, /mlb bets) or graded
into the running record (nflBetsRecap / mlbBetsRecap), its (market,
modelStatus) bucket must have demonstrated positive CLV in the master log.

Rule (one decisive test, no maybes):
  PASS      bucket has >= MIN_N measurable CLV observations and the mean CLV
            is > 0 with t-stat >= MIN_T  (two-sided 95%-ish evidence)
  FAIL      bucket has >= MIN_N observations but does not meet that bar
  UNPROVEN  bucket has < MIN_N observations yet

Everything is STILL LOGGED to the master log regardless of gate status -
CLV is measured from the log, not from what gets surfaced, so gating never
starves a bucket of data. The gate is applied at the point of surfacing.

Usage
  import clvGate
  table = clvGate.bucket_table("nfl")             # {(market, modelStatus): {...}}
  df    = clvGate.annotate(df, "nfl", table)       # adds clvGate / clvBucketN / clvBucketAvg / clvBucketT
  clvGate.gate_status("mlb", market, model_status, table) -> ("PASS"|"FAIL"|"UNPROVEN", info)

  venv/bin/python3 python/clvGate.py               # print both bucket tables
"""
import os
import sys
import csv
import math
import json
import datetime as dt
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clvTracking  # noqa: E402

MIN_N = 50
MIN_T = 2.0

SPORTS = {
    "nfl": {
        "log": os.path.expanduser("~/discordBot/outputs/sports/nfl/betting/hardrock_bets_log.csv"),
        "cache": os.path.expanduser("~/discordBot/outputs/sports/nfl/betting/clv_buckets.json"),
        "game_col": "nflGame",
        "valid": lambda m: m not in ("no_model", "", None),
    },
    "mlb": {
        "log": os.path.expanduser("~/discordBot/outputs/sports/mlb/betting/hardrock_mlb_bets_log.csv"),
        "cache": os.path.expanduser("~/discordBot/outputs/sports/mlb/betting/clv_buckets.json"),
        "game_col": "mlbGame",
        "valid": lambda m: m in ("espn_bpi", "draftkings_devig", "mlbapi_poisson"),
    },
}


def bucket_table(sport, max_cache_age_h=6):
    """Per-(market, modelStatus) CLV stats from the master log. Cached as JSON
    for max_cache_age_h so the bot's slash commands don't re-parse 100k+ rows."""
    cfg = SPORTS[sport]
    cache = cfg["cache"]
    if os.path.exists(cache):
        age_h = (dt.datetime.now().timestamp() - os.path.getmtime(cache)) / 3600
        if age_h < max_cache_age_h:
            try:
                with open(cache) as f:
                    raw = json.load(f)
                return {tuple(k.split("|", 1)): v for k, v in raw["buckets"].items()}
            except Exception:
                pass
    table = _compute(sport)
    try:
        with open(cache, "w") as f:
            json.dump({"computed_at": dt.datetime.now().isoformat(timespec="seconds"),
                       "min_n": MIN_N, "min_t": MIN_T,
                       "buckets": {"|".join(k): v for k, v in table.items()}}, f, indent=1)
    except Exception:
        pass
    return table


def _compute(sport):
    cfg = SPORTS[sport]
    if not os.path.exists(cfg["log"]):
        return {}
    with open(cfg["log"]) as f:
        rows = list(csv.DictReader(f))
    events = defaultdict(list)
    for r in rows:
        events[(r.get(cfg["game_col"]), r.get("matchTime"))].append(r)
    buckets = defaultdict(list)
    for grp in events.values():
        res = clvTracking.compute_clv_for_event(grp, cfg["valid"])
        if not res:
            continue
        meta = {r["outcomeId"]: (r.get("market", ""), r.get("modelStatus", ""))
                for r in grp if r.get("outcomeId")}
        for oid, d in res.items():
            if d["single_snapshot"]:
                continue
            buckets[meta.get(oid, ("?", "?"))].append(d["clv"])
    table = {}
    for key, vals in buckets.items():
        n = len(vals)
        mean = sum(vals) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
        table[key] = {"n": n, "avg_clv": mean, "t": t,
                      "pct_beat": sum(1 for v in vals if v > 0) / n,
                      "status": _status(n, mean, t)}
    return table


def _status(n, mean, t):
    if n < MIN_N:
        return "UNPROVEN"
    return "PASS" if (mean > 0 and t >= MIN_T) else "FAIL"


def gate_status(sport, market, model_status, table=None):
    table = table if table is not None else bucket_table(sport)
    info = table.get((market or "", model_status or ""))
    if info is None:
        return "UNPROVEN", {"n": 0, "avg_clv": None, "t": None, "pct_beat": None, "status": "UNPROVEN"}
    return info["status"], info


def annotate(df, sport, table=None):
    """Add clvGate / clvBucketN / clvBucketAvg / clvBucketT columns to a
    hardrockEV-style DataFrame (needs market + modelStatus columns)."""
    table = table if table is not None else bucket_table(sport)
    gates, ns, avgs, ts = [], [], [], []
    for mk, ms in zip(df.get("market", [""] * len(df)), df.get("modelStatus", [""] * len(df))):
        st, info = gate_status(sport, mk, ms, table)
        gates.append(st)
        ns.append(info["n"])
        avgs.append(round(info["avg_clv"], 5) if info["avg_clv"] is not None else None)
        ts.append(round(info["t"], 2) if info["t"] is not None else None)
    df = df.copy()
    df["clvGate"] = gates
    df["clvBucketN"] = ns
    df["clvBucketAvg"] = avgs
    df["clvBucketT"] = ts
    return df


def row_passes(row):
    """For recap graders reading master-log rows: True if the row was logged
    with a PASS gate. Rows logged before the gate existed have no column and
    are treated as passing so the historical record is unchanged."""
    g = row.get("clvGate")
    return g in (None, "", "PASS")


def format_table(sport, table=None):
    table = table if table is not None else bucket_table(sport)
    lines = [f"{sport.upper()} CLV buckets (PASS needs n>={MIN_N}, mean>0, t>={MIN_T})"]
    for (mk, ms), v in sorted(table.items(), key=lambda kv: -kv[1]["n"]):
        lines.append(f"  {v['status']:<8} {mk:<22}{ms:<18} n={v['n']:<6} avg={v['avg_clv']*100:+.2f}pp "
                     f"beat={v['pct_beat']*100:.0f}%  t={v['t']:+.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    for s in SPORTS:
        print(format_table(s, _compute(s)))
        print()
