#!/usr/bin/env python3
"""
flightWatchCheck.py - checks watched flights (opted-in via /track's 🔔 reaction)
for takeoff / landing state transitions.

Called periodically by main.py's background loop (see _flight_watch_loop).

State is stored in: ~/discordBot/outputs/aerospace/flight_watches.json
{
  "watches": {
    "<discord_message_id>": {
      "tail_query": "EJA781",
      "callsign": "EJA781",
      "registration": "N781QS",
      "hex": "aa95ac",
      "channel_id": 123,
      "guild_id": 456,
      "last_on_ground": true,
      "reactors": [uid, ...],
      "active": true,
      "miss_count": 0,
      "created_utc": "ISO"
    }
  }
}

Usage: python3 flightWatchCheck.py
Prints lines prefixed with ACTION: for main.py to act on (TAKEOFF / LANDED).
"""

import sys, os, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE       = Path(os.path.expanduser("~/discordBot"))
STATE_FILE = BASE / "outputs/aerospace/flight_watches.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "discordBot/1.0 personal project"}
ADSB_HEX_URL = "https://opendata.adsb.fi/api/v2/hex/{hex}"

MAX_MISSES_BEFORE_PRESUMED_LANDED = 4  # ~ a few polling cycles with no ADS-B contact
MAX_WATCH_AGE_HOURS = 30  # auto-prune stale/abandoned watches


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"watches": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_by_hex(hex_id: str):
    try:
        r = requests.get(ADSB_HEX_URL.format(hex=hex_id), headers=HEADERS, timeout=15)
        r.raise_for_status()
        ac = r.json().get("ac") or []
        return ac[0] if ac else None
    except Exception as e:
        print(f"[flightWatch] hex lookup error for {hex_id}: {e}", file=sys.stderr)
        return None


def main():
    state = load_state()
    watches = state.setdefault("watches", {})
    now = datetime.now(timezone.utc)

    to_remove = []

    for msg_id, w in watches.items():
        if not w.get("active", True):
            continue

        try:
            created = datetime.fromisoformat(w.get("created_utc", now.isoformat()))
        except Exception:
            created = now
        if (now - created) > timedelta(hours=MAX_WATCH_AGE_HOURS):
            to_remove.append(msg_id)
            print(f"[flightWatch] pruning stale watch for {w.get('tail_query')} (msg {msg_id})")
            continue

        ac = fetch_by_hex(w["hex"])
        reactors_str = ",".join(str(r) for r in w.get("reactors", []))

        if ac is None:
            w["miss_count"] = w.get("miss_count", 0) + 1
            # if we lost contact while it was airborne, and we've missed it
            # enough consecutive checks, presume it landed (common near touchdown
            # as ADS-B ground stations lose the aircraft at low altitude).
            if not w.get("last_on_ground", True) and w["miss_count"] >= MAX_MISSES_BEFORE_PRESUMED_LANDED:
                print(f"ACTION:LANDED:{msg_id}")
                print(f"  channel: {w['channel_id']}")
                print(f"  tail_query: {w['tail_query']}")
                print(f"  callsign: {w['callsign']}")
                print(f"  registration: {w['registration']}")
                print(f"  reactors: {reactors_str}")
                print(f"  presumed: true")
                w["last_on_ground"] = True
                w["active"] = False  # stop watching after landing
            continue

        w["miss_count"] = 0
        alt = ac.get("alt_baro")
        is_ground = (alt == "ground")
        was_ground = w.get("last_on_ground", is_ground)

        if was_ground and not is_ground:
            print(f"ACTION:TAKEOFF:{msg_id}")
            print(f"  channel: {w['channel_id']}")
            print(f"  tail_query: {w['tail_query']}")
            print(f"  callsign: {w['callsign']}")
            print(f"  registration: {w['registration']}")
            print(f"  reactors: {reactors_str}")
            w["last_on_ground"] = False

        elif (not was_ground) and is_ground:
            print(f"ACTION:LANDED:{msg_id}")
            print(f"  channel: {w['channel_id']}")
            print(f"  tail_query: {w['tail_query']}")
            print(f"  callsign: {w['callsign']}")
            print(f"  registration: {w['registration']}")
            print(f"  reactors: {reactors_str}")
            print(f"  presumed: false")
            w["last_on_ground"] = True
            w["active"] = False  # stop watching after landing

        else:
            w["last_on_ground"] = is_ground

    for msg_id in to_remove:
        del watches[msg_id]

    # prune inactive (already alerted) watches older than 2h so the file doesn't grow forever
    to_remove2 = [
        msg_id for msg_id, w in watches.items()
        if not w.get("active", True)
    ]
    for msg_id in to_remove2:
        try:
            created = datetime.fromisoformat(watches[msg_id].get("created_utc", now.isoformat()))
            if (now - created) > timedelta(hours=2):
                del watches[msg_id]
        except Exception:
            pass

    save_state(state)

    if not any(w.get("active", True) for w in watches.values()):
        print("[flightWatch] no active watches")


if __name__ == "__main__":
    main()
