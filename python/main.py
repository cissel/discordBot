# main.py  --  startup, lifecycle, message logger
# pip install discord.py python-dotenv pandas
#
# All slash commands live in commands.py.
# This file only handles:
#   - on_ready startup message
#   - graceful Ctrl-C shutdown
#   - daily mismatch precompute task (9am ET)
#   - live message CSV append (on_message)
#   - invite tracking (on_member_join)
#   - launch alert background loop

import asyncio
import csv
import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

# -- load libopus for voice audio -----------------------------------------------
try:
    discord.opus.load_opus("/usr/lib/aarch64-linux-gnu/libopus.so.0")
    print("[opus] loaded successfully")
except Exception as _e:
    print(f"[opus] load failed: {_e}")

# -- env ------------------------------------------------------------------------
load_dotenv()
TOKEN               = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID            = int(os.getenv("GUILD_ID", "0"))
TEST_ENV_CHANNEL_ID = int(os.getenv("TEST_ENV_CHANNEL_ID", "0"))
TROLL_THIS_USER_ID  = int(os.getenv("TROLL_THIS_USER_ID", "0"))

if not TOKEN:    raise SystemExit("Set DISCORD_TOKEN in your .env")
if not GUILD_ID: raise SystemExit("Set GUILD_ID in your .env")

GUILD = discord.Object(id=GUILD_ID)

ALLOWED_CHANNELS  = set(map(int, os.getenv("ALLOWED_CHANNELS", "").split(",")))
MESSAGES_CSV      = Path(os.path.expanduser("~/discordBot/outputs/metrics/server_messages.csv"))
MESSAGES_FIELDS   = ["datetime", "user_name", "user_display_name", "channel", "message"]

def _append_message(message: discord.Message):
    """Append a single message to the CSV."""
    try:
        MESSAGES_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not MESSAGES_CSV.exists() or MESSAGES_CSV.stat().st_size == 0
        with open(MESSAGES_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=MESSAGES_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "datetime":          message.created_at.isoformat(),
                "user_name":         message.author.name,
                "user_display_name": message.author.display_name,
                "channel":           message.channel.name if hasattr(message.channel, "name") else "unknown",
                "message":           message.content.replace("\n", "\\n"),
            })
    except Exception as e:
        print(f"[history] failed to append message: {e}")

# -- path constants (shared with commands.py) -----------------------------------
R_PATH      = os.path.expanduser("~/discordBot/r/")
PYTHON_PATH = os.path.expanduser("~/discordBot/python/")
OUTPUT_PATH = os.path.expanduser("~/discordBot/outputs/")

