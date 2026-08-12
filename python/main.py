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
from datetime import timezone
from pathlib import Path

import aiohttp
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
                "datetime":          message.created_at.astimezone(timezone.utc).isoformat(),
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

        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            import traceback
            tb = traceback.format_exc()
            cmd = interaction.command.name if interaction.command else "unknown"
            print(f"[cmd error] /{cmd}: {error}\n{tb}", flush=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(thinking=True, ephemeral=True)
                await interaction.followup.send(f"❌ command error: `{error}`", ephemeral=True)
            except Exception:
                pass

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
        asyncio.create_task(self._flight_watch_loop())
        asyncio.create_task(self._meteor_shower_alert_loop())
        asyncio.create_task(self._hurricane_alert_loop())

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
        """Background loop - checks for launch alerts.
        Every 30 minutes normally, every 5 minutes when a tracked launch is < 2h away."""
        await asyncio.sleep(60)  # wait 1 min after startup before first check
        while not self.is_closed():
            try:
                # fire as a separate task so a hang never blocks the event loop
                asyncio.create_task(self.run_launch_alerts())
            except Exception as e:
                print(f"[launchAlert] loop error: {e}")
            # Determine sleep interval: 5 min if a tracked launch is < 2h away, else 30 min
            try:
                import json as _json
                from pathlib import Path as _Path
                from datetime import datetime as _dt, timezone as _tz
                from dateutil import parser as _dp
                _sf = _Path(os.path.expanduser("~/discordBot/outputs/aerospace/launch_alerts.json"))
                if _sf.exists():
                    _st = _json.loads(_sf.read_text())
                    _now = _dt.now(_tz.utc)
                    _soon = any(
                        0 < (_dp.isoparse(v["launch_time_utc"]).astimezone(_tz.utc) - _now).total_seconds() < 2 * 3600
                        for v in _st.get("tracked", {}).values()
                        if not v.get("alert_5m_sent", False)
                    )
                    _interval = 5 * 60 if _soon else 30 * 60
                else:
                    _interval = 30 * 60
            except Exception:
                _interval = 30 * 60
            await asyncio.sleep(_interval)

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # -- live CSV append ---------------------------------------------------
        if hasattr(message.channel, "id") and message.channel.id in ALLOWED_CHANNELS:
            _append_message(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track reactions to launch alert messages for 1h/5m opt-in,
        and 🔔 reactions on /track messages for takeoff/landing opt-in."""
        import json as _json
        from pathlib import Path as _Path

        if payload.user_id == self.user.id:
            return  # ignore bot's own reactions

        emoji = str(payload.emoji)

        # ── flight watch opt-in (🔔 on a /track message) ─────────────────────
        if emoji == "🔔":
            watch_file = _Path(os.path.expanduser("~/discordBot/outputs/aerospace/flight_watches.json"))
            if watch_file.exists():
                try:
                    wstate = _json.loads(watch_file.read_text())
                    watches = wstate.get("watches", {})
                    entry = watches.get(str(payload.message_id))
                    if entry is not None:
                        if payload.user_id not in entry.get("reactors", []):
                            entry.setdefault("reactors", []).append(payload.user_id)
                            watch_file.write_text(_json.dumps(wstate, indent=2))
                            print(f"[flightWatch] user {payload.user_id} opted into alerts for {entry.get('tail_query')}")
                except Exception as e:
                    print(f"[flightWatch] reaction handler error: {e}")
            return

        state_file = _Path(os.path.expanduser("~/discordBot/outputs/aerospace/launch_alerts.json"))
        if not state_file.exists():
            return

        try:
            state   = _json.loads(state_file.read_text())
            tracked = state.get("tracked", {})
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

    async def _flight_watch_loop(self):
        """Background loop - checks watched flights (opted-in via /track's 🔔
        reaction) for takeoff/landing transitions. Polls every 3 minutes."""
        await asyncio.sleep(45)  # wait after startup before first check
        while not self.is_closed():
            try:
                asyncio.create_task(self.run_flight_watch_check())
            except Exception as e:
                print(f"[flightWatch] loop error: {e}")
            await asyncio.sleep(3 * 60)

    async def run_flight_watch_check(self):
        """Runs flightWatchCheck.py, parses ACTION: lines, posts takeoff/landing alerts."""
        try:
            await asyncio.wait_for(self._run_flight_watch_check_inner(), timeout=45)
        except asyncio.TimeoutError:
            print("[flightWatch] timed out after 45s - skipping this check")
        except Exception as e:
            print(f"[flightWatch] error: {e}")

    async def _run_flight_watch_check_inner(self):
        import subprocess as _sp

        PYTHON_PATH = os.path.expanduser("~/discordBot/venv/bin/python3")
        SCRIPT      = os.path.expanduser("~/discordBot/python/flightWatchCheck.py")

        try:
            result = await asyncio.to_thread(
                lambda: _sp.run([PYTHON_PATH, SCRIPT],
                                capture_output=True, text=True, timeout=60)
            )
            output = result.stdout.strip()
            if result.stderr:
                print(f"[flightWatch] stderr: {result.stderr.strip()}")
        except Exception as e:
            print(f"[flightWatch] script error: {e}")
            return

        if not output:
            return

        lines  = output.splitlines()
        action = None
        fields = {}
        wid    = None

        async def parse_block():
            nonlocal action, fields, wid
            if not action or not wid:
                return

            try:
                channel = self.get_channel(int(fields.get("channel", 0)))
                if not channel:
                    print("[flightWatch] channel not found")
                    return

                tail_query   = fields.get("tail_query", "")
                callsign     = fields.get("callsign", tail_query)
                registration = fields.get("registration", tail_query)
                reactors_str = fields.get("reactors", "")
                reactors     = [int(x) for x in reactors_str.split(",") if x.strip().isdigit()]
                mentions     = " ".join(f"<@{uid_}>" for uid_ in reactors) if reactors else None
                presumed     = fields.get("presumed", "false") == "true"

                label = callsign if callsign == registration else f"{callsign} ({registration})"

                if action == "TAKEOFF":
                    emb = discord.Embed(
                        title=f"🛫 {label} is airborne",
                        description="Wheels up! Track it with `/track " + tail_query + "`",
                        color=0x00bfff,
                    )
                    await channel.send(content=mentions, embed=emb)
                    print(f"[flightWatch] TAKEOFF alert sent for {label}")

                elif action == "LANDED":
                    note = " (presumed - lost ADS-B contact near touchdown)" if presumed else ""
                    emb = discord.Embed(
                        title=f"🛬 {label} has landed{note}",
                        description="Welcome down!",
                        color=0x00bfff,
                    )
                    await channel.send(content=mentions, embed=emb)
                    print(f"[flightWatch] LANDED alert sent for {label}")

            except Exception as e:
                print(f"[flightWatch] parse_block error: {e}")

        for line in lines:
            if line.startswith("ACTION:"):
                await parse_block()
                parts  = line.split(":")
                action = parts[1]
                wid    = parts[2] if len(parts) > 2 else None
                fields = {}
            elif line.startswith("  ") and ":" in line:
                k, _, v = line.strip().partition(": ")
                fields[k] = v

        await parse_block()

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
                                capture_output=True, text=True, timeout=60)
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

    async def _meteor_shower_alert_loop(self):
        """Background loop - checks the annual meteor shower calendar.
        Runs every 6 hours (no live API, static calendar - no need to poll fast)."""
        await asyncio.sleep(90)  # wait after startup before first check
        while not self.is_closed():
            try:
                asyncio.create_task(self.run_meteor_shower_alerts())
            except Exception as e:
                print(f"[meteorShower] loop error: {e}")
            await asyncio.sleep(6 * 60 * 60)

    async def run_meteor_shower_alerts(self):
        """Runs meteorShowerAlert.py, parses its output, posts messages.
        Hard 30s timeout - this is just local date math, should never hang."""
        try:
            await asyncio.wait_for(self._run_meteor_shower_alerts_inner(), timeout=30)
        except asyncio.TimeoutError:
            print("[meteorShower] timed out after 30s - skipping this check")
        except Exception as e:
            print(f"[meteorShower] error: {e}")

    async def _run_meteor_shower_alerts_inner(self):
        import subprocess as _sp

        PYTHON_PATH = os.path.expanduser("~/discordBot/venv/bin/python3")
        SCRIPT      = os.path.expanduser("~/discordBot/python/meteorShowerAlert.py")

        try:
            result = await asyncio.to_thread(
                lambda: _sp.run([PYTHON_PATH, SCRIPT],
                                capture_output=True, text=True, timeout=30)
            )
            output = result.stdout.strip()
            if result.stderr:
                print(f"[meteorShower] stderr: {result.stderr.strip()}")
        except Exception as e:
            print(f"[meteorShower] script error: {e}")
            return

        if not output:
            return

        lines  = output.splitlines()
        action = None
        fields = {}
        sid    = None

        async def parse_block():
            nonlocal action, fields, sid
            if not action or not sid:
                return

            try:
                channel = self.get_channel(int(fields.get("channel", 0)))
                if not channel:
                    print("[meteorShower] channel not found")
                    return

                name       = fields.get("name", "Meteor shower")
                peak_date  = fields.get("peak_date", "")
                zhr        = fields.get("zhr", "")
                radiant    = fields.get("radiant", "")
                notes      = fields.get("notes", "")

                if action == "HEADSUP":
                    emb = discord.Embed(
                        title=f"🌠 Meteor Shower Coming Up - {name}",
                        description=f"Peaks **{peak_date}** - mark your calendar for clear skies that night.",
                        color=0x5865F2,
                    )
                    emb.add_field(name="Peak Rate (ZHR)", value=f"~{zhr}/hr", inline=True)
                    emb.add_field(name="Radiant",         value=radiant,      inline=True)
                    if notes:
                        emb.add_field(name="Notes", value=notes, inline=False)
                    await channel.send(embed=emb)
                    print(f"[meteorShower] heads-up alert sent for {name}")

                elif action == "PEAK":
                    emb = discord.Embed(
                        title=f"🌠 {name} Peaks Tonight!",
                        description=f"**{peak_date}** - best viewing is after midnight, away from city lights.",
                        color=0xFF6600,
                    )
                    emb.add_field(name="Peak Rate (ZHR)", value=f"~{zhr}/hr", inline=True)
                    emb.add_field(name="Radiant",         value=radiant,      inline=True)
                    if notes:
                        emb.add_field(name="Notes", value=notes, inline=False)
                    await channel.send(embed=emb)
                    print(f"[meteorShower] peak alert sent for {name}")

            except Exception as e:
                print(f"[meteorShower] parse_block error: {e}")

        for line in lines:
            if line.startswith("ACTION:"):
                await parse_block()
                parts  = line.split(":")
                action = parts[1]
                sid    = parts[2] if len(parts) > 2 else None
                fields = {}
            elif line.startswith("  ") and ":" in line:
                k, _, v = line.strip().partition(": ")
                fields[k] = v

        await parse_block()

    async def _hurricane_alert_loop(self):
        """Background loop - checks NHC Atlantic tropical weather outlook and
        active storms. Runs every 30 minutes (matches the TWO's own 6-hourly
        cadence with headroom, and CurrentStorms.json's 5-min cache window)."""
        await asyncio.sleep(75)  # wait after startup before first check
        while not self.is_closed():
            try:
                asyncio.create_task(self.run_hurricane_alerts())
            except Exception as e:
                print(f"[hurricaneAlert] loop error: {e}")
            await asyncio.sleep(30 * 60)

    async def run_hurricane_alerts(self):
        """Runs hurricaneAlert.py, parses its output, posts messages.
        Hard 60s timeout - scrapes NHC's outlook feed, CurrentStorms.json, and
        (for named storms) a cone-image page fetch + download, so give it a
        bit more room than the launch/meteor checks."""
        try:
            await asyncio.wait_for(self._run_hurricane_alerts_inner(), timeout=60)
        except asyncio.TimeoutError:
            print("[hurricaneAlert] timed out after 60s - skipping this check")
        except Exception as e:
            print(f"[hurricaneAlert] error: {e}")

    async def _run_hurricane_alerts_inner(self):
        import subprocess as _sp

        PYTHON_PATH = os.path.expanduser("~/discordBot/venv/bin/python3")
        SCRIPT      = os.path.expanduser("~/discordBot/python/hurricaneAlert.py")

        try:
            result = await asyncio.to_thread(
                lambda: _sp.run([PYTHON_PATH, SCRIPT],
                                capture_output=True, text=True, timeout=55)
            )
            output = result.stdout.strip()
            if result.stderr:
                print(f"[hurricaneAlert] stderr: {result.stderr.strip()}")
        except Exception as e:
            print(f"[hurricaneAlert] script error: {e}")
            return

        if not output:
            return

        lines  = output.splitlines()
        action = None
        fields = {}
        oid    = None

        DISTURBANCE_CAT_COLOR = {"low": 0x3399FF, "medium": 0xFFCC00, "high": 0xFF6600}

        async def parse_block():
            nonlocal action, fields, oid
            if not action or not oid:
                return

            try:
                channel = self.get_channel(int(fields.get("channel", 0)))
                if not channel:
                    print("[hurricaneAlert] channel not found")
                    return

                name = fields.get("name", "System")

                if action in ("NEW_DISTURBANCE", "DISTURBANCE_UPGRADE"):
                    cat_7d   = fields.get("cat_7d", "low")
                    pct_7d   = fields.get("pct_7d", "?")
                    cat_48h  = fields.get("cat_48h", "low")
                    pct_48h  = fields.get("pct_48h", "?")
                    snippet  = fields.get("snippet", "")
                    outlook_image = fields.get("outlook_image", "")
                    color = DISTURBANCE_CAT_COLOR.get(cat_7d, 0x888888)

                    title = (f"🌴 New Tropical Disturbance - {oid}" if action == "NEW_DISTURBANCE"
                             else f"🌴 Disturbance Upgrade - {oid}")
                    emb = discord.Embed(title=title, description=f"**{name}**", color=color)
                    emb.add_field(name="Formation Chance (48h)", value=f"{cat_48h.title()} ({pct_48h}%)", inline=True)
                    emb.add_field(name="Formation Chance (7-day)", value=f"{cat_7d.title()} ({pct_7d}%)", inline=True)
                    if snippet:
                        emb.add_field(name="Details", value=snippet, inline=False)
                    emb.set_footer(text="Source: NHC / Atlantic Tropical Weather Outlook")

                    if outlook_image and os.path.exists(outlook_image):
                        file = discord.File(outlook_image, filename="outlook.png")
                        emb.set_image(url="attachment://outlook.png")
                        await channel.send(embed=emb, file=file)
                    else:
                        await channel.send(embed=emb)
                    print(f"[hurricaneAlert] {action} alert sent for {oid} ({name})")

                elif action in ("NEW_STORM", "CATEGORY_CHANGE"):
                    category_label = fields.get("category_label", "System")
                    classification = fields.get("classification", "")
                    intensity_kt   = fields.get("intensity_kt", "?")
                    pressure_mb    = fields.get("pressure_mb", "?")
                    lat            = fields.get("lat", "?")
                    lon            = fields.get("lon", "?")
                    move_dir       = fields.get("movement_dir", "?")
                    move_speed     = fields.get("movement_speed", "?")
                    adv_num        = fields.get("adv_num", "?")
                    cone_image     = fields.get("cone_image", "")
                    color          = int(fields.get("color", "0x888888"), 16) if fields.get("color") else 0x888888
                    emoji          = fields.get("emoji", "🌀")
                    previous_label = fields.get("previous_label", "")

                    if action == "NEW_STORM":
                        title = f"{emoji} New Named Storm - {name}"
                        desc  = f"**{category_label}**"
                    else:
                        title = f"{emoji} {name} Now a {category_label}"
                        desc  = f"Upgraded from {previous_label}" if previous_label else f"**{category_label}**"

                    emb = discord.Embed(title=title, description=desc, color=color)
                    emb.add_field(name="Position", value=f"{lat}, {lon}", inline=True)
                    emb.add_field(name="Max Sustained Winds", value=f"{intensity_kt} kt", inline=True)
                    emb.add_field(name="Pressure", value=f"{pressure_mb} mb", inline=True)
                    emb.add_field(name="Movement", value=f"{move_dir} at {move_speed} mph", inline=True)
                    emb.add_field(name="Advisory #", value=adv_num, inline=True)
                    emb.set_footer(text="Source: NHC / National Hurricane Center")

                    if cone_image and os.path.exists(cone_image):
                        file = discord.File(cone_image, filename="cone.png")
                        emb.set_image(url="attachment://cone.png")
                        await channel.send(embed=emb, file=file)
                    else:
                        await channel.send(embed=emb)
                    print(f"[hurricaneAlert] {action} alert sent for {name}")

                elif action == "STORM_DISSIPATED":
                    emb = discord.Embed(
                        title=f"✅ {name} No Longer Tracked",
                        description="This system has dissipated, gone post-tropical, or moved out of NHC's active list.",
                        color=0x00CC00,
                    )
                    emb.set_footer(text="Source: NHC / National Hurricane Center")
                    await channel.send(embed=emb)
                    print(f"[hurricaneAlert] dissipation notice sent for {name}")

            except Exception as e:
                print(f"[hurricaneAlert] parse_block error: {e}")

        for line in lines:
            if line.startswith("ACTION:"):
                await parse_block()
                parts  = line.split(":")
                action = parts[1]
                oid    = parts[2] if len(parts) > 2 else None
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
print(f"[startup] token present: {bool(TOKEN)}, token length: {len(TOKEN)}", flush=True)

# Transient network errors (DNS not ready yet, connection refused during boot,
# brief outages) should be retried with backoff instead of killing the process.
# Systemd's Restart=on-failure/RestartSec=60 was too coarse for this - a Pi
# network blip during boot could exhaust retries before DNS came back, leaving
# the bot down until a manual restart. Retry in-process first.
_NETWORK_RETRY_ERRORS = (
    aiohttp.ClientConnectorError,
    aiohttp.ClientConnectorDNSError,
    aiohttp.ClientOSError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)
_MAX_CONNECT_RETRIES = 8
_BACKOFF_BASE_SECONDS = 10
_BACKOFF_MAX_SECONDS = 300


async def main():
    for attempt in range(1, _MAX_CONNECT_RETRIES + 1):
        client = BotClient()
        try:
            async with client:
                await client.start(TOKEN)
            return  # clean shutdown (e.g. client.close() called)
        except _NETWORK_RETRY_ERRORS as _e:
            if attempt >= _MAX_CONNECT_RETRIES:
                print(
                    f"[startup] FATAL: network error after {attempt} attempts: {_e}",
                    flush=True,
                )
                raise
            delay = min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_MAX_SECONDS)
            print(
                f"[startup] network error on attempt {attempt}/{_MAX_CONNECT_RETRIES} "
                f"({type(_e).__name__}: {_e}) - retrying in {delay}s",
                flush=True,
            )
            await asyncio.sleep(delay)
        finally:
            if not client.is_closed():
                await client.close()


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
