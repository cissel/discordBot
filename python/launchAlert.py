#!/usr/bin/env python3
"""
launchAlert.py - KSC launch alert checker
Called by cron every 30 minutes.

Checks the next Cape Canaveral/Kennedy launch and:
  - Posts a 24h alert if launch is within 24h-25h window (bot adds rocket emoji)
  - Posts a 1h alert to everyone who reacted with rocket to the 24h message
  - Posts a 5m alert to everyone who reacted with fire to the 1h message

State is stored in: ~/discordBot/outputs/space/launch_alerts.json
{
  "tracked": {
    "<launch_id>": {
      "name": "...",
      "launch_time_utc": "ISO",
      "alert_24h_sent": false,
      "alert_1h_sent": false,
      "alert_5m_sent": false,
      "msg_24h_id": null,
      "msg_1h_id": null,
      "channel_id": 1476278139167571968,
      "reactors_1h": [],   # user IDs who reacted rocket to 24h msg
      "reactors_5m": [],   # user IDs who reacted fire to 1h msg
    }
  }
}

Usage: python3 launchAlert.py
Prints lines prefixed with ACTION: for the bot to act on.
"""

import sys, os, json, requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from dateutil import parser as dtparser

BASE       = Path(os.path.expanduser("~/discordBot"))
STATE_FILE = BASE / "outputs/space/launch_alerts.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

SPACE_CENTER_CHANNEL = 1476278139167571968

ET = ZoneInfo("America/New_York")

ROCKET_EMOJI = "🚀"
FIRE_EMOJI   = "🔥"

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {"tracked": {}}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def fetch_next_launch() -> dict | None:
    url = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
    params = {"limit": 50, "ordering": "window_start", "mode": "detailed"}
    try:
        s = requests.Session()
        r = s.get(url, params=params, timeout=(8, 15), stream=False)
        r.raise_for_status()
        data = r.json()
        s.close()
        now  = datetime.now(timezone.utc)
        filtered = [
            l for l in data["results"]
            if any(x in l["pad"]["location"]["name"].lower()
                   for x in ["cape canaveral", "kennedy"])
            and dtparser.isoparse(l["window_end"]).astimezone(timezone.utc) > now
        ]
        if not filtered:
            return None
        filtered.sort(key=lambda x: x["window_start"])
        return filtered[0]
    except Exception as e:
        print(f"[launchAlert] fetch error: {e}", file=sys.stderr)
        return None

def fmt_et(dt_utc: datetime) -> str:
    return dt_utc.astimezone(ET).strftime("%A %b %d at %-I:%M %p ET")

def main():
    state  = load_state()
    now    = datetime.now(timezone.utc)
    launch = fetch_next_launch()

    if not launch:
        print("[launchAlert] no upcoming KSC launch found")
        return

    lid          = launch["id"]
    name         = launch["name"]
    provider     = launch["launch_service_provider"]["name"]
    pad          = launch["pad"]["name"]
    image        = launch.get("image") or ""
    status       = launch["status"]["name"]
    launch_time  = dtparser.isoparse(launch["window_start"]).astimezone(timezone.utc)
    delta        = launch_time - now
    delta_hours  = delta.total_seconds() / 3600
    mission_desc = ""
    if launch.get("mission"):
        mission_desc = launch["mission"].get("description", "") or ""
    if len(mission_desc) > 800:
        mission_desc = mission_desc[:797] + "..."

    # Init tracking entry for this launch
    tracked = state.setdefault("tracked", {})
    if lid not in tracked:
        tracked[lid] = {
            "name":           name,
            "launch_time_utc": launch_time.isoformat(),
            "alert_24h_sent": False,
            "alert_1h_sent":  False,
            "alert_5m_sent":  False,
            "msg_24h_id":     None,
            "msg_1h_id":      None,
            "channel_id":     SPACE_CENTER_CHANNEL,
            "reactors_1h":    [],
            "reactors_5m":    [],
        }

    entry = tracked[lid]

    # Prune old launches (more than 1 day past launch)
    to_remove = [k for k, v in tracked.items()
                 if dtparser.isoparse(v["launch_time_utc"]) < now - timedelta(days=1)]
    for k in to_remove:
        del tracked[k]

    # ── 24h alert ─────────────────────────────────────────────────────────────
    if not entry["alert_24h_sent"] and 23.5 <= delta_hours <= 25.5:
        days = delta.days
        hrs, rem = divmod(delta.seconds, 3600)
        mins = rem // 60
        print(f"ACTION:ALERT_24H:{lid}")
        print(f"  channel: {SPACE_CENTER_CHANNEL}")
        print(f"  name: {name}")
        print(f"  provider: {provider}")
        print(f"  pad: {pad}")
        print(f"  status: {status}")
        print(f"  time_et: {fmt_et(launch_time)}")
        print(f"  tminus: {days}d {hrs}h {mins}m")
        print(f"  mission: {mission_desc}")
        print(f"  image: {image}")
        print(f"  react_emoji: {ROCKET_EMOJI}")
        print(f"  react_prompt: React {ROCKET_EMOJI} for a 1-hour warning")
        entry["alert_24h_sent"] = True

    # ── 1h alert ──────────────────────────────────────────────────────────────
    elif not entry["alert_1h_sent"] and 0.75 <= delta_hours <= 1.5:
        hrs, rem = divmod(int(delta.total_seconds()), 3600)
        mins = rem // 60
        reactors = entry.get("reactors_1h", [])
        print(f"ACTION:ALERT_1H:{lid}")
        print(f"  channel: {SPACE_CENTER_CHANNEL}")
        print(f"  name: {name}")
        print(f"  time_et: {fmt_et(launch_time)}")
        print(f"  tminus: {hrs}h {mins}m")
        print(f"  reactors: {','.join(str(r) for r in reactors)}")
        print(f"  react_emoji: {FIRE_EMOJI}")
        print(f"  react_prompt: React {FIRE_EMOJI} for a 5-minute warning")
        entry["alert_1h_sent"] = True

    # ── 5m alert ──────────────────────────────────────────────────────────────
    elif not entry["alert_5m_sent"] and 0 <= delta_hours <= 0.15:
        mins = max(0, int(delta.total_seconds() // 60))
        reactors = entry.get("reactors_5m", [])
        print(f"ACTION:ALERT_5M:{lid}")
        print(f"  channel: {SPACE_CENTER_CHANNEL}")
        print(f"  name: {name}")
        print(f"  time_et: {fmt_et(launch_time)}")
        print(f"  tminus: T-{mins}m")
        print(f"  reactors: {','.join(str(r) for r in reactors)}")
        entry["alert_5m_sent"] = True

    else:
        print(f"[launchAlert] {name} in {delta_hours:.1f}h - no action needed")

    save_state(state)

if __name__ == "__main__":
    main()