# -- bot / tree -----------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True          # needed for live CSV append
intents.members         = True          # needed for invite tracking / on_member_join
intents.reactions       = True          # needed for launch alert opt-in tracking

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        from commands import register_commands
        register_commands(self.tree, GUILD,
                          R_PATH=R_PATH,
                          PYTHON_PATH=PYTHON_PATH,
                          OUTPUT_PATH=OUTPUT_PATH)

        for cmd in self.tree.get_commands(guild=GUILD):
            print(f"  registered: /{cmd.name}")
            if hasattr(cmd, 'commands'):
                for sub in cmd.commands:
                    print(f"    /{cmd.name} {sub.name}")

        print(f"[setup] {len(self.tree.get_commands(guild=GUILD))} commands registered")
        try:
            synced = await self.tree.sync(guild=GUILD)
            print(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
        except Exception as e:
            print(f"[warn] sync failed: {e}")

    async def on_ready(self):
        print(f"Online as {self.user}", flush=True)
        ch = self.get_channel(TEST_ENV_CHANNEL_ID)
        if ch:
            await ch.send("going online!")
        asyncio.create_task(self._dj_warmup())
        asyncio.create_task(self._snapshot_invites())
        asyncio.create_task(self._launch_alert_loop())

    async def _snapshot_invites(self):
        """Cache current use-count for every invite in the guild."""
        try:
            guild = self.get_guild(GUILD_ID)
            if guild:
                invites = await guild.invites()
                self._invite_cache = {inv.code: inv for inv in invites}
                print(f"[invites] cached {len(self._invite_cache)} invites")
        except Exception as e:
            print(f"[invites] snapshot failed: {e}")
            self._invite_cache = {}

    async def on_member_join(self, member: discord.Member):
        """Diff invite counts to find which invite was just used, log to CSV."""
        import csv as _csv
        from pathlib import Path as _Path
        log_path = _Path(os.path.expanduser("~/discordBot/outputs/server/invite_log.csv"))
        log_path.parent.mkdir(parents=True, exist_ok=True)

        inviter_name = "unknown"
        inviter_id   = ""
        used_code    = ""

        try:
            new_invites = await member.guild.invites()
            new_cache   = {inv.code: inv for inv in new_invites}

            for code, new_inv in new_cache.items():
                old_inv = self._invite_cache.get(code)
                old_uses = old_inv.uses if old_inv else 0
                if new_inv.uses > old_uses:
                    used_code    = code
                    inviter_name = str(new_inv.inviter) if new_inv.inviter else "unknown"
                    inviter_id   = str(new_inv.inviter.id) if new_inv.inviter else ""
                    break

            self._invite_cache = new_cache

        except Exception as e:
            print(f"[invites] on_member_join diff failed: {e}")

        # append to log
        write_header = not log_path.exists()
        with open(log_path, "a", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["timestamp","member","member_id","inviter","inviter_id","code"])
            if write_header:
                w.writeheader()
            w.writerow({
                "timestamp":  discord.utils.utcnow().isoformat(),
                "member":     str(member),
                "member_id":  str(member.id),
                "inviter":    inviter_name,
                "inviter_id": inviter_id,
                "code":       used_code,
            })
        print(f"[invites] {member} joined via {inviter_name} (code: {used_code})")

    async def _dj_warmup(self):
        from commands import _dj_genres, _dj_artists
        try:
            genres  = await asyncio.to_thread(_dj_genres)
            artists = await asyncio.to_thread(_dj_artists)
            total   = sum(len(v) for v in genres.values())
            print(f"[DJ] indexes ready - {total} tracks · {len(genres)} genres · {len(artists)} artists")
        except Exception as e:
            print(f"[DJ] warmup failed: {e}")

    async def _launch_alert_loop(self):
        """Background loop - checks for launch alerts every 30 minutes."""
        await asyncio.sleep(60)  # wait 1 min after startup before first check
        while not self.is_closed():
            try:
                # fire as a separate task so a hang never blocks the event loop
                asyncio.create_task(self.run_launch_alerts())
            except Exception as e:
                print(f"[launchAlert] loop error: {e}")
            await asyncio.sleep(30 * 60)  # 30 minutes

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # -- live CSV append ---------------------------------------------------
        if hasattr(message.channel, "id") and message.channel.id in ALLOWED_CHANNELS:
            _append_message(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track reactions to launch alert messages for 1h/5m opt-in."""
        import json as _json
        from pathlib import Path as _Path

        if payload.user_id == self.user.id:
            return  # ignore bot's own reactions

        state_file = _Path(os.path.expanduser("~/discordBot/outputs/aerospace/launch_alerts.json"))
        if not state_file.exists():
            return

        try:
            state   = _json.loads(state_file.read_text())
            tracked = state.get("tracked", {})
            emoji   = str(payload.emoji)
            msg_id  = payload.message_id
            uid     = payload.user_id

            for lid, entry in tracked.items():
                # React rocket to 24h message -> opt into 1h alert
                if emoji == "🚀" and entry.get("msg_24h_id") == msg_id:
                    if uid not in entry["reactors_1h"]:
                        entry["reactors_1h"].append(uid)
                        state_file.write_text(_json.dumps(state, indent=2))
                        print(f"[launchAlert] user {uid} opted into 1h alert for {entry['name']}")
                    break

                # React fire to 1h message -> opt into 5m alert
                if emoji == "🔥" and entry.get("msg_1h_id") == msg_id:
                    if uid not in entry["reactors_5m"]:
                        entry["reactors_5m"].append(uid)
                        state_file.write_text(_json.dumps(state, indent=2))
                        print(f"[launchAlert] user {uid} opted into 5m alert for {entry['name']}")
                    break
        except Exception as e:
            print(f"[launchAlert] reaction handler error: {e}")

    async def run_launch_alerts(self):
        """
        Runs launchAlert.py, parses its output, posts messages.
        Hard 45s timeout so a hung HTTP call never blocks anything.
        """
        try:
            await asyncio.wait_for(self._run_launch_alerts_inner(), timeout=45)
        except asyncio.TimeoutError:
            print("[launchAlert] timed out after 45s - skipping this check")
        except Exception as e:
            print(f"[launchAlert] error: {e}")

    async def _run_launch_alerts_inner(self):
        import json as _json, subprocess as _sp
        from pathlib import Path as _Path

        PYTHON_PATH = os.path.expanduser("~/discordBot/venv/bin/python3")
        SCRIPT      = os.path.expanduser("~/discordBot/python/launchAlert.py")
        state_file  = _Path(os.path.expanduser("~/discordBot/outputs/aerospace/launch_alerts.json"))

        try:
            result = await asyncio.to_thread(
                lambda: _sp.run([PYTHON_PATH, SCRIPT],
                                capture_output=True, text=True, timeout=30)
            )
            output = result.stdout.strip()
        except Exception as e:
            print(f"[launchAlert] script error: {e}")
            return

        if not output:
            return

        lines  = output.splitlines()
        action = None
        fields = {}
        lid    = None

        async def parse_block():
            nonlocal action, fields, lid
            if not action or not lid:
                return

            try:
                state   = _json.loads(state_file.read_text()) if state_file.exists() else {"tracked": {}}
                tracked = state.get("tracked", {})
                channel = self.get_channel(int(fields.get("channel", 0)))
                if not channel:
                    print(f"[launchAlert] channel not found")
                    return

                name      = fields.get("name", "Launch")
                time_et   = fields.get("time_et", "TBD")
                tminus    = fields.get("tminus", "")
                image     = fields.get("image", "")
                provider  = fields.get("provider", "")
                pad       = fields.get("pad", "")
                status    = fields.get("status", "")
                mission   = fields.get("mission", "")
                react_p   = fields.get("react_prompt", "")
                reactors_str = fields.get("reactors", "")
                reactors  = [int(x) for x in reactors_str.split(",") if x.strip().isdigit()]

                if action == "ALERT_24H":
                    emb = discord.Embed(
                        title=f"🚀 Launch Alert - T-{tminus}",
                        description=f"**{name}**",
                        color=0x5865F2,
                    )
                    emb.add_field(name="Launch Window", value=time_et,  inline=True)
                    emb.add_field(name="Agency",        value=provider, inline=True)
                    emb.add_field(name="Pad",           value=pad,      inline=True)
                    emb.add_field(name="Status",        value=status,   inline=True)
                    if mission:
                        emb.add_field(name="Mission",  value=mission,  inline=False)
                    if react_p:
                        emb.set_footer(text=react_p)
                    if image and image.startswith("http"):
                        emb.set_image(url=image)
                    msg = await channel.send(embed=emb)
                    await msg.add_reaction("🚀")
                    if lid in tracked:
                        tracked[lid]["msg_24h_id"] = msg.id
                    state_file.write_text(_json.dumps(state, indent=2))
                    print(f"[launchAlert] 24h alert sent for {name} (msg {msg.id})")

                elif action == "ALERT_1H":
                    mentions = " ".join(f"<@{uid}>" for uid in reactors) if reactors else ""
                    emb = discord.Embed(
                        title=f"🚀 1 Hour to Launch - {name}",
                        description=f"**{time_et}**\nT-{tminus}",
                        color=0xFF6600,
                    )
                    if react_p:
                        emb.set_footer(text=react_p)
                    content = mentions if mentions else None
                    msg = await channel.send(content=content, embed=emb)
                    await msg.add_reaction("🔥")
                    if lid in tracked:
                        tracked[lid]["msg_1h_id"] = msg.id
                    state_file.write_text(_json.dumps(state, indent=2))
                    print(f"[launchAlert] 1h alert sent for {name} (msg {msg.id})")

                elif action == "ALERT_5M":
                    mentions = " ".join(f"<@{uid}>" for uid in reactors) if reactors else ""
                    emb = discord.Embed(
                        title=f"🔥 5 Minutes to Launch - {name}",
                        description=f"**{time_et}**\n{tminus}",
                        color=0xFF0000,
                    )
                    content = mentions if mentions else None
                    await channel.send(content=content, embed=emb)
                    print(f"[launchAlert] 5m alert sent for {name}")

            except Exception as e:
                print(f"[launchAlert] parse_block error: {e}")

        for line in lines:
            if line.startswith("ACTION:"):
                await parse_block()
                parts  = line.split(":")
                action = parts[1]
                lid    = parts[2] if len(parts) > 2 else None
                fields = {}
            elif line.startswith("  ") and ":" in line:
                k, _, v = line.strip().partition(": ")
                fields[k] = v

        await parse_block()

    async def close(self):
        ch = self.get_channel(TEST_ENV_CHANNEL_ID)
        if ch:
            print("sending goodbye message")
            await ch.send("going offline!")
            await asyncio.sleep(1)
        await super().close()


# -- run ------------------------------------------------------------------------
client = BotClient()
print(f"[startup] token present: {bool(TOKEN)}, token length: {len(TOKEN)}", flush=True)

async def main():
    async with client:
        await client.start(TOKEN)

try:
    print("[startup] calling asyncio.run...", flush=True)
    asyncio.run(main())
except (KeyboardInterrupt, SystemExit) as _e:
    print(f"[startup] clean exit: {type(_e).__name__}", flush=True)
except Exception as _e:
    import traceback
    print(f"[startup] FATAL: {_e}", flush=True)
    traceback.print_exc()
    raise
