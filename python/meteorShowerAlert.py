#!/usr/bin/env python3
"""
meteorShowerAlert.py - Annual meteor shower alert checker
Called by bot's background loop (see _meteor_shower_alert_loop in main.py),
roughly every 6 hours.

Unlike launchAlert.py this doesn't hit a live API - meteor shower peak dates
are well-known annual astronomical events (IMO calendar), so we use a static
table of the major showers visible from mid-northern latitudes (tuned for
Jacksonville, FL / America/New_York) and just track which stage of alert has
already fired for the current year.

Posts:
  - HEADSUP alert ~3 days before peak night
  - PEAK alert on the peak night itself ("clear skies tonight, look up")

State is stored in: ~/discordBot/outputs/aerospace/meteor_shower_alerts.json
{
  "alerted": {
    "<year>-<shower_id>": {"headsup": true, "peak": true}
  }
}

Usage: python3 meteorShowerAlert.py
Prints lines prefixed with ACTION: for the bot to act on.
"""

import sys, os, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

BASE       = Path(os.path.expanduser("~/discordBot"))
STATE_FILE = BASE / "outputs/aerospace/meteor_shower_alerts.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

SPACE_CENTER_CHANNEL = 1476278139167571968

ET = ZoneInfo("America/New_York")

# Major annual meteor showers visible from mid-northern latitudes.
# peak_month/peak_day: typical peak night (date the peak *night* begins - the
# best viewing is generally after midnight into the following morning).
# zhr: zenithal hourly rate under ideal dark-sky conditions (real visible
# counts will be much lower with light pollution / moonlight).
SHOWERS = [
    {"id": "quadrantids",     "name": "Quadrantids",       "peak_month": 1,  "peak_day": 3,  "zhr": 110, "radiant": "Boötes",       "notes": "Sharp, short peak - easy to miss the window."},
    {"id": "lyrids",          "name": "Lyrids",            "peak_month": 4,  "peak_day": 22, "zhr": 18,  "radiant": "Lyra",         "notes": "Modest rates but can produce bright fireballs."},
    {"id": "eta_aquariids",   "name": "Eta Aquariids",     "peak_month": 5,  "peak_day": 5,  "zhr": 50,  "radiant": "Aquarius",     "notes": "Best viewed just before dawn; better from southern latitudes."},
    {"id": "delta_aquariids", "name": "Southern Delta Aquariids", "peak_month": 7, "peak_day": 30, "zhr": 25, "radiant": "Aquarius", "notes": "Broad, weak peak - overlaps with early Perseids."},
    {"id": "perseids",        "name": "Perseids",          "peak_month": 8,  "peak_day": 12, "zhr": 100, "radiant": "Perseus",      "notes": "The big one - most popular shower of the year, warm summer nights."},
    {"id": "draconids",       "name": "Draconids",         "peak_month": 10, "peak_day": 8,  "zhr": 10,  "radiant": "Draco",        "notes": "Usually quiet but has produced storms (1000s/hr) in rare years. Best viewed at dusk, not after midnight."},
    {"id": "orionids",        "name": "Orionids",          "peak_month": 10, "peak_day": 21, "zhr": 20,  "radiant": "Orion",        "notes": "Debris from Halley's Comet. Fast, bright meteors."},
    {"id": "taurids",         "name": "Taurids",           "peak_month": 11, "peak_day": 5,  "zhr": 7,   "radiant": "Taurus",       "notes": "Low rate but known for slow, bright fireballs."},
    {"id": "leonids",         "name": "Leonids",           "peak_month": 11, "peak_day": 17, "zhr": 15,  "radiant": "Leo",          "notes": "Occasionally storms (last major one 2001) but typically modest."},
    {"id": "geminids",        "name": "Geminids",          "peak_month": 12, "peak_day": 13, "zhr": 150, "radiant": "Gemini",       "notes": "Best shower of the year - high rate, works well even earlier in the evening."},
    {"id": "ursids",          "name": "Ursids",            "peak_month": 12, "peak_day": 22, "zhr": 10,  "radiant": "Ursa Minor",   "notes": "Minor shower right before Christmas, easy to overlook."},
]

HEADSUP_DAYS_OUT = 3   # fire the heads-up alert this many days before peak
STAR_EMOJI = "🌠"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"alerted": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def next_peak_date(shower: dict, today_et) -> datetime:
    """Return the datetime (ET, midnight) of this shower's peak, using the
    current year unless that date has already passed by more than a couple
    days, in which case roll to next year's occurrence."""
    year = today_et.year
    candidate = datetime(year, shower["peak_month"], shower["peak_day"], tzinfo=ET)
    if (today_et.date() - candidate.date()).days > 2:
        candidate = datetime(year + 1, shower["peak_month"], shower["peak_day"], tzinfo=ET)
    return candidate


def main():
    state   = load_state()
    alerted = state.setdefault("alerted", {})
    now_et  = datetime.now(ET)

    # prune alert entries older than 400 days (past year's cycle complete)
    to_remove = []
    for key in alerted:
        try:
            yr = int(key.split("-")[0])
            if yr < now_et.year - 1:
                to_remove.append(key)
        except Exception:
            pass
    for k in to_remove:
        del alerted[k]

    fired_any = False

    for shower in SHOWERS:
        peak_dt   = next_peak_date(shower, now_et)
        peak_year = peak_dt.year
        key       = f"{peak_year}-{shower['id']}"
        entry     = alerted.setdefault(key, {"headsup": False, "peak": False})

        days_until = (peak_dt.date() - now_et.date()).days

        # ── heads-up (3 days out) ────────────────────────────────────────
        if not entry["headsup"] and days_until == HEADSUP_DAYS_OUT:
            print(f"ACTION:HEADSUP:{shower['id']}")
            print(f"  channel: {SPACE_CENTER_CHANNEL}")
            print(f"  name: {shower['name']}")
            print(f"  peak_date: {peak_dt.strftime('%A, %B %d')}")
            print(f"  zhr: {shower['zhr']}")
            print(f"  radiant: {shower['radiant']}")
            print(f"  notes: {shower['notes']}")
            entry["headsup"] = True
            fired_any = True

        # ── peak night ("tonight") ───────────────────────────────────────
        if not entry["peak"] and days_until == 0:
            print(f"ACTION:PEAK:{shower['id']}")
            print(f"  channel: {SPACE_CENTER_CHANNEL}")
            print(f"  name: {shower['name']}")
            print(f"  peak_date: {peak_dt.strftime('%A, %B %d')}")
            print(f"  zhr: {shower['zhr']}")
            print(f"  radiant: {shower['radiant']}")
            print(f"  notes: {shower['notes']}")
            entry["peak"] = True
            fired_any = True

    if not fired_any:
        # find nearest upcoming shower for a quiet status line (stderr, not parsed)
        upcoming = min(SHOWERS, key=lambda s: (next_peak_date(s, now_et) - now_et))
        nearest_peak = next_peak_date(upcoming, now_et)
        print(f"[meteorShowerAlert] no alert due - nearest is {upcoming['name']} "
              f"on {nearest_peak.strftime('%b %d')} ({(nearest_peak.date() - now_et.date()).days}d out)",
              file=sys.stderr)

    save_state(state)


if __name__ == "__main__":
    main()
