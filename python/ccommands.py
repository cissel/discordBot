# commands.py  ──  all slash commands
# Called from main.py via register_commands().
#
# MIGRATION NOTES
# ───────────────
# Every on_message keyword trigger has become a @app_commands.command().
# Where the original had a "thinking…" plain-text prefix, we now use
# interaction.response.defer(thinking=True) so Discord shows the spinner.
# Blocking subprocess/file-IO calls are run via asyncio.to_thread().
# All responses use interaction.followup.send() after a defer, or
# interaction.response.send_message() for instant replies.
#
# Commands are grouped into cogs-style groups using app_commands.Group
# so the slash-menu stays tidy:
#
#   /ping
#   /r2
#   /goodmorning
#   /bully  <target>          (one command, one choice param)
#   /chucktronic
#   /ts
#   /dontavius
#   /boobs
#   /scrote
#   /nuggets
#   /hali
#
#   /weather  today | tomorrow | surf | wavemap | windmap
#             jaxradar | flradar | usradar | jaxsat
#             buoywaves | tideplot | windplot | hurricane
#
#   /history  server | channel | user | daily
#
#   /cats     next | today | win | lost | hockeytoday | hockeytomorrow
#             kodak | barkov | bobby | thuggybobby | chucky | reino
#             ekblad | swaggy | marchand | drunkMarchand | benny | eetu
#             gaddy | sethjones | lundy | forsling | jesper | schmidty
#             pantr | ilikethepanthers | pleasecats | floridapanthers
#             fuckedm | curseedm | djkhaled | stanleycup2024
#
#   /nfl      nextgame | jags | jagsgame | jagswin | howboutthemjags
#             wr | fantasyscoreboard | epamap | room40points | amonra
#
#   /nba      scoreboard | today | tomorrow
#
#   /markets  fedrate | yieldcurve | yieldspread | yieldspreadshort
#
#   /space    nextlaunch
#
#   /jaxplanes
#   /serversdown
#   /standings            (already existed in original commands.py)
#   /duval  /westside

import asyncio
import os
import random
import subprocess
from pathlib import Path
from datetime import datetime

import discord
from discord import app_commands
import pandas as pd


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(*args, **kwargs):
    """Blocking subprocess.run wrapper (call via asyncio.to_thread)."""
    subprocess.run(list(args), check=False, timeout=120, **kwargs)

async def _defer(interaction: discord.Interaction, ephemeral=False):
    await interaction.response.defer(thinking=True, ephemeral=ephemeral)

async def _send(interaction: discord.Interaction, content=None, **kwargs):
    """Send a followup after defer."""
    await interaction.followup.send(content, **kwargs)

async def _quick(interaction: discord.Interaction, content=None, **kwargs):
    """Instant (no defer) response."""
    await interaction.response.send_message(content, **kwargs)


# ── register_commands ─────────────────────────────────────────────────────────

