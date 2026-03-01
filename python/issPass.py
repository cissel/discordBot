# issPass.py
# Fetches the latest ISS TLE from Celestrak, computes the next N visible
# passes over Jacksonville, and writes a CSV for the bot to read.
#
# pip install ephem requests
#
# A "visible" pass requires:
#   - ISS elevation >= 10 degrees above horizon
#   - Observer in darkness (sun below -6 deg = civil twilight)
#   - ISS in sunlight (not in Earth's shadow)
# If no visible passes are found in the next 7 days, falls back to
# returning the next overhead pass regardless of visibility.

import ephem
import requests
import csv
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── config ─────────────────────────────────────────────────────────────────────
JAX_LAT    = "30.3322"
JAX_LON    = "-81.6557"
JAX_ELEV   = 5          # meters above sea level (Jacksonville is basically sea level)
N_PASSES   = 5          # how many passes to find
MIN_ELEV   = 10         # degrees — ignore grazing passes below this
DAYS_AHEAD = 7
OUTPUT     = Path("~/discordBot/outputs/space/issPasses.csv").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def az_to_compass(az_deg):
    idx = round(az_deg / 22.5) % 16
    return COMPASS[idx]

def fetch_tle():
    r = requests.get(TLE_URL, timeout=15,
                     headers={"User-Agent": "discordBot/1.0 personal project"})
    r.raise_for_status()
    lines = [l.strip() for l in r.text.strip().splitlines() if l.strip()]
    # TLE format: name, line1, line2
    # Celestrak may return just the two TLE lines or name + two lines
    if len(lines) == 2:
        return "ISS (ZARYA)", lines[0], lines[1]
    elif len(lines) >= 3:
        return lines[0], lines[1], lines[2]
    raise ValueError(f"Unexpected TLE response: {lines}")

def make_observer():
    obs = ephem.Observer()
    obs.lat  = JAX_LAT
    obs.lon  = JAX_LON
    obs.elev = JAX_ELEV
    obs.pressure = 0        # disable atmospheric refraction for cleaner math
    obs.horizon = f"{MIN_ELEV}"
    return obs

def is_visible(obs, iss, t):
    """True if ISS is in sunlight and observer is in darkness at time t."""
    obs.date = t
    iss.compute(obs)
    sun = ephem.Sun()
    sun.compute(obs)
    # Observer in darkness: sun below civil twilight (-6 deg)
    observer_dark = float(sun.alt) < math.radians(-6)
    # ISS in sunlight: not eclipsed
    iss_lit = not iss.eclipsed
    return observer_dark and iss_lit

def find_passes(tle_name, tle1, tle2):
    obs = make_observer()
    iss = ephem.readtle(tle_name, tle1, tle2)

    passes   = []
    now      = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD)
    obs.date = ephem.Date(now.strftime("%Y/%m/%d %H:%M:%S"))

    attempts = 0
    while len(passes) < N_PASSES and attempts < 200:
        attempts += 1
        try:
            rise_t, rise_az, max_t, max_el, set_t, set_az = obs.next_pass(iss)
        except Exception:
            break

        if rise_t is None:
            break

        rise_dt = ephem.Date(rise_t).datetime().replace(tzinfo=timezone.utc)
        if rise_dt > end_time:
            break

        max_el_deg = round(math.degrees(max_el), 1)

        # check visibility at peak
        visible = is_visible(obs, iss, max_t)

        passes.append({
            "rise_utc":    rise_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "rise_et":     rise_dt.astimezone(
                               timezone(timedelta(hours=-5))
                           ).strftime("%a %b %-d, %-I:%M %p ET"),
            "rise_az":     az_to_compass(math.degrees(rise_az)),
            "max_el":      max_el_deg,
            "set_az":      az_to_compass(math.degrees(set_az)),
            "duration_s":  int((ephem.Date(set_t) - ephem.Date(rise_t)) * 86400),
            "visible":     visible,
        })

        # advance past this pass
        obs.date = ephem.Date(set_t) + ephem.minute

    return passes

def main():
    print("[issPass] fetching TLE from Celestrak...")
    tle_name, tle1, tle2 = fetch_tle()
    print(f"[issPass] TLE: {tle_name}")

    passes = find_passes(tle_name, tle1, tle2)
    print(f"[issPass] found {len(passes)} passes")

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rise_utc", "rise_et", "rise_az", "max_el",
            "set_az", "duration_s", "visible"
        ])
        writer.writeheader()
        writer.writerows(passes)

    print(f"[issPass] saved {OUTPUT}")

if __name__ == "__main__":
    main()