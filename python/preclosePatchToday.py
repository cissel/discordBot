#!/usr/bin/env python3
"""
preclosePatchToday.py - append TODAY's provisional return row to the Alpaca
daily return caches (SPY/QQQ/GLD/USO .csv, columns date,<SYM>_ret) from the live
snapshot, for the 15:25 pre-close run. Alpaca's 1Day bars endpoint does not
return the in-progress bar, so without this the feature row for today cannot
be built. The 16:15 EOD run (fetchCrossAssetData, keep="last") overwrites
these rows with the real close. Idempotent: skips symbols that already have
today's row.
"""
import os, sys, datetime, requests
sys.path.insert(0, os.path.dirname(__file__))
from marketMaker import load_env  # noqa

CACHE = os.path.expanduser("~/discordBot/outputs/markets/cache")
SYMS = ["SPY", "QQQ", "GLD", "USO"]


def main():
    env = load_env()
    key = env.get("APCA_API_KEY_ID") or env.get("APCA_PAPER_API_KEY_ID")
    sec = env.get("APCA_API_SECRET_KEY") or env.get("APCA_PAPER_API_SECRET_KEY")
    h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    today = datetime.date.today().isoformat()
    r = requests.get("https://data.alpaca.markets/v2/stocks/snapshots", headers=h,
                     params={"symbols": ",".join(SYMS), "feed": "iex"}, timeout=20)
    r.raise_for_status()
    snaps = r.json()
    for s in SYMS:
        p = os.path.join(CACHE, f"{s}.csv")
        lines = open(p).read().rstrip("\n").split("\n")
        if lines[-1].startswith(today):
            print(f"  {s}: today's row already present")
            continue
        snap = snaps.get(s) or {}
        last = (snap.get("latestTrade") or {}).get("p")
        prev = (snap.get("prevDailyBar") or {}).get("c")
        if not last or not prev:
            print(f"  {s}: snapshot missing latestTrade/prevDailyBar, skipped")
            continue
        ret = last / prev - 1
        with open(p, "a") as f:
            f.write(f"{today},{ret}\n")
        print(f"  {s}: appended {today} provisional ret {ret:+.5f} (last {last} vs prev close {prev})")


if __name__ == "__main__":
    main()