def register_commands(tree: app_commands.CommandTree, guild: discord.Object,
                      R_PATH: str, PYTHON_PATH: str, OUTPUT_PATH: str):
    """
    Called once from main.py setup_hook().
    Registers every slash command onto the provided tree.
    """

    rp  = R_PATH
    pp  = PYTHON_PATH
    op  = OUTPUT_PATH

    # ── /standings ────────────────────────────────────────────────────────────
    # (kept from original commands.py – full implementation preserved)
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz

    RSCRIPT  = _Path("~/discordBot/r/room40leaderboard.R").expanduser()
    CSV_PATH = _Path("~/discordBot/outputs/sports/nfl/room40leaderboard.csv").expanduser()

    def _to_pct(val):
        s = str(val).strip()
        if s.endswith("%"):
            try: return float(s[:-1]) / 100.0
            except ValueError: return float("nan")
        try:
            v = float(s)
            return v if 0.0 <= v <= 1.0 else v / 100.0
        except ValueError:
            return float("nan")

    def _fmt_mtime(p):
        try: return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        except FileNotFoundError: return "missing"

    def _load_df():
        if not CSV_PATH.exists():
            raise FileNotFoundError(f"CSV not found at {CSV_PATH}")
        cols = ["team_name","wins","losses","accuracy","fpts","fpts_against","ppts"]
        df = pd.read_csv(CSV_PATH)[cols].copy()
        to_i = lambda s: pd.to_numeric(s, errors="coerce").fillna(0).astype(int)
        df["wins"]   = to_i(df["wins"])
        df["losses"] = to_i(df["losses"])
        df["fpts"]   = pd.to_numeric(df["fpts"],         errors="coerce").fillna(0)
        df["fpa"]    = pd.to_numeric(df["fpts_against"], errors="coerce").fillna(0)
        df["ppts"]   = pd.to_numeric(df["ppts"],         errors="coerce").fillna(0)
        df = df.sort_values(["wins","fpts","ppts"], ascending=[False,False,False]).reset_index(drop=True)
        ICONS = {0:"🏆",1:"🥈",2:"🥉",3:"⭐",4:"5️⃣",5:"6️⃣",6:"7️⃣",7:"8️⃣",8:"😰",9:"🧻",10:"💩",11:"🚽"}
        rows = []
        for i, r in df.iterrows():
            prefix = ICONS.get(i, f"{i+1}")
            name = str(r["team_name"])[:26]
            if i < 3: name = f"**{name}**"
            acc = _to_pct(r["accuracy"])
            rows.append({
                "Team": f"{prefix} {name} ({int(r['wins'])}–{int(r['losses'])})",
                "PFPA": f"{r['fpts']:.0f}–{r['fpa']:.0f}",
                "Acc":  f"{acc:.1%}" if pd.notna(acc) else "—",
            })
        return pd.DataFrame(rows)

    def _make_pages(df, rows_per_page=12, title=":football: Room 40 Standings",
                    color=discord.Color.green()):
        pages = []
        for start in range(0, len(df), rows_per_page):
            ch  = df.iloc[start:start+rows_per_page]
            col = lambda s: "\n".join(s.astype(str)) or "—"
            emb = discord.Embed(title=title, color=color)
            emb.add_field(name="Team",     value=col(ch["Team"]), inline=True)
            emb.add_field(name="PF–PA",    value=col(ch["PFPA"]), inline=True)
            emb.add_field(name="Accuracy", value=col(ch["Acc"]),  inline=True)
            emb.set_footer(text="scoreboard")
            pages.append(emb)
        return pages

    def _refresh_csv():
        subprocess.run(["Rscript", str(RSCRIPT), str(CSV_PATH)],
                       check=False, timeout=90)

    @tree.command(name="standings", description="Show fantasy league standings", guild=guild)
    async def standings(interaction: discord.Interaction):
        await _defer(interaction)
        try:
            await asyncio.to_thread(_refresh_csv)
            df    = await asyncio.to_thread(_load_df)
            pages = _make_pages(df)
            if not pages:
                await _send(interaction, "No standings available right now.")
                return
            await interaction.edit_original_response(embed=pages[0])
            for emb in pages[1:]:
                await _send(interaction, embed=emb)
        except FileNotFoundError as e:
            await _send(interaction, str(e), ephemeral=True)
        except Exception as e:
            await _send(interaction, f"Error building standings: {e}", ephemeral=True)

    # ── /ping ─────────────────────────────────────────────────────────────────
    @tree.command(name="ping", description="pong 🏓", guild=guild)
    async def ping(interaction: discord.Interaction):
        await _quick(interaction, "pong 🏓")

    # ── /duval /westside ──────────────────────────────────────────────────────
    @tree.command(name="duval", description="duval", guild=guild)
    async def duval(interaction: discord.Interaction):
        await _quick(interaction, "bang em")

    @tree.command(name="westside", description="westside", guild=guild)
    async def westside(interaction: discord.Interaction):
        await _quick(interaction, "jville")

    # ── /ts ───────────────────────────────────────────────────────────────────
    @tree.command(name="ts", description="type shit", guild=guild)
    async def ts(interaction: discord.Interaction):
        await _quick(interaction, "ong fr")

    # ── /goodmorning ──────────────────────────────────────────────────────────
    @tree.command(name="goodmorning", description="gm 🌞", guild=guild)
    async def goodmorning(interaction: discord.Interaction):
        await _quick(interaction, "good morning! :)")

    # ── /dontavius ────────────────────────────────────────────────────────────
    @tree.command(name="dontavius", description="ayo dontavius", guild=guild)
    async def dontavius(interaction: discord.Interaction):
        await _quick(interaction, "you gotta stay gaming bruh. don't focus on no girls just stay gaming.")

    # ── /r2 ───────────────────────────────────────────────────────────────────
    @tree.command(name="r2", description="random bot sound", guild=guild)
    async def r2(interaction: discord.Interaction):
        audio_folder = os.path.join(op, "botSounds")
        files = [os.path.join(audio_folder, f) for f in os.listdir(audio_folder)
                 if f.endswith((".wav", ".mp3", ".mp4"))]
        if not files:
            await _quick(interaction, "no audio files found")
            return
        await interaction.response.send_message(file=discord.File(random.choice(files)))

    # ── /chucktronic ──────────────────────────────────────────────────────────
    @tree.command(name="chucktronic", description="chucktronic", guild=guild)
    async def chucktronic(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "misc/chucktronic.jpg")))

    # ── /serversdown ──────────────────────────────────────────────────────────
    @tree.command(name="serversdown", description="EA SPORTS YOUR MOTHAFUCKIN SERVER IS DOWN", guild=guild)
    async def serversdown(interaction: discord.Interaction):
        await _defer(interaction)
        lines = [
            "EA SPORTS YOUR MOTHAFUCKIN SERVER IS DOWN",
            "get that shit back up we tryna play",
            "there's a bunch of motherfuckers at home that's locked and loaded right now",
            "FIX THIS SHIT",
            "Bill Gates Microsoft whoever the fuck",
            "all these mothafuckin",
            "bullshit ass vaccines yall tryna give us",
            "FIX THE MOTHAFUCKIN VIDEO GAME :ninja:",
        ]
        for line in lines:
            await _send(interaction, line)
            await asyncio.sleep(2)
        await _send(interaction, "NOW", file=discord.File(os.path.join(op, "misc/serversDown.mp4")))

    # ── /scrote ───────────────────────────────────────────────────────────────
    @tree.command(name="scrote", description="trae young", guild=guild)
    async def scrote(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nba/traeYoung.png")))

    # ── /nuggets ──────────────────────────────────────────────────────────────
    @tree.command(name="nuggets", description="denver nuggets", guild=guild)
    async def nuggets(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "joke around and find out")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nba/thugJokic.jpg")))

    # ── /hali ─────────────────────────────────────────────────────────────────
    @tree.command(name="hali", description="the haliban strikes again", guild=guild)
    async def hali(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "the haliban strikes again")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nba/hali.png")))

    # ── /boobs ────────────────────────────────────────────────────────────────
    @tree.command(name="boobs", description="🎲", guild=guild)
    async def boobs(interaction: discord.Interaction):
        if random.random() < 0.333:
            await _quick(interaction, "gulag")
            return
        folder = os.path.join(op, "misc/bb")
        files  = [os.path.join(folder, f) for f in os.listdir(folder)
                  if f.endswith((".png",".jpg",".jpeg",".gif",".mov"))]
        if not files:
            await _quick(interaction, "no files found")
            return
        await interaction.response.send_message(file=discord.File(random.choice(files)))

    # ── /bully ────────────────────────────────────────────────────────────────
    # All bully targets in one command with an autocomplete choice
    BULLY_RESPONSES = {
        "zooksy":  [("!kingcap", 0), ("🤣🫵", 1)],
        "chuck":   [("!kingcap", 0), ("🤣🫵", 1)],
        "fish":    [("fuck you fish", 0), ("🤣🫵", 1)],
        "bryce":   [("no", 0), ("squidly rulez", 1)],
        "james":   [
            ("which one bro there are several james in here", 0),
            ("you know what", 1), ("fuck you james", 1), ("fuck you too james", 1),
        ],
        "cissel":  [("fuck you james", 0), ("🤣🫵", 1)],
        "jp":      [("fuck you p", 0), ("🤣🫵", 1)],
        "p":       [("fuck you p", 0), ("🤣🫵", 1)],
        "peyton":  [("fuck you p", 0), ("🤣🫵", 1)],
        "eli":     [("silky johnson player hater of the year 2025", 0)],
        "brandon": [("hell nah", 0), ("we did hard time together", 1)],
        "vapedad": [("hell nah", 0), ("we did hard time together", 1)],
        "jordan":  [("nah jordan's only ever been nice to me", 0)],
        "chevy":   [("yo fuck you bubba", 0), ("🤣🫵", 1), ("ayo u know im jk i love u bubba", 1)],
        "verv":    [("i'll swiss cheese ur ass right here twin", 0), ("JIT", 1)],
        "tyler":   [("fuck you tyler", 0), ("get your tall strong handsome ass outta here smh", 1)],
        "tyjo":    [("fuck you tyler", 0), ("get your tall strong handsome ass outta here smh", 1)],
    }

    @tree.command(name="bully", description="bully someone", guild=guild)
    @app_commands.describe(target="who's getting it")
    async def bully(interaction: discord.Interaction, target: str):
        key = target.lower().strip()
        lines = BULLY_RESPONSES.get(key)
        if not lines:
            await _quick(interaction, f"i don't know who {target} is lol")
            return
        await _defer(interaction)
        first = True
        for text, delay in lines:
            if delay:
                await asyncio.sleep(delay)
            if first:
                await _send(interaction, text)
                first = False
            else:
                await _send(interaction, text)

    @bully.autocomplete("target")
    async def bully_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=k, value=k)
            for k in BULLY_RESPONSES
            if current.lower() in k
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # WEATHER GROUP  /weather <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    weather_group = app_commands.Group(name="weather", description="weather commands", guild_ids=[guild.id])

    @weather_group.command(name="today", description="today's weather")
    async def weather_today(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "weathe.R"))
        await _send(interaction, "have a great day :)",
                    file=discord.File(os.path.join(op, "weather/weatherTd.png")))

    @weather_group.command(name="tomorrow", description="tomorrow's weather")
    async def weather_tomorrow(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "weatherTm.R"))
        await _send(interaction, "here ya go:",
                    file=discord.File(os.path.join(op, "weather/weatherTm.png")))

    @weather_group.command(name="surf", description="surf forecast")
    async def surf(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "surf4castPlot.R"))
        await _send(interaction, "here's the latest surf forecast:",
                    file=discord.File(os.path.join(op, "weather/surf_fcst.png")))

    @weather_group.command(name="wavemap", description="wave forecast map (slow)")
    async def wavemap(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "surfMap.R"))
        await _send(interaction, "here's the latest forecast map:",
                    file=discord.File(os.path.join(op, "weather/wave_animation.gif")))

    @weather_group.command(name="windmap", description="wind forecast map (slow)")
    async def windmap(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "windMap.R"))
        await _send(interaction, "here's the latest wind forecast map:",
                    file=discord.File(os.path.join(op, "weather/wind_animation.gif")))

    @weather_group.command(name="jaxradar", description="Jacksonville NWS radar loop")
    async def jaxradar(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "jaxRada.R"))
        await _send(interaction, "here's the latest radar loop:",
                    file=discord.File(os.path.join(op, "weather/nwsJaxRadar.gif")))

    @weather_group.command(name="flradar", description="Florida radar loop")
    async def flradar(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "flRada.R"))
        await _send(interaction, "here's the latest radar loop:",
                    file=discord.File(os.path.join(op, "weather/flRadar.gif")))

    @weather_group.command(name="usradar", description="US radar loop")
    async def usradar(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "usRada.R"))
        await _send(interaction, "here's the latest radar loop:",
                    file=discord.File(os.path.join(op, "weather/usRadar.gif")))

    @weather_group.command(name="jaxsat", description="Jacksonville satellite gif")
    async def jaxsat(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "jaxSat.R"))
        await _send(interaction, "here's the latest satellite view:",
                    file=discord.File(os.path.join(op, "weather/nwsJaxSat.gif")))

    @weather_group.command(name="buoywaves", description="NOAA buoy wave plot")
    async def buoywaves(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "noaaBuoy.R"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "buoyWavePlot.R"))
        await _send(interaction, "latest observations from NOAA buoy #41117:",
                    file=discord.File(os.path.join(op, "weather/buoyWaves.png")))

    @weather_group.command(name="tideplot", description="Mayport tide plot")
    async def tideplot(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "tidePlot.R"))
        await _send(interaction, "current tides:",
                    file=discord.File(os.path.join(op, "weather/mayportTides.png")))

    @weather_group.command(name="windplot", description="Mayport wind plot")
    async def windplot(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "mayportWind.R"))
        await _send(interaction, "current winds:",
                    file=discord.File(os.path.join(op, "weather/mayportWinds.png")))

    @weather_group.command(name="hurricane", description="7-day tropical weather outlook")
    async def hurricane(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "hurricane.R"))
        await _send(interaction, "here's the 7 day tropical weather outlook",
                    file=discord.File(os.path.join(op, "weather/two7d.png")))

    tree.add_command(weather_group)

    # ─────────────────────────────────────────────────────────────────────────
    # HISTORY GROUP  /history <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    history_group = app_commands.Group(name="history", description="server history charts", guild_ids=[guild.id])

    @history_group.command(name="server", description="all-server message history")
    async def hist_server(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "serverHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/allMessages.png")))

    @history_group.command(name="channel", description="per-channel message history")
    async def hist_channel(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "channelHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/channelMessages.png")))

    @history_group.command(name="user", description="per-user message history")
    async def hist_user(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "userHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/userMessages.png")))

    @history_group.command(name="daily", description="daily messages per day plot")
    async def hist_daily(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "dailyMessage.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/dailyMessages.png")))

    tree.add_command(history_group)

    # ─────────────────────────────────────────────────────────────────────────
    # CATS GROUP  /cats <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    cats_group = app_commands.Group(name="cats", description="Florida Panthers commands", guild_ids=[guild.id])

    # player images ────────────────────────────────────────────────────────────
    def _cats_image(path_fragment, prefix_msg=None):
        """Factory: returns a subcommand handler that optionally sends text then an image."""
        async def _cmd(interaction: discord.Interaction):
            if prefix_msg:
                await _defer(interaction)
                await _send(interaction, prefix_msg)
                await _send(interaction, file=discord.File(os.path.join(op, path_fragment)))
            else:
                await interaction.response.send_message(
                    file=discord.File(os.path.join(op, path_fragment)))
        return _cmd

    @cats_group.command(name="barkov", description="Barkov :)")
    async def barkov(interaction: discord.Interaction):
        await interaction.response.send_message(
            ":)", file=discord.File(os.path.join(op, "sports/nhl/barky.png")))

    @cats_group.command(name="bobby", description="BRICK WALL BOB")
    async def bobby(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BRICK WALL BOB")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/brickwallbob.jpg")))

    @cats_group.command(name="thuggybobby", description="iced out bobby")
    async def thuggybobby(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/bobbyChain.png")))

    @cats_group.command(name="praisebobby", description="bobby bless")
    async def praisebobby(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "bobby bless")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/stbobby.jpeg")))

    @cats_group.command(name="chucky", description="Tkachuk")
    async def chucky(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/chucky.jpeg")))

    @cats_group.command(name="reino", description="i love you sam <3")
    async def reino(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "i love you sam <3")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/reino.png")))

    @cats_group.command(name="ekblad", description="BOOSTED EKKY")
    async def ekblad(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BOOSTED EKKY")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/ekblad.jpeg")))

    @cats_group.command(name="swaggy", description="NEVER FORGET")
    async def swaggy(interaction: discord.Interaction):
        await interaction.response.send_message(
            "NEVER FORGET",
            file=discord.File(os.path.join(op, "sports/nhl/buttpuck.mov")))

    @cats_group.command(name="marchand", description="ALL HAIL THE RAT KING")
    async def marchand(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "ALL HAIL THE RAT KING")
        await _send(interaction, "PANTHERS LEGEND AND FUTURE HALL OF FAMER BRADLEY MARCHAND",
                    file=discord.File(os.path.join(op, "sports/nhl/marchand.png")))

    @cats_group.command(name="drunkMarchand", description="BRAD MF MARCHAND")
    async def drunkMarchand(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BRAD MF MARCHAND")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/drunkMarchand.jpg")))

    @cats_group.command(name="benny", description="Sam Bennett")
    async def benny(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/benny.jpeg")))

    @cats_group.command(name="eetu", description="Luostarinen")
    async def eetu(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/eetu.png")))

    @cats_group.command(name="gaddy", description="HEY SIRI PLAY SICKO MODE")
    async def gaddy(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "HEY SIRI PLAY SICKO MODE")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/gadjo.jpg")))

    @cats_group.command(name="sethjones", description="SETH MF JONES")
    async def sethjones(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "SETH MF JONES")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/sethjones.jpeg")))

    @cats_group.command(name="lundy", description="lundy a mf shooter fr")
    async def lundy(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "lundy a mf shooter fr")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/lundell.png")))

    @cats_group.command(name="forsling", description="Forsling")
    async def forsling(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/forsling.png")))

    @cats_group.command(name="jesper", description="average jesper boqvist moment")
    async def jesper(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "average jesper boqvist moment")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/jesper.png")))

    @cats_group.command(name="schmidty", description="NATE THE GREAT")
    async def schmidty(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "NATE THE GREAT")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/nateSchmidt.png")))

    @cats_group.command(name="pantr", description="pantr hands")
    async def pantr(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/pantrHands.jpeg")))

    @cats_group.command(name="kodak", description="Kodak")
    async def kodak(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/kodak.jpg")))

    # game / schedule ──────────────────────────────────────────────────────────
    @cats_group.command(name="next", description="next Panthers game + odds")
    async def cats_next(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "nextCats.py"))
        csv = os.path.join(op, "sports/nhl/nextTeamGame.csv")
        if not os.path.exists(csv):
            await _send(interaction, "couldnt find game info 😿", ephemeral=True)
            return
        row = pd.read_csv(csv).iloc[0]
        emb = discord.Embed(
            title="Next Florida Panthers Game",
            description=f"**{row['matchup']}**",
            color=0xB9975B,
        )
        emb.add_field(name="📅 When",  value=row["time"],  inline=False)
        emb.add_field(name="🏟️ Where", value=row["venue"], inline=False)
        emb.set_footer(text="vamos gatos")
        emb.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/en/5/5d/Florida_Panthers_2023_logo.svg")
        await _send(interaction, embed=emb)

    @cats_group.command(name="hockeytoday", description="NHL games today")
    async def hockeytoday(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "nhlToday.py"))
        csv = os.path.join(op, "sports/nhl/gamesToday.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hockey today :(")
            return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏒 Today's NHL Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    @cats_group.command(name="hockeytomorrow", description="NHL games tomorrow")
    async def hockeytomorrow(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "nhlTomorrow.py"))
        csv = os.path.join(op, "sports/nhl/gamesTomorrow.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hockey tomorrow :(")
            return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏒 Tomorrow's NHL Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    @cats_group.command(name="win", description="cats win!")
    async def cats_win(interaction: discord.Interaction):
        await interaction.response.send_message(
            "W", file=discord.File(os.path.join(op, "sports/nhl/catsWin.png")))

    @cats_group.command(name="lost", description="cats lost :(")
    async def cats_lost(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/sickAssPanther.webp")))

    @cats_group.command(name="stanleycup2024", description="vamos gatos")
    async def stanleycup2024(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "vamos gatos")
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "floridaPanthe.R"))
        await _send(interaction, "champions",
                    file=discord.File(os.path.join(op, "sports/nhl/catsCup.png")))

    @cats_group.command(name="ilikethepanthers", description="i like the panthers")
    async def ilikethepanthers(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/iLikeThePanthers.png")))

    @cats_group.command(name="pleasecats", description="please florida panthers")
    async def pleasecats(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "please florida panthers")
        await asyncio.sleep(2)
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/stunt.png")))

    @cats_group.command(name="floridapanthers", description="༼ つ ◕◕ ༽つ TAKE MY ENERGY")
    async def floridapanthers(interaction: discord.Interaction):
        await _quick(interaction, "༼ つ ◕◕ ༽つ FLORIDA PANTHERS TAKE MY ENERGY ༼ つ ◕◕ ༽つ")

    @cats_group.command(name="fuckedm", description="FUCK EDM")
    async def fuckedm(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "FUCK EDM")
        await _send(interaction, "🤣🫵")

    @cats_group.command(name="curseedm", description="curse edm")
    async def curseedm(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "༼ つ •̀_•́ ༽つ ቹጋጮዐክፕዐክ ዐጎረቹዪነ ፕልኡቹ ጮሃ ፪ልጋ ፓ፱ፓ፱ ༼ つ •̀_•́ ༽つ")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/curseEdm.mp4")))

    @cats_group.command(name="djkhaled", description="WE THE BEST HOCKEY TEAM")
    async def djkhaled(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "WE THE BEST HOCKEY TEAM!")
        await _send(interaction, "<3",
                    file=discord.File(os.path.join(op, "sports/nhl/djkhaled.png")))

    tree.add_command(cats_group)

    # ─────────────────────────────────────────────────────────────────────────
    # NFL GROUP  /nfl <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    nfl_group = app_commands.Group(name="nfl", description="NFL commands", guild_ids=[guild.id])

    def _build_nfl_embed(df, title, color, footer):
        row = df.iloc[0]
        away, home = row["away_team"], row["home_team"]
        matchup = f"{away} ({row['away_moneyline']}) @ {home} ({row['home_moneyline']})"
        spread  = f"{away}: {row['away_spread_odds']} | {home}: {row['home_spread_odds']}"
        ou      = f"Under: {row['under_odds']} | Over: {row['over_odds']}"
        emb = discord.Embed(title=title.format(**row), description=f"**{matchup}**", color=color)
        emb.add_field(name="📅 When",  value=f"{row['gameday']} at {row['gametime']}", inline=True)
        emb.add_field(name="🏟️ Where", value=row["stadium"], inline=True)
        emb.add_field(name="",         value="", inline=False)
        emb.add_field(name=f"🎲 Betting Spread: {row['spread_line']}", value=spread, inline=True)
        emb.add_field(name=f"Total O/U: {row['total_line']}",          value=ou,     inline=True)
        emb.set_footer(text=footer)
        return emb

    @nfl_group.command(name="nextgame", description="next NFL game + odds")
    async def nfl_nextgame(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nextNFL.R"))
        csv = os.path.join(op, "sports/nfl/nextGame.csv")
        if not os.path.exists(csv):
            await _send(interaction, "couldn't find game info :(", ephemeral=True); return
        emb = _build_nfl_embed(
            pd.read_csv(csv),
            title="🏈 {daysUntil} Days Until Next NFL Game",
            color=0x013369, footer="source: i know ball",
        )
        await _send(interaction, embed=emb)

    @nfl_group.command(name="jags", description="next Jaguars game + odds")
    async def nfl_jags(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nextJagua.R"))
        csv = os.path.join(op, "sports/nfl/nextJags.csv")
        if not os.path.exists(csv):
            await _send(interaction, "couldn't find game info 😔", ephemeral=True); return
        emb = _build_nfl_embed(
            pd.read_csv(csv),
            title="🏈 {daysUntil} Days Until Next Jacksonville Jaguars Game",
            color=0x006778, footer="duval bangem westside jville",
        )
        await _send(interaction, embed=emb)

    @nfl_group.command(name="jagsgame", description="i just wanna party with you <3")
    async def nfl_jagsgame(interaction: discord.Interaction):
        await interaction.response.send_message(
            "i just wanna party with you <3",
            file=discord.File(os.path.join(op, "sports/nfl/jagsParty.mov")))

    @nfl_group.command(name="jacksonvillejaguars", description="༼ つ ◕◕ ༽つ TAKE MY ENERGY")
    async def nfl_jacksonvillejaguars(interaction: discord.Interaction):
        await _quick(interaction, "༼ つ ◕◕ ༽つ JACKSONVILLE JAGUARS TAKE MY ENERGY ༼ つ ◕◕ ༽つ")

    @nfl_group.command(name="jagswin", description="jags win!")
    async def nfl_jagswin(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "how do the jags taste with my balls on your face")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/catnip.mov")))

    @nfl_group.command(name="howboutthemjags", description="how bout them jags")
    async def nfl_howbout(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nfl/howBout.mov")))

    @nfl_group.command(name="wr", description="top WR targets this week")
    async def nfl_wr(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "targetShare.R"))
        await _send(interaction, "here are the top targets",
                    file=discord.File(os.path.join(op, "sports/nfl/tgtShr.png")))

    @nfl_group.command(name="fantasyscoreboard", description="fantasy football scoreboard")
    async def nfl_fsb(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "dynProj.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "scorekeepe.R"))
        await _send(interaction, "ball don't lie",
                    file=discord.File(os.path.join(op, "sports/nfl/fantasyScoreboard.png")))

    @nfl_group.command(name="epamap", description="mean EPA map by team")
    async def nfl_epamap(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "epaMap.R"))
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/epaMap.png")))

    @nfl_group.command(name="room40points", description="Room 40 fantasy points map")
    async def nfl_room40points(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "room40map.R"))
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/room40map.png")))

    @nfl_group.command(name="amonra", description="I RUN THIS SHIT")
    async def nfl_amonra(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "I RUN THIS SHIT")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/amonRa.mov")))

    tree.add_command(nfl_group)

    # ─────────────────────────────────────────────────────────────────────────
    # NBA GROUP  /nba <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    nba_group = app_commands.Group(name="nba", description="NBA commands", guild_ids=[guild.id])

    @nba_group.command(name="scoreboard", description="live NBA scores")
    async def nba_scoreboard(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaLiveScore.R"))
        csv = os.path.join(op, "sports/nba/liveScoreboard.csv")
        if not os.path.exists(csv):
            await _send(interaction, "😢 Couldn't find live NBA scores.", ephemeral=True); return
        df = pd.read_csv(csv)
        for _, group in df.groupby("game_id"):
            teams = group.sort_values("team_name")
            t1, t2 = teams.iloc[0], teams.iloc[1]
            emb = discord.Embed(
                title=f"🏀 {t1['team_name']} vs {t2['team_name']}", color=0x5865F2)
            emb.add_field(name=t1["TEAM_ABBREVIATION"], value=str(t1["PTS"]),              inline=True)
            emb.add_field(name=t2["TEAM_ABBREVIATION"], value=str(t2["PTS"]),              inline=True)
            emb.add_field(name="Game Status",           value=t1["game_status_text"].strip(), inline=False)
            await _send(interaction, embed=emb)

    @nba_group.command(name="today", description="NBA games today")
    async def nba_today(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaToday.R"))
        csv = os.path.join(op, "sports/nba/gamesToday.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hoops today :("); return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏀 Today's NBA Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    @nba_group.command(name="tomorrow", description="NBA games tomorrow")
    async def nba_tomorrow(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaTomorrow.R"))
        csv = os.path.join(op, "sports/nba/gamesTomorrow.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hoops tomorrow :("); return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏀 Tomorrow's NBA Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    tree.add_command(nba_group)

    # ─────────────────────────────────────────────────────────────────────────
    # MARKETS GROUP  /markets <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    markets_group = app_commands.Group(name="markets", description="markets / macro charts", guild_ids=[guild.id])

    @markets_group.command(name="fedrate", description="Federal funds target rate")
    async def fedrate(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "fedTarget.R"))
        await _send(interaction, "here's the federal funds target rate:",
                    file=discord.File(os.path.join(op, "markets/dfedtaru.png")))

    @markets_group.command(name="yieldcurve", description="current yield curve")
    async def yieldcurve(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "yieldCurve.R"))
        await _send(interaction, "this is what the yield curve looks like right now:",
                    file=discord.File(os.path.join(op, "markets/yield_curve.png")))

    @markets_group.command(name="yieldspread", description="historical yield spreads")
    async def yieldspread(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "yieldSpreade.R"))
        await _send(interaction, "historical yield spreads:",
                    file=discord.File(os.path.join(op, "markets/yield_spread.png")))

    @markets_group.command(name="yieldspreadshort", description="last 2 months of yield spreads")
    async def yieldspreadshort(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "yieldSpreadShort.R"))
        await _send(interaction, "last 2 months of yield spreads:",
                    file=discord.File(os.path.join(op, "markets/yield_spread_2mo.png")))

    tree.add_command(markets_group)

    # ─────────────────────────────────────────────────────────────────────────
    # SPACE GROUP  /space <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    space_group = app_commands.Group(name="space", description="space / launch commands", guild_ids=[guild.id])

    @space_group.command(name="nextlaunch", description="next KSC rocket launch")
    async def nextlaunch(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "spaceLaunches.py"))
        csv = os.path.join(op, "space/next_launch.csv")
        if not os.path.exists(csv):
            await _send(interaction, "🚫 Couldn't find launch data.", ephemeral=True); return
        row = pd.read_csv(csv).iloc[0]
        mission_desc = str(row["Mission"])
        if len(mission_desc) > 1000:
            mission_desc = mission_desc[:997] + "..."
        emb = discord.Embed(
            title=f"⏳ **T - {row['T-minus']}**",
            description="Next Launch from Kennedy Space Center",
            color=0x5865F2,
        )
        emb.add_field(name="🚀 Mission",            value=f"{row['Name']}\n{mission_desc}", inline=False)
        emb.add_field(name="📋 Status",              value=row["Status"],                   inline=True)
        emb.add_field(name="🗓️ Launch Window Opens", value=row["Window (ET)"],              inline=True)
        emb.add_field(name="🏢 Agency",              value=row["Provider"],                 inline=True)
        emb.add_field(name="📍 Launch Pad",          value=row["Pad"],                      inline=False)
        if isinstance(row["Image"], str) and row["Image"].startswith("http"):
            emb.set_image(url=row["Image"])
        await _send(interaction, embed=emb)

    tree.add_command(space_group)

    # ── /jaxplanes ────────────────────────────────────────────────────────────
    @tree.command(name="jaxplanes", description="planes over Jacksonville right now", guild=guild)
    async def jaxplanes(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "python3", os.path.join(pp, "overJax.py"))
        await _send(interaction, "here you go:",
                    file=discord.File(os.path.join(op, "aerospace/adsb250nm_map.html")))
