"""
channelReader.py  —  backfill server messages into server_messages.csv

Run modes:
  python channelReader.py          # fetch all messages newer than latest in CSV
  python channelReader.py full     # full re-fetch from scratch (wipes CSV)

On first run (CSV missing/empty) it automatically does a full backfill.
After that each run only fetches new messages, so it gets faster over time.
"""

import asyncio
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN           = os.getenv("DISCORD_TOKEN")
GUILD_ID        = int(os.getenv("GUILD_ID"))
ALLOWED_CHANNELS = set(map(int, os.getenv("ALLOWED_CHANNELS", "").split(",")))

CSV_PATH = Path(os.path.expanduser("~/discordBot/outputs/metrics/server_messages.csv"))
FIELDS   = ["datetime", "user_name", "user_display_name", "channel", "message"]

FULL_MODE = len(sys.argv) > 1 and sys.argv[1] == "full"


def get_latest_timestamp() -> datetime | None:
    """Return the most recent message datetime already in the CSV, or None."""
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        return None
    latest = None
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["datetime"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest is None or ts > latest:
                        latest = ts
                except Exception:
                    pass
    except Exception:
        pass
    return latest


def append_rows(rows: list[dict]):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


intents = discord.Intents.default()
intents.messages = True
intents.guilds   = True
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

    if FULL_MODE:
        print("Full re-fetch mode — wiping existing CSV")
        CSV_PATH.unlink(missing_ok=True)
        after_dt = None
    else:
        after_dt = get_latest_timestamp()
        if after_dt:
            print(f"Incremental fetch — only messages after {after_dt.isoformat()}")
        else:
            print("CSV empty/missing — doing full backfill")

    total = 0
    for channel in guild.text_channels:
        if channel.id not in ALLOWED_CHANNELS:
            continue
        try:
            print(f"  Fetching #{channel.name}...")
            rows = []
            async for msg in channel.history(limit=None, after=after_dt, oldest_first=True):
                rows.append({
                    "datetime":          msg.created_at.isoformat(),
                    "user_name":         msg.author.name,
                    "user_display_name": msg.author.display_name,
                    "channel":           channel.name,
                    "message":           msg.content.replace("\n", "\\n"),
                })
            if rows:
                append_rows(rows)
                print(f"    +{len(rows)} messages")
                total += len(rows)
        except Exception as e:
            print(f"  Could not fetch #{channel.name}: {e}")

    print(f"✅ Done — {total} new messages written to {CSV_PATH}")
    await client.close()


client.run(TOKEN)
