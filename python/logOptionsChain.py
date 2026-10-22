#!/usr/bin/env python3
"""
logOptionsChain.py - save the full SPY options snapshot once a day.

Why: every strike / DTE / skew question about the condor book is blocked by
having no options price history (SPY_options_daily.csv keeps only a handful of
summary metrics). The chain is already fetched for volBook.py; writing it to
disk costs a few MB a day and in six months is the dataset for "which DTE and
delta carries the premium". Not used by anything live.

Out: outputs/markets/options_chain/SPY_YYYY-MM-DD.csv.gz
     columns: sym, exp, type, strike, bid, ask, mid, delta, gamma, theta, vega, iv, oi, volume, spot, ts
Idempotent: skips if today's file exists unless --force.
"""
import os
import sys
import gzip
import csv
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
from volBook import headers, get, spot_price, OCC, parse_occ, DATA  # noqa: E402

OUT_DIR = os.path.expanduser("~/discordBot/outputs/markets/options_chain")
DTE_MAX = 75


def main():
    force = "--force" in sys.argv
    today = dt.date.today()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"SPY_{today.isoformat()}.csv.gz")
    if os.path.exists(path) and not force:
        print(f"exists: {path}"); return 0
    h = headers()
    spot = spot_price(h)
    snaps, token = {}, None
    while True:
        p = {"feed": "indicative", "limit": 1000, "expiration_date_gte": today.isoformat(),
             "expiration_date_lte": (today + dt.timedelta(days=DTE_MAX)).isoformat()}
        if token:
            p["page_token"] = token
        d = get(f"{DATA}/v1beta1/options/snapshots/SPY", h, **p)
        snaps.update(d.get("snapshots", {}))
        token = d.get("next_page_token")
        if not token:
            break
    ts = dt.datetime.now().isoformat(timespec="seconds")
    n = 0
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "exp", "type", "strike", "bid", "ask", "mid", "delta", "gamma", "theta", "vega", "iv", "oi", "volume", "spot", "ts"])
        for sym, v in snaps.items():
            if not OCC.match(sym):
                continue
            exp, typ, k = parse_occ(sym)
            q = v.get("latestQuote") or {}; g = v.get("greeks") or {}; db = v.get("dailyBar") or {}
            bid, ask = q.get("bp"), q.get("ap")
            mid = (bid + ask) / 2 if bid is not None and ask is not None else None
            w.writerow([sym, exp.isoformat(), typ, k, bid, ask, mid, g.get("delta"), g.get("gamma"), g.get("theta"), g.get("vega"),
                        v.get("impliedVolatility"), v.get("openInterest"), db.get("v"), spot, ts])
            n += 1
    print(f"wrote {n} contracts -> {path} ({os.path.getsize(path)//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
