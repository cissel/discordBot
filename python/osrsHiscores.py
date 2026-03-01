# osrsHiscores.py
# Fetches OSRS hiscores for the crew from the official Jagex API.
# No API key required.
#
# Usage: python3 osrsHiscores.py [skill]
#   skill = "total" or any skill name (attack, defence, etc.)
#   Defaults to "total" if no argument given.
#
# Writes: outputs/osrs/hiscores.csv
#   columns: player, skill, level, rank, xp

import sys, csv, time, urllib.request, urllib.parse
from pathlib import Path

OUTPUT = Path("~/discordBot/outputs/osrs/hiscores.csv").expanduser()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

PLAYERS = [
    "cissel",
    "SubieVapeski",
    "Pexci",
    "TrimIsLife",
    "Squidlies",
    "Wmwhite",
    "The Bog Body",
    "Fart Johnsun",
]

# Fixed order the Jagex CSV returns skills in
SKILLS = [
    "total",
    "attack", "defence", "strength", "hitpoints", "ranged",
    "prayer", "magic", "cooking", "woodcutting", "fletching",
    "fishing", "firemaking", "crafting", "smithing", "mining",
    "herblore", "agility", "thieving", "slayer", "farming",
    "runecraft", "hunter", "construction", "sailing",
]

SKILL_EMOJI = {
    "total":        "⚔️",
    "attack":       "⚔️",
    "defence":      "🛡️",
    "strength":     "💪",
    "hitpoints":    "❤️",
    "ranged":       "🏹",
    "prayer":       "🙏",
    "magic":        "🔮",
    "cooking":      "🍳",
    "woodcutting":  "🪓",
    "fletching":    "🪶",
    "fishing":      "🎣",
    "firemaking":   "🔥",
    "crafting":     "💎",
    "smithing":     "⚒️",
    "mining":       "⛏️",
    "herblore":     "🌿",
    "agility":      "🏃",
    "thieving":     "🗝️",
    "slayer":       "💀",
    "farming":      "🌾",
    "runecraft":    "🔵",
    "hunter":       "🐾",
    "construction": "🏠",
    "sailing":      "⛵",
}

BASE_URL = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={}"
HEADERS  = {"User-Agent": "discordBot/1.0 personal project"}

def fetch_player(rsn):
    url = BASE_URL.format(urllib.parse.quote(rsn))
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8")
        rows = text.strip().splitlines()
        result = {}
        for i, skill in enumerate(SKILLS):
            if i >= len(rows):
                break
            parts = rows[i].split(",")
            if len(parts) >= 2:
                result[skill] = {
                    "rank":  int(parts[0]) if parts[0] != "-1" else None,
                    "level": int(parts[1]),
                    "xp":    int(parts[2]) if len(parts) > 2 else 0,
                }
        return result
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[osrsHiscores] {rsn}: not on hiscores (unranked or doesn't exist)")
        else:
            print(f"[osrsHiscores] {rsn}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[osrsHiscores] {rsn}: {e}")
        return None

def main():
    target_skill = sys.argv[1].lower() if len(sys.argv) > 1 else "total"
    if target_skill not in SKILLS:
        print(f"[osrsHiscores] unknown skill '{target_skill}', defaulting to total")
        target_skill = "total"

    rows = []
    for rsn in PLAYERS:
        data = fetch_player(rsn)
        time.sleep(0.3)  # be polite to Jagex servers
        if data and target_skill in data:
            s = data[target_skill]
            rows.append({
                "player": rsn,
                "skill":  target_skill,
                "level":  s["level"],
                "rank":   s["rank"] if s["rank"] else "unranked",
                "xp":     s["xp"],
            })
        else:
            rows.append({
                "player": rsn,
                "skill":  target_skill,
                "level":  0,
                "rank":   "unranked",
                "xp":     0,
            })

    # Sort by level descending
    rows.sort(key=lambda r: r["level"], reverse=True)

    with open(OUTPUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player","skill","level","rank","xp"])
        w.writeheader()
        w.writerows(rows)

    print(f"[osrsHiscores] wrote {len(rows)} players for skill: {target_skill}")

if __name__ == "__main__":
    main()