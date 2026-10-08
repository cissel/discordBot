#!/usr/bin/env python3
"""
osrsHistoryBackfill.py  --  one-off reverse-engineering of OSRS level/XP
history from the bot's own past embed responses to /osrs lvl and
/osrs hiscores, across all text channels it can see.

Why: osrsLevelHistory.py's daily cron snapshot only started Sep 17 2026.
But James has been running /osrs lvl and /osrs hiscores manually since
well before that, and the bot's *responses* (embeds) to those commands
are still sitting in channel history with real level/xp numbers timestamped
at the moment the command was run. This script re-parses those embeds and
derives (date, player, skill, level, xp) rows to fill in history before
(and gaps within) the cron-logged period.

Usage:
  python3 osrsHistoryBackfill.py scan          # dry run: just report what it finds
  python3 osrsHistoryBackfill.py write         # write derived rows to
                                                #   outputs/osrs/level_history_backfill.csv

Does NOT touch outputs/osrs/level_history.csv directly - writes to a
separate file so the derived (lower-confidence, sparse) data can be
reviewed/merged deliberately rather than silently mixed with the canonical
daily cron snapshot.

Parses two embed shapes (must match commands.py /osrs lvl and /osrs
hiscores embed builders exactly - see those for the source format):

  /osrs lvl <player>  (title starts with "📜 ")
    - one embed = full skill sheet for ONE player at a point in time
    - per-skill fields: name="{emoji} {Skill}", value="`bar`\\n**{lvl}** / 99\\n{xp:,} xp"
    - total field: name="📊 Total Level", value="**{lvl:,}** / 2,376\\n{xp:,} xp\\nRank: ..."
    -> richest source: full 24-skill + total snapshot per message

  /osrs hiscores [skill]  (title starts with an emoji + " OSRS ")
    - one embed = leaderboard for ONE skill across ALL crew members
    - per-player fields: name="{prefix} {player}" (prefix = medal emoji or "`N.`")
      value (non-total) = "**{lvl}** / 99  `bar`\\nXP: {xp:,}  ·  Rank: {rank}"
      value (total)     = "**{lvl:,}** / 2,376  `bar`"   <- NOTE: no XP for total!
    -> only yields xp for non-total skills; total-skill hiscores embeds give
       level only (xp left blank in output, never fabricated)
"""
import csv
import os
import re
import sys
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/discordBot/.env"))
TOKEN    = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

ET = ZoneInfo("America/New_York")

OUT_DIR  = Path("~/discordBot/outputs/osrs").expanduser()
OUT_PATH = OUT_DIR / "level_history_backfill.csv"
FIELDNAMES = ["date", "player", "skill", "level", "rank", "xp", "source", "msg_created_utc", "channel"]

ALL_SKILLS = [
    "attack",      "hitpoints",    "mining",
    "strength",    "agility",      "smithing",
    "defence",     "herblore",     "fishing",
    "ranged",      "thieving",     "cooking",
    "prayer",      "crafting",     "firemaking",
    "magic",       "fletching",    "woodcutting",
    "runecraft",   "slayer",       "farming",
    "construction","hunter",       "sailing",
]
SKILL_SET = set(ALL_SKILLS) | {"total"}

OSRS_PLAYERS = [
    "captdeadhead", "SubieVapeski", "Pexci", "swampdog",
    "Squidlies", "Wmwhite", "TrimIsLife", "Fart Johnsun",
    "carpetweed",
]

WRITE_MODE = len(sys.argv) > 1 and sys.argv[1] == "write"

LVL_FIELD_RE   = re.compile(r"\*\*([\d,]+)\*\*\s*/\s*99\s*\n([\d,]+)\s*xp", re.IGNORECASE)
TOTAL_FIELD_RE = re.compile(r"\*\*([\d,]+)\*\*\s*/\s*2,376\s*\n([\d,]+)\s*xp", re.IGNORECASE)
HISCORE_NONTOTAL_RE = re.compile(r"\*\*([\d,]+)\*\*\s*/\s*99.*?XP:\s*([\d,]+)", re.IGNORECASE | re.DOTALL)
HISCORE_TOTAL_RE    = re.compile(r"\*\*([\d,]+)\*\*\s*/\s*2,376", re.IGNORECASE)


def _num(s):
    return int(s.replace(",", ""))


def parse_lvl_embed(embed):
    """Returns list of (skill, level, xp) for a /osrs lvl embed, or None if not that shape."""
    if not embed.title or not embed.title.startswith("📜"):
        return None
    rows = []
    for field in embed.fields:
        name = (field.name or "").strip()
        value = field.value or ""
        if name == "📊 Total Level":
            m = TOTAL_FIELD_RE.search(value)
            if m:
                rows.append(("total", _num(m.group(1)), _num(m.group(2))))
            continue
        # strip leading emoji to get skill name, e.g. "⚔️ Attack" -> "Attack"
        parts = name.split(" ", 1)
        if len(parts) != 2:
            continue
        skill = parts[1].strip().lower()
        if skill not in SKILL_SET:
            continue
        m = LVL_FIELD_RE.search(value)
        if m:
            rows.append((skill, _num(m.group(1)), _num(m.group(2))))
    return rows if rows else None


def parse_hiscores_embed(embed):
    """Returns (skill, [(player, level, xp_or_None), ...]) for a /osrs hiscores embed, or None."""
    if not embed.title:
        return None
    title = embed.title.strip()
    # title format: f"{emoji} OSRS {'Total Level' if total else skill.title()}"
    m = re.search(r"OSRS\s+(.+)$", title)
    if not m:
        return None
    label = m.group(1).strip().lower()
    skill = "total" if label == "total level" else label
    if skill not in SKILL_SET:
        return None

    results = []
    for field in embed.fields:
        name = (field.name or "").strip()
        value = field.value or ""
        player = None
        for p in OSRS_PLAYERS:
            if name.lower().endswith(p.lower()):
                player = p
                break
        if not player:
            continue
        if skill == "total":
            mt = HISCORE_TOTAL_RE.search(value)
            if mt:
                results.append((player, _num(mt.group(1)), None))
        else:
            mn = HISCORE_NONTOTAL_RE.search(value)
            if mn:
                results.append((player, _num(mn.group(1)), _num(mn.group(2))))
    return (skill, results) if results else None


intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("Guild not found!")
        await client.close()
        return

    me = client.user
    derived_rows = []
    lvl_msgs = 0
    hiscore_msgs = 0
    scanned_channels = 0

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me) if guild.me else None
        if perms and not perms.read_message_history:
            continue
        scanned_channels += 1
        chan_msg_count = 0
        chan_hit_count = 0
        try:
            async for msg in channel.history(limit=None, oldest_first=True):
                chan_msg_count += 1
                if chan_msg_count % 2000 == 0:
                    print(f"  #{channel.name}: scanned {chan_msg_count} msgs so far, {chan_hit_count} hits", flush=True)
                if msg.author.id != me.id or not msg.embeds:
                    continue
                for embed in msg.embeds:
                    created_et = msg.created_at.astimezone(ET)
                    date_str = created_et.date().isoformat()

                    lvl_rows = parse_lvl_embed(embed)
                    if lvl_rows:
                        lvl_msgs += 1
                        chan_hit_count += 1
                        # need the player RSN - lvl embed title is "📜 {rsn}"
                        rsn = embed.title[2:].strip() if embed.title else None
                        if not rsn:
                            continue
                        for skill, level, xp in lvl_rows:
                            derived_rows.append({
                                "date": date_str, "player": rsn, "skill": skill,
                                "level": level, "rank": "", "xp": xp,
                                "source": "chat_backfill_lvl",
                                "msg_created_utc": msg.created_at.astimezone(timezone.utc).isoformat(),
                                "channel": channel.name,
                            })
                        continue

                    hs = parse_hiscores_embed(embed)
                    if hs:
                        hiscore_msgs += 1
                        chan_hit_count += 1
                        skill, results = hs
                        for player, level, xp in results:
                            derived_rows.append({
                                "date": date_str, "player": player, "skill": skill,
                                "level": level, "rank": "", "xp": xp if xp is not None else "",
                                "source": "chat_backfill_hiscores",
                                "msg_created_utc": msg.created_at.astimezone(timezone.utc).isoformat(),
                                "channel": channel.name,
                            })
        except discord.Forbidden:
            print(f"  (no access to #{channel.name}, skipping)")
        except Exception as e:
            print(f"  error scanning #{channel.name}: {e}")
        print(f"#{channel.name}: done - {chan_msg_count} msgs, {chan_hit_count} osrs hits", flush=True)

    print(f"Scanned {scanned_channels} channels.")
    print(f"Found {lvl_msgs} /osrs lvl embed messages, {hiscore_msgs} /osrs hiscores embed messages.")
    print(f"Derived {len(derived_rows)} total (date, player, skill) data points.")

    if derived_rows:
        by_date = {}
        for r in derived_rows:
            by_date.setdefault(r["date"], 0)
            by_date[r["date"]] += 1
        print("Dates covered (derived):")
        for d in sorted(by_date):
            print(f"  {d}: {by_date[d]} rows")

    if WRITE_MODE and derived_rows:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_PATH, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(derived_rows)
        print(f"Wrote {len(derived_rows)} rows to {OUT_PATH}")
    elif WRITE_MODE:
        print("Nothing to write.")

    await client.close()


client.run(TOKEN)
