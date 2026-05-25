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
from datetime import datetime, timedelta

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
    PYTHON = os.path.expanduser("~/discordBot/venv/bin/python3")

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
        "chuck":   [("!kingcap", 0), ("🤣🫵", 1)],
        "fish":    [("fuck you fish", 0), ("🤣🫵", 1)],
        "bryce":   [("no", 0), ("squidly rulez", 1)],
        "weston":  [("no we play poker together", 0), ("we love weston", 1)],
        "james":   [
            ("which one bro there are several james in here", 0),
            ("you know what", 1), ("fuck you james", 1), ("fuck you too james", 1),
        ],
        "cissel":  [("fuck you james", 0), ("🤣🫵", 1)],
        "peyton":  [("fuck you p", 0), ("🤣🫵", 1)],
        "eli":     [("silky johnson player hater of the year 2025", 0)],
        "vapedad": [("hell nah", 0), ("we did hard time together", 1)],
        "verv":    [("i'll swiss cheese ur ass right here twin", 0), ("JIT", 1)],
        "tyjo":    [("fuck you tyler", 0), ("get your tall strong handsome ass outta here smh", 1)],
    }

    @tree.command(name="bully", description="bully someone", guild=guild)
    @app_commands.describe(target="fuck this person in particular")
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
    surf_group     = app_commands.Group(name="surf",    description="surf & ocean conditions", guild_ids=[guild.id])

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

    @surf_group.command(name="surf", description="surf forecast")
    async def surf(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "surf4castPlot.R"))
        await _send(interaction, "here's the latest surf forecast:",
                    file=discord.File(os.path.join(op, "weather/surf_fcst.png")))

    @surf_group.command(name="wavemap", description="wave forecast map (slow)")
    async def wavemap(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "surfMap.R"))
        await _send(interaction, "here's the latest forecast map:",
                    file=discord.File(os.path.join(op, "weather/wave_animation.gif")))

    @surf_group.command(name="windmap", description="wind forecast map (slow)")
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

    @surf_group.command(name="buoywaves", description="NOAA buoy wave plot")
    async def buoywaves(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "noaaBuoy.R"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "buoyWavePlot.R"))
        await _send(interaction, "latest observations from NOAA buoy #41117:",
                    file=discord.File(os.path.join(op, "weather/buoyWaves.png")))

    @surf_group.command(name="tideplot", description="Mayport tide plot")
    async def tideplot(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "tidePlot.R"))
        await _send(interaction, "current tides:",
                    file=discord.File(os.path.join(op, "weather/mayportTides.png")))

    @surf_group.command(name="windplot", description="Mayport wind plot")
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
    tree.add_command(surf_group)

    # ─────────────────────────────────────────────────────────────────────────
    # HISTORY GROUP  /history <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    history_group = app_commands.Group(name="history", description="server history charts", guild_ids=[guild.id])

    @history_group.command(name="server", description="total server message history")
    async def hist_server(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "serverHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/allMessages.png")))

    @history_group.command(name="channel", description="channel message history")
    async def hist_channel(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "channelHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/channelMessages.png")))

    @history_group.command(name="user", description="user message history")
    async def hist_user(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "userHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/userMessages.png")))

    @history_group.command(name="daily", description="daily messages plot")
    async def hist_daily(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "channelReader.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "dailyMessage.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/dailyMessages.png")))

    tree.add_command(history_group)

    # ─────────────────────────────────────────────────────────────────────────
    # CATS GROUP  /cats <subcommand>  (games, schedule, reactions only)
    # Player image commands dropped for now — add back once bot is stable
    # ─────────────────────────────────────────────────────────────────────────
    cats_group        = app_commands.Group(name="cats",        description="Florida Panthers",              guild_ids=[guild.id])
    rats_group = app_commands.Group(name="rats", description="Florida Panthers players", guild_ids=[guild.id])


    # ─────────────────────────────────────────────────────────────────────────
    # CATSGAMES GROUP  /catsgames <subcommand>  (schedule, scores, reactions)
    # ─────────────────────────────────────────────────────────────────────────

    @cats_group.command(name="next", description="next Panthers game")
    async def cats_next(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "nextCats.py"))
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

    @cats_group.command(name="takemyenergy", description="༼ つ ◕◕ ༽つ TAKE MY ENERGY")
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

    # ── player images (batch 1 — fills /cats to 24) ──────────────────────────
    @rats_group.command(name="barkov", description="Barkov :)")
    async def barkov(interaction: discord.Interaction):
        await interaction.response.send_message(
            ":)", file=discord.File(os.path.join(op, "sports/nhl/barky.png")))

    @rats_group.command(name="bobby", description="BRICK WALL BOB")
    async def bobby(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BRICK WALL BOB")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/brickwallbob.jpg")))

    @rats_group.command(name="thuggybobby", description="iced out bobby")
    async def thuggybobby(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/bobbyChain.png")))

    @rats_group.command(name="praisebobby", description="bobby bless")
    async def praisebobby(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "bobby bless")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/stbobby.jpeg")))

    @rats_group.command(name="chucky", description="Tkachuk")
    async def chucky(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/chucky.jpeg")))

    @rats_group.command(name="reino", description="i love you sam <3")
    async def reino(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "i love you sam <3")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/reino.png")))

    @rats_group.command(name="ekblad", description="BOOSTED EKKY")
    async def ekblad(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BOOSTED EKKY")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/ekblad.jpeg")))

    @rats_group.command(name="swaggy", description="NEVER FORGET")
    async def swaggy(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "NEVER FORGET")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/buttpuck.mov")))

    @rats_group.command(name="marchand", description="ALL HAIL THE RAT KING")
    async def marchand(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "ALL HAIL THE RAT KING")
        await _send(interaction, "PANTHERS LEGEND AND FUTURE HALL OF FAMER BRADLEY MARCHAND",
                    file=discord.File(os.path.join(op, "sports/nhl/marchand.png")))

    @rats_group.command(name="drunkmarchand", description="BRAD MF MARCHAND")
    async def drunkmarchand(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "BRAD MF MARCHAND")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/drunkMarchand.jpg")))

    @rats_group.command(name="benny", description="Sam Bennett")
    async def benny(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/benny.jpeg")))

    @rats_group.command(name="eetu", description="Luostarinen")
    async def eetu(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/eetu.png")))

    @rats_group.command(name="gaddy", description="HEY SIRI PLAY SICKO MODE")
    async def gaddy(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "HEY SIRI PLAY SICKO MODE")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/gadjo.jpg")))

    @rats_group.command(name="sethjones", description="SETH MF JONES")
    async def sethjones(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "SETH MF JONES")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/sethjones.jpeg")))

    tree.add_command(cats_group)

    # ─────────────────────────────────────────────────────────────────────────
    # CATSPLAYERS GROUP  /catsplayers <subcommand>  (overflow player images)
    # ─────────────────────────────────────────────────────────────────────────

    @rats_group.command(name="lundy", description="lundy a mf shooter fr")
    async def lundy(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "lundy a mf shooter fr")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/lundell.png")))

    @rats_group.command(name="forsling", description="Forsling")
    async def forsling(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/forsling.png")))

    @rats_group.command(name="jesper", description="average jesper boqvist moment")
    async def jesper(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "average jesper boqvist moment")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nhl/jesper.png")))

    @rats_group.command(name="schmidty", description="NATE THE GREAT")
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

    tree.add_command(rats_group)


    # ─────────────────────────────────────────────────────────────────────────
    # NHL GROUP  /nhl <subcommand>  (league-wide schedule)
    # ─────────────────────────────────────────────────────────────────────────
    nhl_group = app_commands.Group(name="nhl", description="NHL schedule & scores", guild_ids=[guild.id])

    @nhl_group.command(name="today", description="NHL games today")
    async def nhl_today(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "nhlToday.py"))
        csv = os.path.join(op, "sports/nhl/gamesToday.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hockey today :(")
            return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏒 Today's NHL Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    @nhl_group.command(name="tomorrow", description="NHL games tomorrow")
    async def nhl_tomorrow(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "nhlTomorrow.py"))
        csv = os.path.join(op, "sports/nhl/gamesTomorrow.csv")
        if not os.path.exists(csv):
            await _send(interaction, "no hockey tomorrow :(")
            return
        df  = pd.read_csv(csv)
        emb = discord.Embed(title="🏒 Tomorrow's NHL Matchups", color=0x3498db)
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        await _send(interaction, embed=emb)

    tree.add_command(nhl_group)


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

    @nfl_group.command(name="nextgame", description="next NFL game")
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

    @nfl_group.command(name="wr", description="top WR targets this szn")
    async def nfl_wr(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "targetShare.R"))
        await _send(interaction, "here are the top targets",
                    file=discord.File(os.path.join(op, "sports/nfl/tgtShr.png")))

    @nfl_group.command(name="fantasyscoreboard", description="fantasy football scoreboard")
    async def nfl_fsb(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "dynProj.py"))
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "scorekeepe.R"))
        await _send(interaction, "ball don't lie",
                    file=discord.File(os.path.join(op, "sports/nfl/fantasyScoreboard.png")))

    @nfl_group.command(name="epamap", description="mean EPA map by team")
    async def nfl_epamap(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "epaMap.R"))
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/epaMap.png")))

    @nfl_group.command(name="room40points", description="room 40 fantasy points map")
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
    # JAGS GROUP  /jags <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    jags_group = app_commands.Group(name="jags", description="Jacksonville Jaguars", guild_ids=[guild.id])

    @jags_group.command(name="next", description="next Jaguars game")
    async def jags_next(interaction: discord.Interaction):
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

    @jags_group.command(name="today", description="i just wanna party with you <3")
    async def jags_today(interaction: discord.Interaction):
        await interaction.response.send_message(
            "i just wanna party with you <3",
            file=discord.File(os.path.join(op, "sports/nfl/jagsParty.mov")))

    @jags_group.command(name="takemyenergy", description="༼ つ ◕◕ ༽つ TAKE MY ENERGY")
    async def jags_energy(interaction: discord.Interaction):
        await _quick(interaction, "༼ つ ◕◕ ༽つ JACKSONVILLE JAGUARS TAKE MY ENERGY ༼ つ ◕◕ ༽つ")

    @jags_group.command(name="win", description="jags win!")
    async def jags_win(interaction: discord.Interaction):
        await _defer(interaction)
        await _send(interaction, "how do the jags taste with my balls on your face")
        await _send(interaction, file=discord.File(os.path.join(op, "sports/nfl/catnip.mov")))

    @jags_group.command(name="howbout", description="how bout them jags")
    async def jags_howbout(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nfl/howBout.mov")))

    tree.add_command(jags_group)

    # ─────────────────────────────────────────────────────────────────────────
    # NBA GROUP  /nba <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    nba_group = app_commands.Group(name="nba", description="NBA commands", guild_ids=[guild.id])

    @nba_group.command(name="today", description="NBA games today")
    async def nba_today(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.gather(
            asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaToday.R")),
            asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaLiveScore.R")),
        )
        csv_today = os.path.join(op, "sports/nba/gamesToday.csv")
        if not os.path.exists(csv_today):
            await _send(interaction, "no hoops today :("); return
        today_df = pd.read_csv(csv_today)
        live_scores = {}
        csv_live = os.path.join(op, "sports/nba/liveScoreboard.csv")
        if os.path.exists(csv_live):
            live_df = pd.read_csv(csv_live)
            live_df = live_df[live_df["game_status"] == 2]
            for _, row in live_df.iterrows():
                abbr = str(row["TEAM_ABBREVIATION"]).strip()
                live_scores[abbr] = {
                    "pts":    int(row["PTS"]) if pd.notna(row["PTS"]) else 0,
                    "status": str(row["game_status_text"]).strip(),
                }
        emb = discord.Embed(title="🏀 Today's NBA Games", color=0x3498db)
        for _, row in today_df.iterrows():
            matchup  = str(row["matchup"])
            time_val = str(row["time"])
            parts    = [p.strip() for p in matchup.replace("@", "vs").split("vs")]
            value    = time_val
            if len(parts) == 2:
                a1, a2 = parts[0], parts[1]
                if a1 in live_scores and a2 in live_scores:
                    s1, s2 = live_scores[a1], live_scores[a2]
                    value  = f"🔴 LIVE  {a1} **{s1['pts']}** — **{s2['pts']}** {a2}  *{s1['status']}*"
            emb.add_field(name=matchup, value=value, inline=False)
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
    # MAGIC GROUP  /magic <subcommand>
    # ─────────────────────────────────────────────────────────────────────────   
    magic_group = app_commands.Group(name="magic", description="Orlando Magic", guild_ids=[guild.id])

    @magic_group.command(name="takemyenergy", description="༼ つ ◕◕ ༽つ TAKE MY ENERGY")
    async def magic_energy(interaction: discord.Interaction):
        await _quick(interaction, "༼ つ ◕◕ ༽つ ORLANDO MAGIC TAKE MY ENERGY ༼ つ ◕◕ ༽つ")

    tree.add_command(magic_group)

    # ─────────────────────────────────────────────────────────────────────────
    # GOLF GROUP  /golf <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    golf_group = app_commands.Group(name="golf", description="PGA Tour golf", guild_ids=[guild.id])

    def _build_golf_embeds():
        subprocess.run(["PYTHON", os.path.join(pp, "pgaLeaderboard.py")],
                    check=False, timeout=30)
        tourn_csv = os.path.join(op, "sports/pga/tournament.csv")
        lb_csv    = os.path.join(op, "sports/pga/leaderboard.csv")
        if not os.path.exists(tourn_csv) or not os.path.exists(lb_csv):
            return None

        t  = pd.read_csv(tourn_csv).iloc[0]
        lb = pd.read_csv(lb_csv)
        if lb.empty or not t.get("name"):
            return None

        # build description safely — skip any nan fields
        detail = t.get("detail") or t.get("status") or ""
        course = t.get("course", "")
        city   = t.get("city", "")
        state  = t.get("state", "")
        location_parts = [x for x in [course, city, state]
                        if x and str(x).strip() and str(x).strip().lower() != "nan"]
        location_line = "  ·  ".join(location_parts)
        description = f"{location_line}\n*{detail}*" if location_line else f"*{detail}*"

        emb = discord.Embed(
            title=f"⛳ {t['name']}",
            description=description.strip(),
            color=0x2E7D32,
        )

        for i, (_, row) in enumerate(lb.iterrows(), start=1):
            pos   = str(row.get("position", "")).strip()
            name  = str(row.get("name", "–"))
            score = str(row.get("score", "–"))
            today = str(row.get("today", "")).strip()
            thru  = str(row.get("thru", "")).strip()

            # build value line — only show today/thru if they're real values
            parts = [f"**{score}**"]
            if today and today not in ("–", "nan", ""):
                parts.append(f"today: {today}")
            if thru and thru not in ("–", "nan", ""):
                parts.append(f"thru {thru}")

            # use rank number if position is missing
            label = f"{pos} {name}" if pos and pos not in ("–", "nan") else f"{i}. {name}"

            emb.add_field(name=label, value="  ".join(parts), inline=False)

        return emb

    @golf_group.command(name="pga", description="live PGA Tour leaderboard")
    async def golf_leaderboard(interaction: discord.Interaction):
        await _defer(interaction)
        emb = await asyncio.to_thread(_build_golf_embeds)
        if emb is None:
            await _send(interaction, "⛳ no PGA tournament in progress right now"); return
        await _send(interaction, embed=emb)

    tree.add_command(golf_group)

    # ─────────────────────────────────────────────────────────────────────────
    # /ball  —  all sports today in one message
    # ─────────────────────────────────────────────────────────────────────────
    @tree.command(name="ball", description="sports happening today", guild=guild)
    async def ball(interaction: discord.Interaction):
        await _defer(interaction)

        # Run all schedule/leaderboard scripts concurrently
        await asyncio.gather(
            asyncio.to_thread(_run, PYTHON,  os.path.join(pp, "nhlToday.py")),
            asyncio.to_thread(_run, PYTHON,  os.path.join(pp, "mlbToday.py")),
            asyncio.to_thread(_run, PYTHON,  os.path.join(pp, "pgaLeaderboard.py")),
            asyncio.to_thread(_run, "Rscript",  os.path.join(rp, "nbaToday.R")),
            asyncio.to_thread(_run, "Rscript",  os.path.join(rp, "nbaLiveScore.R")),
            asyncio.to_thread(_run, "Rscript",  os.path.join(rp, "nextNFL.R")),
        )

        embeds = []

        # ── NBA ──────────────────────────────────────────────────────────────
        nba_csv  = os.path.join(op, "sports/nba/gamesToday.csv")
        live_csv = os.path.join(op, "sports/nba/liveScoreboard.csv")
        if os.path.exists(nba_csv):
            nba_df = pd.read_csv(nba_csv)
            if not nba_df.empty:
                live_scores = {}
                if os.path.exists(live_csv):
                    live_df = pd.read_csv(live_csv)
                    live_df = live_df[live_df["game_status"] == 2]
                    for _, r in live_df.iterrows():
                        abbr = str(r["TEAM_ABBREVIATION"]).strip()
                        live_scores[abbr] = {
                            "pts":    int(r["PTS"]) if pd.notna(r["PTS"]) else 0,
                            "status": str(r["game_status_text"]).strip(),
                        }
                emb = discord.Embed(title="🏀 NBA", color=0xC9082A)
                for _, row in nba_df.iterrows():
                    matchup  = str(row["matchup"])
                    time_val = str(row["time"])
                    parts    = [p.strip() for p in matchup.replace("@", "vs").split("vs")]
                    value    = time_val
                    if len(parts) == 2:
                        a1, a2 = parts[0], parts[1]
                        if a1 in live_scores and a2 in live_scores:
                            s1, s2 = live_scores[a1], live_scores[a2]
                            value  = f"🔴 **{s1['pts']}–{s2['pts']}**  *{s1['status']}*"
                    emb.add_field(name=matchup, value=value, inline=False)
                embeds.append(emb)

        # ── NHL ──────────────────────────────────────────────────────────────
        nhl_csv = os.path.join(op, "sports/nhl/gamesToday.csv")
        if os.path.exists(nhl_csv):
            nhl_df = pd.read_csv(nhl_csv)
            if not nhl_df.empty:
                emb = discord.Embed(title="🏒 NHL", color=0x000000)
                for _, row in nhl_df.iterrows():
                    emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
                embeds.append(emb)

        # ── MLB ──────────────────────────────────────────────────────────────
        mlb_csv = os.path.join(op, "sports/mlb/gamesToday.csv")
        if os.path.exists(mlb_csv):
            mlb_df = pd.read_csv(mlb_csv)
            if not mlb_df.empty:
                emb = discord.Embed(title="⚾ MLB", color=0x002D72)
                for _, row in mlb_df.iterrows():
                    emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
                embeds.append(emb)

        # ── PGA ──────────────────────────────────────────────────────────────
        tourn_csv = os.path.join(op, "sports/pga/tournament.csv")
        lb_csv    = os.path.join(op, "sports/pga/leaderboard.csv")
        if os.path.exists(tourn_csv) and os.path.exists(lb_csv):
            t  = pd.read_csv(tourn_csv).iloc[0]
            lb = pd.read_csv(lb_csv)
            if not lb.empty and t.get("name"):
                status_line = t.get("detail") or t.get("status") or ""
                emb = discord.Embed(
                    title=f"⛳ {t['name']}",
                    description=f"*{status_line}*  ·  Top 5",
                    color=0x2E7D32,
                )
                for _, row in lb.head(5).iterrows():
                    pos   = str(row.get("position", "–"))
                    name  = str(row.get("name", "–"))
                    score = str(row.get("score", "–"))
                    today = str(row.get("today", "–"))
                    emb.add_field(
                        name=f"{pos}. {name}",
                        value=f"**{score}**  today: {today}",
                        inline=True,
                    )
                embeds.append(emb)

        # ── NFL (today only) ─────────────────────────────────────────────────
        nfl_csv = os.path.join(op, "sports/nfl/nextGame.csv")
        if os.path.exists(nfl_csv):
            nfl_df = pd.read_csv(nfl_csv)
            if not nfl_df.empty:
                row = nfl_df.iloc[0]
                try:
                    days_until = int(row["daysUntil"])
                except (ValueError, KeyError):
                    days_until = 99
                if days_until == 0:
                    away, home = row["away_team"], row["home_team"]
                    emb = discord.Embed(title="🏈 NFL", color=0x013369)
                    emb.add_field(
                        name=f"{away} @ {home}",
                        value=f"{row['gametime']}  ·  {row['stadium']}",
                        inline=False,
                    )
                    embeds.append(emb)

        if not embeds:
            await _send(interaction, "nothing going on in the sports world today 😔")
            return

        await _send(interaction, embeds=embeds)

    # ─────────────────────────────────────────────────────────────────────────
    # OSRS GROUP  /osrs <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    OSRS_SKILLS = [
        "total",
        "attack", "defence", "strength", "hitpoints", "ranged",
        "prayer", "magic", "cooking", "woodcutting", "fletching",
        "fishing", "firemaking", "crafting", "smithing", "mining",
        "herblore", "agility", "thieving", "slayer", "farming",
        "runecraft", "hunter", "construction", "sailing",
    ]
    OSRS_EMOJI = {
        "total": "📊", "attack": "🗡️", "defence": "🛡️", "strength": "💪",
        "hitpoints": "❤️", "ranged": "🏹", "prayer": "🙏", "magic": "🔮",
        "cooking": "🍳", "woodcutting": "🪓", "fletching": "🪶", "fishing": "🎣",
        "firemaking": "🔥", "crafting": "💎", "smithing": "⚒️", "mining": "⛏️",
        "herblore": "🌿", "agility": "🏃", "thieving": "🗝️", "slayer": "💀",
        "farming": "🌾", "runecraft": "🔵", "hunter": "🐾", "construction": "🏠",
        "sailing": "⛵",
    }
    OSRS_LOGO = os.path.join(op, "osrs/osrs.png")

    osrs_group = app_commands.Group(name="osrs", description="Old School RuneScape hiscores", guild_ids=[guild.id])

    async def osrs_skill_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=s, value=s)
            for s in OSRS_SKILLS if current.lower() in s.lower()
        ][:25]

    @osrs_group.command(name="hiscores", description="crew hiscores leaderboard — total level or any skill")
    @app_commands.describe(skill="skill to look up (default: total)")
    @app_commands.autocomplete(skill=osrs_skill_autocomplete)
    async def osrs_hiscores(interaction: discord.Interaction, skill: str = "total"):
        await _defer(interaction)
        skill = skill.lower().strip()
        if skill not in OSRS_SKILLS:
            await _send(interaction, f"❓ `{skill}` isn't a valid OSRS skill. Try: total, attack, magic, slayer…", ephemeral=True)
            return

        await asyncio.to_thread(
            lambda: subprocess.run(
                ["PYTHON", os.path.join(pp, "osrsHiscores.py"), skill],
                check=False, timeout=60
            )
        )

        csv_path = os.path.join(op, "osrs/hiscores.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "😔 couldn't fetch hiscores right now", ephemeral=True)
            return

        df = pd.read_csv(csv_path)
        emoji   = OSRS_EMOJI.get(skill, "📊")
        max_lvl = 99 if skill != "total" else 2376
        title   = f"{emoji} OSRS {'Total Level' if skill == 'total' else skill.title()}"
        emb     = discord.Embed(title=title, color=0x8B4513)
        medals  = ["🥇", "🥈", "🥉"]

        for i, (_, row) in enumerate(df.iterrows()):
            lvl  = int(row["level"])
            rank = str(row["rank"])
            xp   = int(row["xp"])
            prefix     = medals[i] if i < 3 else f"`{i+1}.`"
            pct        = min(lvl / max_lvl, 1.0)
            bar_filled = round(pct * 10)
            bar        = "█" * bar_filled + "░" * (10 - bar_filled)
            if skill == "total":
                value = f"**{lvl:,}** / {max_lvl:,}  `{bar}`"
            else:
                value = f"**{lvl}** / {max_lvl}  `{bar}`\nXP: {xp:,}  ·  Rank: {rank}"
            emb.add_field(name=f"{prefix} {row['player']}", value=value, inline=False)

        emb.set_footer(text="source: OSRS hiscores  ·  sorted by level")

        if os.path.exists(OSRS_LOGO):
            emb.set_thumbnail(url="attachment://osrs.png")
            await _send(interaction, embed=emb, file=discord.File(OSRS_LOGO, filename="osrs.png"))
        else:
            await _send(interaction, embed=emb)

    # ── OSRS_PLAYERS for autocomplete on /osrs lvl ────────────────────────────
    OSRS_PLAYERS = [
        "captdeadhead", "SubieVapeski", "Pexci", "swampdog",
        "Squidlies", "Wmwhite", "TrimIsLife", "Fart Johnsun",
    ]

    async def osrs_player_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in OSRS_PLAYERS if current.lower() in p.lower()
        ][:25]

    @osrs_group.command(name="lvl", description="full skill sheet for a player")
    @app_commands.describe(player="RSN to look up (defaults to cissel if not given)")
    @app_commands.autocomplete(player=osrs_player_autocomplete)
    async def osrs_lvl(interaction: discord.Interaction, player: str = ""):
        await _defer(interaction)

        # if no player given, fall back to first crew member as a safe default
        rsn = player.strip() if player.strip() else OSRS_PLAYERS[0]

        import urllib.request, urllib.parse, urllib.error

        SKILLS_ORDER = [
            "total",
            "attack", "defence", "strength", "hitpoints", "ranged",
            "prayer", "magic", "cooking", "woodcutting", "fletching",
            "fishing", "firemaking", "crafting", "smithing", "mining",
            "herblore", "agility", "thieving", "slayer", "farming",
            "runecraft", "hunter", "construction", "sailing",
        ]

        def _fetch_all(rsn: str):
            url = f"https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws?player={urllib.parse.quote(rsn)}"
            req = urllib.request.Request(url, headers={"User-Agent": "discordBot/1.0 personal project"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    text = r.read().decode("utf-8")
                rows = text.strip().splitlines()
                result = {}
                for i, skill in enumerate(SKILLS_ORDER):
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
                print(f"[osrs lvl] HTTP {e.code} for player '{rsn}'")
                return None
            except Exception as e:
                print(f"[osrs lvl] fetch error for '{rsn}': {e}")
                return None

        data = await asyncio.to_thread(_fetch_all, rsn)

        if data is None:
            await _send(interaction, f"😔 couldn't find `{rsn}` on the hiscores — are they ranked?", ephemeral=True)
            return

        print(f"[osrs lvl] got {len(data)} skills for {rsn}")

        # ── build embed ───────────────────────────────────────────────────────
        total   = data.get("total", {})
        tot_lvl = total.get("level", 0)
        tot_xp  = total.get("xp", 0)

        emb = discord.Embed(
            title=f"📜 {rsn}",
            description=f"**Total Level:** {tot_lvl:,}   |   **Total XP:** {tot_xp:,}",
            color=0x8B4513,
        )

        # 24 skills in in-game order (3-column layout in Discord)
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

        for skill in ALL_SKILLS:
            d = data.get(skill)
            emoji = OSRS_EMOJI.get(skill, "?")
            if d:
                lvl = d["level"]
                xp  = d["xp"]
                bar_filled = round(min(lvl / 99, 1.0) * 6)
                bar = "█" * bar_filled + "░" * (6 - bar_filled)
                val = f"`{bar}`\n**{lvl}** / 99\n{xp:,} xp"
            else:
                val = "unranked"
            emb.add_field(name=f"{emoji} {skill.title()}", value=val, inline=True)

        # 25th field: total level
        rank_str = f"#{total['rank']:,}" if total.get("rank") else "unranked"
        emb.add_field(
            name=f"📊 Total Level",
            value=f"**{tot_lvl:,}** / 2,376\n{tot_xp:,} xp\nRank: {rank_str}",
            inline=True,
        )

        emb.set_footer(text="source: OSRS hiscores")

        if os.path.exists(OSRS_LOGO):
            emb.set_thumbnail(url="attachment://osrs.png")
            await _send(interaction, embed=emb, file=discord.File(OSRS_LOGO, filename="osrs.png"))
        else:
            await _send(interaction, embed=emb)

    tree.add_command(osrs_group)

    # ─────────────────────────────────────────────────────────────────────────
    # JAXCAMS GROUP  /jaxcams
    # ─────────────────────────────────────────────────────────────────────────
    JAX_CAM_GROUPS = [
        "i95", "i10", "i295", "downtown", "beaches", "bridges",
        "northside", "southside", "westside", "random",
    ]

    async def jaxcams_autocomplete(interaction: discord.Interaction, current: str):
        descs = {
            "i95":       "I-95 corridor cams",
            "i10":       "I-10 corridor cams",
            "i295":      "I-295 beltway cams",
            "downtown":  "Downtown / I-10 & I-95 interchange",
            "beaches":   "JTB / Atlantic Blvd / beach routes",
            "bridges":   "Dames Point Bridge cams",
            "northside": "Northside cams",
            "southside": "Southside cams",
            "westside":  "Westside / Blanding cams",
            "random":    "Random cam from anywhere in JAX",
        }
        return [
            app_commands.Choice(name=f"{g} — {descs.get(g, '')}", value=g)
            for g in JAX_CAM_GROUPS if current.lower() in g
        ][:25]

    @tree.command(name="jaxcams", description="Live JAX traffic cams from FL511", guild=guild)
    @app_commands.describe(group="Which cameras to show (default: random)")
    @app_commands.autocomplete(group=jaxcams_autocomplete)
    async def jaxcams(interaction: discord.Interaction, group: str = "random"):
        await _defer(interaction)

        await asyncio.to_thread(
            lambda: subprocess.run(
                ["PYTHON", os.path.join(pp, "jaxS.py"), group, "1"],
                check=False, timeout=30
            )
        )

        csv_path = os.path.join(op, "cams/cams.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "😔 couldn't reach FL511 right now", ephemeral=True)
            return

        df = pd.read_csv(csv_path)
        good = df[df["ok"] == True]

        if good.empty:
            await _send(interaction, "📷 No cameras returned images — FL511 may be down", ephemeral=True)
            return

        row = good.iloc[0]
        group_label = group.upper() if group not in ("random", "downtown", "beaches", "bridges", "jtb") else group.upper()
        emb = discord.Embed(
            title=f"🎥 JAX Traffic Cams — {group_label}",
            description=f"**{row['name']}**  ·  {pd.Timestamp.now().strftime('%I:%M %p')}",
            color=0x005F9E,
        )
        emb.set_image(url="attachment://cam.jpg")
        emb.set_footer(text="source: FL511 / FDOT  ·  refreshes on each call")

        await _send(interaction, embed=emb, file=discord.File(row["path"], filename="cam.jpg"))

    # ─────────────────────────────────────────────────────────────────────────
    # MARKETS GROUP  /markets <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    markets_group = app_commands.Group(name="markets", description="markets / macro charts", guild_ids=[guild.id])

    @markets_group.command(name="fedrate", description="federal funds target rate")
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
        
    @markets_group.command(name="crudeoil", description="west texas intermediate - cushing, oklahoma")
    async def crudeoil(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "crude.R"))
        await _send(interaction, "full price history for crude oil wti @ cushing, oklahoma:",
                    file=discord.File(os.path.join(op, "markets/crudewti.png")))
        
    @markets_group.command(name="chart", description="return stock price chart")
    @app_commands.describe(ticker="ticker symbol (e.g. SPY, AAPL)")
    async def chart(interaction: discord.Interaction, ticker: str):
        
        # Sanitize input
        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 10:
            await interaction.response.send_message("Invalid ticker symbol.", ephemeral=True)
            return
        
        # Defer the response since Rscript will take a second
        await interaction.response.defer()
        
        # Run the R script with the ticker as an argument
        result = subprocess.run(
            ["Rscript", "/home/jhcv/discordBot/r/stockChart.R", ticker],
            capture_output=True,
            text=True
        )
        
        # Check if R script ran successfully
        if result.returncode != 0:
            await interaction.followup.send(f"Error generating chart for **${ticker}**.")
            print(result.stderr)  # log the R error to console for debugging
            return
        
        # Send the chart image
        output_path = f"/home/jhcv/discordBot/outputs/markets/stockchart.png"
        
        if os.path.exists(output_path):
            await interaction.followup.send(
                content=f"**${ticker}**",
                file=discord.File(output_path)
            )
        else:
            await interaction.followup.send("Chart file not found.")

    TIMEFRAME_CHOICES = [
    app_commands.Choice(name="Recent — last hour of trading",         value="recent"),
    app_commands.Choice(name="Close — last hour of market (3-4 PM)",  value="close"),
    app_commands.Choice(name="Open — first hour of market (9:30-10:30 AM)", value="open"),
    app_commands.Choice(name="Day — regular session (9:30 AM - 4 PM)", value="day"),
    app_commands.Choice(name="Full — pre-market through after-hours",  value="full"),
]

    @markets_group.command(name="trades", description="Return trade level chart for a given timeframe")
    @app_commands.describe(ticker="ticker symbol (e.g. SPY, AAPL)", timeframe="time window to display")
    @app_commands.choices(timeframe=TIMEFRAME_CHOICES)
    async def chart(interaction: discord.Interaction, ticker: str, timeframe: app_commands.Choice[str]):

        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 10:
            await interaction.response.send_message("Invalid ticker symbol.", ephemeral=True)
            return

        await interaction.response.defer()

        # Step 1: Pull trade data
        pull_result = subprocess.run(
            [PYTHON, os.path.join(pp, "marketTrades.py"), ticker, timeframe.value],
            capture_output=True,
            text=True
        )

        if pull_result.returncode != 0:
            await interaction.followup.send(f"Error pulling trade data for **${ticker}**.\n```{pull_result.stderr[-1500:]}```")
            print("PULL ERROR:", pull_result.stderr)
            return

        # Step 2: Generate chart
        chart_result = subprocess.run(
            ["Rscript", os.path.join(rp, "tradeChart.R"), ticker, timeframe.value],
            capture_output=True,
            text=True
        )

        if chart_result.returncode != 0:
            await interaction.followup.send(f"Error generating chart for **${ticker}**.\n```{chart_result.stderr[-1500:]}```")
            print("CHART ERROR:", chart_result.stderr)
            return

        output_path = os.path.join(op, "markets/tradechart.png")

        if os.path.exists(output_path):
            await interaction.followup.send(
                content=f"**${ticker}** — {timeframe.name}",
                file=discord.File(output_path)
            )
        else:
            await interaction.followup.send("Chart file not found.")

    tree.add_command(markets_group)

    # ─────────────────────────────────────────────────────────────────────────
    # SPACE GROUP  /space <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    space_group = app_commands.Group(name="space", description="space / launch commands", guild_ids=[guild.id])

    @space_group.command(name="nextlaunch", description="next rocket launch from KSC")
    async def nextlaunch(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "spaceLaunches.py"))
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


    @space_group.command(name="isspass", description="next ISS passes over Jacksonville")
    async def isspass(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "issPass.py"))
        csv_path = os.path.join(op, "space/issPasses.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "couldn't compute ISS passes :(", ephemeral=True)
            return

        df = pd.read_csv(csv_path)
        if df.empty:
            await _send(interaction, "no ISS passes found in the next 7 days")
            return

        # lead with the next visible pass if one exists, otherwise next pass at all
        visible = df[df["visible"] == True]
        next_visible = visible.iloc[0] if not visible.empty else None
        next_any     = df.iloc[0]
        featured     = next_visible if next_visible is not None else next_any

        def duration_fmt(s):
            return f"{int(s) // 60}m {int(s) % 60}s"

        def el_bar(el):
            filled = round(el / 10)  # max ~90 deg -> 9 blocks
            return "█" * filled + "░" * (9 - min(filled, 9)) + f"  {el}°"

        # ── main embed ────────────────────────────────────────────────────────
        is_vis = bool(featured["visible"])
        color  = 0x44ccff if is_vis else 0x555577
        eye    = "👁️  Visible pass" if is_vis else "🌑  Pass (ISS in shadow)"

        emb = discord.Embed(
            title=f"Next ISS Pass Over Jacksonville",
            description=f"**{eye}**",
            color=color,
        )
        emb.add_field(name="📅 When (ET)",   value=featured["rise_et"],              inline=False)
        emb.add_field(name="🧭 Rises from",  value=featured["rise_az"],              inline=True)
        emb.add_field(name="🏔️ Max elevation", value=el_bar(featured["max_el"]),     inline=True)
        emb.add_field(name="🧭 Sets toward", value=featured["set_az"],               inline=True)
        emb.add_field(name="⏱️ Duration",    value=duration_fmt(featured["duration_s"]), inline=True)

        if not is_vis and next_visible is not None:
            emb.add_field(
                name="✨ Next visible pass",
                value=next_visible["rise_et"],
                inline=False
            )

        # ── upcoming passes table ─────────────────────────────────────────────
        lines = []
        for _, row in df.head(5).iterrows():
            eye_icon = "👁️" if row["visible"] else "🌑"
            lines.append(
                f"{eye_icon} **{row['rise_et']}**  ·  "
                f"↑{row['rise_az']} → ↓{row['set_az']}  ·  "
                f"{row['max_el']}° max  ·  {duration_fmt(row['duration_s'])}"
            )
        emb.add_field(name="📋 Next 5 passes", value="\n".join(lines), inline=False)
        emb.set_footer(text="TLE data: Celestrak · 👁️ = dark sky + ISS in sunlight (naked eye visible)")
        emb.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/0/04/International_Space_Station_after_undocking_of_STS-132.jpg/600px-International_Space_Station_after_undocking_of_STS-132.jpg")

        await _send(interaction, embed=emb)

    tree.add_command(space_group)

    # ─────────────────────────────────────────────────────────────────────────
    # F1 GROUP  /f1 <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    f1_group = app_commands.Group(name="f1", description="Formula 1", guild_ids=[guild.id])

    @f1_group.command(name="nextrace", description="next Formula 1 race weekend")
    async def f1_nextrace(interaction: discord.Interaction):
        await _defer(interaction)

        def _fetch():
            import urllib.request, json
            url = "https://api.jolpi.ca/ergast/f1/current/next.json"
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())

        try:
            data  = await asyncio.to_thread(_fetch)
            races = data["MRData"]["RaceTable"]["Races"]
            if not races:
                await _send(interaction, "no upcoming races found :(", ephemeral=True)
                return
            race = races[0]
        except Exception as e:
            await _send(interaction, f"couldn't fetch F1 data: {e}", ephemeral=True)
            return

        # ── parse ─────────────────────────────────────────────────────────────
        name     = race["raceName"]
        circuit  = race["Circuit"]["circuitName"]
        locality = race["Circuit"]["Location"]["locality"]
        country  = race["Circuit"]["Location"]["country"]
        date     = race["date"]               # YYYY-MM-DD
        time_utc = race.get("time", "")       # HH:MM:SSZ or missing

        # format race date nicely
        from datetime import datetime, timezone, timedelta
        try:
            if time_utc:
                dt_utc = datetime.strptime(f"{date}T{time_utc}", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                dt_et  = dt_utc.astimezone(timezone(timedelta(hours=-4)))  # EDT
                race_time_str = dt_et.strftime("%a %b %-d, %-I:%M %p ET")
                days_away = (dt_utc.date() - datetime.now(timezone.utc).date()).days
            else:
                dt_date = datetime.strptime(date, "%Y-%m-%d")
                race_time_str = dt_date.strftime("%a %b %-d")
                days_away = (dt_date.date() - datetime.now(timezone.utc).date()).days
        except Exception:
            race_time_str = date
            days_away = "?"

        # ── session schedule ─────────────────────────────────────────────────
        session_fields = []
        session_keys = [
            ("FirstPractice",  "🔧 Practice 1"),
            ("SecondPractice", "🔧 Practice 2"),
            ("ThirdPractice",  "🔧 Practice 3"),
            ("SprintQualifying", "⚡ Sprint Quali"),
            ("Sprint",         "⚡ Sprint Race"),
            ("Qualifying",     "⏱️ Qualifying"),
        ]
        for key, label in session_keys:
            if key in race:
                s = race[key]
                try:
                    s_utc = datetime.strptime(f"{s['date']}T{s['time']}", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    s_et  = s_utc.astimezone(timezone(timedelta(hours=-4)))
                    val   = s_et.strftime("%a %-I:%M %p ET")
                except Exception:
                    val = s["date"]
                session_fields.append((label, val))

        # ── embed ─────────────────────────────────────────────────────────────
        flag_map = {
            "Australia": "🇦🇺", "Bahrain": "🇧🇭", "Saudi Arabia": "🇸🇦",
            "Japan": "🇯🇵", "China": "🇨🇳", "United States": "🇺🇸",
            "Italy": "🇮🇹", "Monaco": "🇲🇨", "Canada": "🇨🇦",
            "Spain": "🇪🇸", "Austria": "🇦🇹", "United Kingdom": "🇬🇧",
            "Hungary": "🇭🇺", "Belgium": "🇧🇪", "Netherlands": "🇳🇱",
            "Azerbaijan": "🇦🇿", "Singapore": "🇸🇬", "Mexico": "🇲🇽",
            "Brazil": "🇧🇷", "Las Vegas": "🇺🇸", "Qatar": "🇶🇦",
            "UAE": "🇦🇪", "Abu Dhabi": "🇦🇪",
        }
        flag = flag_map.get(country, "🏁")

        emb = discord.Embed(
            title=f"{flag}  {name}",
            description=f"**{circuit}** — {locality}, {country}",
            color=0xe10600,   # F1 red
        )
        emb.add_field(name="🏎️ Race",        value=race_time_str,      inline=True)
        emb.add_field(name="📅 Days away",   value=str(days_away),     inline=True)
        for label, val in session_fields:
            emb.add_field(name=label, value=val, inline=True)
        emb.set_footer(text="Data: jolpica-f1 · times in ET")
        emb.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/thumb/3/33/F1.svg/500px-F1.svg.png")

        await _send(interaction, embed=emb)

    tree.add_command(f1_group)

    # ─────────────────────────────────────────────────────────────────────────
    # MLB GROUP  /mlb <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    mlb_group = app_commands.Group(name="mlb", description="Major League Baseball", guild_ids=[guild.id])

    @mlb_group.command(name="today", description="MLB games today")
    async def mlb_today(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "mlbToday.R"))

        csv_path = os.path.join(op, "sports/mlb/gamesToday.csv")

        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            await interaction.followup.send("❌ Could not load today's MLB schedule.")
            return

        if df.empty:
            await interaction.followup.send("No MLB games scheduled for today.")
            return

        today_str = datetime.today().strftime("%B %d, %Y")
        embed = discord.Embed(
            title=f"⚾ MLB Games — {today_str}",
            color=0x002D72
        )

        for _, row in df.iterrows():
            away  = row["teams_away_team_name"]
            home  = row["teams_home_team_name"]
            state = row["status_abstract_game_state"]  # "Preview", "Live", "Final"
            venue = row["venue_name"]
            time  = row.get("game_time", "TBD")

            away_pct = row.get("teams_away_league_record_pct", "")
            home_pct = row.get("teams_home_league_record_pct", "")

            away_rec = f"({away_pct})" if pd.notna(away_pct) and away_pct != "" else ""
            home_rec = f"({home_pct})" if pd.notna(home_pct) and home_pct != "" else ""

            if state == "Final":
                away_score = int(row["teams_away_score"])
                home_score = int(row["teams_home_score"])
                away_w = "🏆 " if away_score > home_score else ""
                home_w = "🏆 " if home_score > away_score else ""
                value = (
                    f"{away_w}**{away}** — {away_score}\n"
                    f"{home_w}**{home}** — {home_score}\n"
                    f"*Final • {venue}*"
                )
            elif state == "Live":
                away_score = int(row["teams_away_score"])
                home_score = int(row["teams_home_score"])
                value = (
                    f"🔴 **{away}** — {away_score}\n"
                    f"🔴 **{home}** — {home_score}\n"
                    f"*Live • {venue}*"
                )
            else:  # Preview
                value = f"*{time} ET • {venue}*"

            embed.add_field(
                name=f"{away} ({away_pct}) @ {home} ({home_pct})",
                value=value,
                inline=False
            )

        embed.set_footer(text="Data via MLB Stats API")
        await interaction.followup.send(embed=embed)

    @mlb_group.command(name="tomorrow", description="MLB games tomorrow")
    async def mlb_tomorrow(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "mlbtomorrow.R"))

        csv_path = os.path.join(op, "sports/mlb/gamestomorrow.csv")

        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            await _send(interaction, "❌ Could not load tomorrow's MLB schedule.")
            return

        if df.empty:
            await _send(interaction, "No MLB games scheduled for tomorrow.")
            return

        tomorrow_str = (datetime.today() + timedelta(days=1)).strftime("%B %d, %Y")
        embed = discord.Embed(
            title=f"⚾ MLB Games — {tomorrow_str}",
            color=0x002D72
        )

        for _, row in df.iterrows():
            away     = row["teams_away_team_name"]
            home     = row["teams_home_team_name"]
            away_pct = row.get("teams_away_league_record_pct", "")
            home_pct = row.get("teams_home_league_record_pct", "")
            venue    = row["venue_name"]
            time     = row.get("game_time", "TBD")

            embed.add_field(
                name=f"{away} @ {home}",
                value=f"*{time} ET • {venue}*",
                inline=False
            )

        embed.set_footer(text="Data via MLB Stats API")
        await _send(interaction, embed=embed)

    @mlb_group.command(name="fantasystandings", description="World Sillies fantasy baseball standings")
    async def mlb_fantasystandings(interaction: discord.Interaction):
        await _defer(interaction)

        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesStandings.py"))

        csv_path = os.path.join(op, "sports/mlb/fantasy/standings.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "couldn't fetch standings :(", ephemeral=True)
            return

        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        ICONS = {
            1: "🏆", 2: "🥈", 3: "🥉",
            4: "4️⃣", 5: "5️⃣", 6: "6️⃣",
            7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
        }

        embed = discord.Embed(
            title="⚾ World Sillies - Fantasy Standings",
            color=0x002D72,
        )

        rank_col   = ""
        team_col   = ""
        record_col = ""

        for row in rows:
            rank   = int(row["rank"])
            icon   = ICONS.get(rank, f"`{rank}`")
            rank_col   += f"{icon}\n"
            team_col   += f"**{row['team_name']}** - {row['owner']}\n"
            record_col += f"`{row['wins']}-{row['losses']}`\n"

        embed.add_field(name="Rank",      value=rank_col,   inline=True)
        embed.add_field(name="Team - Owner",   value=team_col,   inline=True)
        embed.add_field(name="Record", value=record_col, inline=True)
        embed.set_footer(text="Data via ESPN Fantasy API")

        await _send(interaction, embed=embed)

    @mlb_group.command(name="fantasyscoreboard", description="World Sillies live fantasy baseball scoreboard")
    async def mlb_fantasyscoreboard(interaction: discord.Interaction):
        await _defer(interaction)

        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesScoreboard.py"))

        csv_path = os.path.join(op, "sports/mlb/fantasy/scoreboard.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "couldn't fetch the scoreboard :(", ephemeral=True)
            return

        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        embed = discord.Embed(
            title="⚾ World Sillies - Scoreboard",
            color=0x002D72,
        )

        for row in rows:
            home      = row["home_team"].strip()
            away      = row["away_team"].strip()
            home_score = float(row["home_score"])
            away_score = float(row["away_score"])

            # trophy on the winning side, dash if tied
            if home_score > away_score:
                home_str = f"🏆 **{home}**"
                away_str = away
            elif away_score > home_score:
                home_str = home
                away_str = f"🏆 **{away}**"
            else:
                home_str = f"**{home}**"
                away_str = f"**{away}**"

            embed.add_field(
                name=f"{away_str}  vs  {home_str}",
                value=f"`{away_score:.1f}`  -  `{home_score:.1f}`",
                inline=False,
            )

        embed.set_footer(text="Data via ESPN Fantasy API")
        await _send(interaction, embed=embed)

        # team names for autocomplete — update if your league changes
    SILLIES_TEAMS = [
        "dock ellis fan club",
        "Chandler Simpson Worshipper",
        "UNCLE CUCKUS",
        "2 balls 1 bat",
        "Jose Caballero",
        "Zach's Baseball Classic",
        "Nolan Ryan's Right Hook",
        "JungHooLee is My Father",
    ]
 
    async def sillies_team_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=t, value=t)
            for t in SILLIES_TEAMS if current.lower() in t.lower()
        ][:25]
 
    @mlb_group.command(name="fantasyroster", description="World Sillies roster & today's points for a team")
    @app_commands.describe(team="fantasy team name")
    @app_commands.autocomplete(team=sillies_team_autocomplete)
    async def mlb_fantasyroster(interaction: discord.Interaction, team: str):
        await _defer(interaction)

        print("DEBUG pp:", pp)
 
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesRoster.py"))
 
        csv_path = os.path.join(op, "sports/mlb/fantasy/roster.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "couldn't fetch roster data :(", ephemeral=True)
            return
 
        import csv as _csv
        with open(csv_path, newline="") as f:
            all_rows = list(_csv.DictReader(f))
 
        # fuzzy-ish match in case of trailing spaces (e.g. "JungHooLee is My Father ")
        team_rows = [r for r in all_rows if r["team_name"].strip().lower() == team.strip().lower()]
 
        if not team_rows:
            await _send(interaction, f"couldn't find team **{team}** in the roster data.", ephemeral=True)
            return
 
        active = [r for r in team_rows if r["slot_position"] not in ("BE", "IL")]
        bench  = [r for r in team_rows if r["slot_position"] == "BE"]
        il     = [r for r in team_rows if r["slot_position"] == "IL"]
 
        def fmt_player(r):
            pts = float(r["points"])
            pts_str = f"`{pts:+.1f}`" if pts != 0 else "`  0.0`"
            inj = " 🤕" if r["injury_status"] not in ("ACTIVE", "NORMAL", "") else ""
            return f"{pts_str} **{r['player_name']}**{inj} ({r['slot_position']} · {r['position']})"
 
        total_today = sum(float(r["points"]) for r in active)
 
        embed = discord.Embed(
            title=f"⚾ {team.strip()} - Today's Roster",
            description=f"**Points today (active): `{total_today:.1f}`**",
            color=0x002D72,
        )
 
        if active:
            embed.add_field(
                name="🟢 Active",
                value="\n".join(fmt_player(r) for r in active) or "—",
                inline=False,
            )
        if bench:
            embed.add_field(
                name="🪑 Bench",
                value="\n".join(fmt_player(r) for r in bench) or "—",
                inline=False,
            )
        if il:
            embed.add_field(
                name="🏥 IL",
                value="\n".join(fmt_player(r) for r in il) or "—",
                inline=False,
            )
 
        embed.set_footer(text="Data via ESPN Fantasy API")
        await _send(interaction, embed=embed)

    @mlb_group.command(name="fantasyfa", description="World Sillies top 10 free agents by position")
    @app_commands.describe(position="player position to look up")
    @app_commands.choices(position=[
        app_commands.Choice(name="PP - Probable Pitchers Tomorrow", value="PP"),
        app_commands.Choice(name="SP - Starting Pitcher", value="SP"),
        app_commands.Choice(name="RP - Relief Pitcher",   value="RP"),
        app_commands.Choice(name="C  - Catcher",          value="C"),
        app_commands.Choice(name="1B - First Base",       value="1B"),
        app_commands.Choice(name="2B - Second Base",      value="2B"),
        app_commands.Choice(name="3B - Third Base",       value="3B"),
        app_commands.Choice(name="SS - Shortstop",        value="SS"),
        app_commands.Choice(name="OF - Outfield",         value="OF"),
    ])
    async def mlb_fantasyfa(interaction: discord.Interaction, position: str):
        await _defer(interaction)

        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesFA.py"), position)

        csv_path = os.path.join(op, "sports/mlb/fantasy/freeagents.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "couldn't fetch free agent data :(", ephemeral=True)
            return

        import csv as _csv
        with open(csv_path, newline="") as f:
            rows = list(_csv.DictReader(f))

        if not rows:
            await _send(interaction, f"no free agents found at position **{position}**.", ephemeral=True)
            return

        POSITION_EMOJI = {
            "SP": "🤾", "RP": "🔥", "C": "🎯",
            "1B": "1️⃣", "2B": "2️⃣", "3B": "3️⃣",
            "SS": "⚡", "OF": "🌿",
        }
        emoji = POSITION_EMOJI.get(position, "⚾")
        is_pitcher = position in ("SP", "RP", "PP")
        is_pp      = position == "PP"

        embed = discord.Embed(
            title=f"⚾ World Sillies - {'Probable Pitcher' if is_pp else f'Top {position}'} Free Agents",
            description="Starting tomorrow · Sorted by % owned" if is_pp else "Sorted by % owned · Top 10 available" + (" · ⚾ = starting tomorrow" if is_pitcher else ""),
            color=0x002D72,
        )

        player_col = ""
        owned_col  = ""
        proj_col   = ""

        for i, row in enumerate(rows, start=1):
            inj      = " 🤕" if row["injury_status"] not in ("ACTIVE", "NORMAL", "") else ""
            starting = " ⚾" if row.get("starting_tomorrow") == "True" and is_pitcher else ""
            pct      = float(row["percent_owned"])
            proj     = float(row["projected_total_points"])
            player_col += f"`{i:>2}.` **{row['player_name']}**{starting}{inj} ({row['pro_team']})\n"
            owned_col  += f"`{pct:.1f}%`\n"
            proj_col   += f"`{proj:.0f} pts`\n"

        embed.add_field(name="Player",     value=player_col, inline=True)
        embed.add_field(name="% Owned",    value=owned_col,  inline=True)
        embed.add_field(name="Proj Total", value=proj_col,   inline=True)
        embed.set_footer(text="Data via ESPN Fantasy API + MLB Stats API")

        await _send(interaction, embed=embed)

    @mlb_group.command(name="whohits", description="Top 10 hitters vs. a pitcher (last 5 seasons)")
    @app_commands.describe(pitcher="Full name of the pitcher, e.g. 'Paul Skenes'")
    async def mlb_pitcherhitters(interaction: discord.Interaction, pitcher: str):
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "whoHits.py"), pitcher, op)
 
        csv_path = os.path.join(op, "sports/mlb/top10_vs_pitcher.csv")
 
        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            await _send(interaction, f"❌ No data found for **{pitcher}**. Check the spelling or the logs.")
            return
 
        if df.empty:
            await _send(interaction, f"No matchup data found for **{pitcher}** in the last 5 seasons (min. 5 PA).")
            return
 
        embed = discord.Embed(
            title=f"⚾ Top Hitters vs. {pitcher}",
            description="Sorted by OPS · Min. 5 PA · Last 5 seasons",
            color=0x002D72
        )
 
        for i, row in df.iterrows():
            def fmt(val):
                try:    return f"{float(val):.3f}"
                except: return "---"
 
            name = row['Batter']
            team = row.get('Team', '?')
            avg  = fmt(row.get('AVG'))
            obp  = fmt(row.get('OBP'))
            slg  = fmt(row.get('SLG'))
            ops  = fmt(row.get('OPS'))
            h    = int(row['H'])
            hr   = int(row['HR'])
            bb   = int(row['BB'])
            pa   = int(row['PA'])
 
            embed.add_field(
                name=f"{i+1}. {name} ({team})",
                value=f"`{avg} / {obp} / {slg}` · OPS: `{ops}` · {h}H {hr}HR {bb}BB in {pa}PA",
                inline=False
            )
 
        embed.set_footer(text="Data via Baseball Savant / Statcast")
        await _send(interaction, embed=embed)
    
    @mlb_group.command(name="mismatch", description="Top batter/pitcher mismatches for tomorrow's games")
    @app_commands.describe(mode="Which mismatches to show (default: both)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Both (Top 5 each)",       value="both"),
        app_commands.Choice(name="Top 10 Batter Favored",   value="batters"),
        app_commands.Choice(name="Top 10 Pitcher Favored",  value="pitchers"),
    ])
    async def mlb_mismatch(interaction: discord.Interaction, mode: str = "both"):
        await _defer(interaction)
        print(f"[DEBUG] /mlb mismatch called with mode='{mode}'")

        csv_path = os.path.join(op, "sports/mlb/mismatch.csv")

        # Only regenerate if CSV is missing or more than 3 hours old
        needs_refresh = True
        if os.path.exists(csv_path):
            age_hours = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(csv_path))).total_seconds() / 3600
            if age_hours < 3:
                needs_refresh = False
                print(f"[DEBUG] Using cached CSV ({age_hours:.1f}h old)")

        if needs_refresh:
            print("[DEBUG] Regenerating mismatch data...")
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbProbPitchers.py"))
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbMismatch.py"))

        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            await _send(interaction, "❌ Could not generate mismatch data. Check the logs.")
            return

        if df.empty:
            await _send(interaction, "No matchup data found with sufficient PA history (min. 5 PA).")
            return

        date_str = (datetime.today() + timedelta(days=1)).strftime("%B %d, %Y")

        embed = discord.Embed(
            title=f"⚾ Pitcher/Batter Mismatches — {date_str}",
            description="Based on last 5 seasons of Statcast data · Min. 5 PA",
            color=0x002D72
        )

        def fmt(val):
            try:    return f"{float(val):.3f}"
            except: return "---"

        def format_batter_row(row):
            return (
                f"**{row['batter']}** vs {row['pitcher']} *({row['matchup']})*\n"
                f"`{fmt(row['AVG'])} / {fmt(row['OBP'])} / {fmt(row['SLG'])}` · "
                f"OPS: `{fmt(row['OPS'])}` · "
                f"{int(row['H'])}H {int(row['HR'])}HR {int(row['BB'])}BB {int(row['K'])}K in {int(row['PA'])}PA\n\n"
            )

        def format_pitcher_row(row):
            return (
                f"**{row['pitcher']}** vs {row['batter']} *({row['matchup']})*\n"
                f"`{fmt(row['AVG'])} / {fmt(row['OBP'])} / {fmt(row['SLG'])}` · "
                f"OPS: `{fmt(row['OPS'])}` · "
                f"{int(row['H'])}H {int(row['HR'])}HR {int(row['BB'])}BB {int(row['K'])}K in {int(row['PA'])}PA\n\n"
            )

        def add_fields(embed, rows, formatter, base_name, emoji):
            """Add rows as fields, splitting into two if showing 10 to stay under Discord's 1024 char limit."""
            if len(rows) <= 5:
                embed.add_field(
                    name=f"{emoji} {base_name}",
                    value="".join(formatter(row) for _, row in rows.iterrows()).strip(),
                    inline=False
                )
            else:
                half = len(rows) // 2
                embed.add_field(
                    name=f"{emoji} {base_name} (1-{half})",
                    value="".join(formatter(row) for _, row in rows.iloc[:half].iterrows()).strip(),
                    inline=False
                )
                embed.add_field(
                    name=f"{emoji} {base_name} ({half+1}-{len(rows)})",
                    value="".join(formatter(row) for _, row in rows.iloc[half:].iterrows()).strip(),
                    inline=False
                )

        if mode in ("both", "batters"):
            n = 5 if mode == "both" else 10
            add_fields(embed, df.head(n), format_batter_row, "Batter Favored (Highest OPS)", "🔥")

        if mode in ("both", "pitchers"):
            n = 5 if mode == "both" else 10
            add_fields(embed, df.tail(n).iloc[::-1].reset_index(drop=True), format_pitcher_row, "Pitcher Favored (Lowest OPS)", "🧊")

        embed.set_footer(text="Data via Baseball Savant / Statcast")
        await _send(interaction, embed=embed)

    tree.add_command(mlb_group)

    # ── /jaxships ─────────────────────────────────────────────────────────────
    @tree.command(name="jaxships", description="ships in and around Jacksonville / JAXPORT right now", guild=guild)
    async def jaxships(interaction: discord.Interaction):
        await _defer(interaction)
        mst_key = os.environ.get("MYSHIPTRACKING_KEY", "")
        if not mst_key:
            await _send(interaction, "⚠️ MYSHIPTRACKING_KEY env var not set — grab a free trial key at https://myshiptracking.com", ephemeral=True)
            return
        env = {**os.environ, "MYSHIPTRACKING_KEY": mst_key}
        await asyncio.to_thread(
            lambda: subprocess.run(
                ["PYTHON", os.path.join(pp, "jaxShips.py")],
                check=False, timeout=60, env=env
            )
        )
        img = os.path.join(op, "maritime/jaxShips.png")
        if not os.path.exists(img):
            await _send(interaction, "couldn't generate the ship plot :(", ephemeral=True)
            return
        await _send(interaction, "⚓ here's what's on the water around jax right now:",
                    file=discord.File(img))

    # ── /jaxplanes ────────────────────────────────────────────────────────────
    @tree.command(name="jaxplanes", description="planes over Jacksonville right now", guild=guild)
    @app_commands.describe(radius="display radius in nautical miles (default 50, max 250)")
    async def jaxplanes(interaction: discord.Interaction, radius: int = 50):
        radius = max(10, min(radius, 250))  # clamp to sane range
        await _defer(interaction)
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "overJax.py"), "--radius", str(radius))
        img = os.path.join(op, "aerospace/jaxPlanes.png")
        if not os.path.exists(img):
            await _send(interaction, "couldn't generate the plot :(", ephemeral=True)
            return
        await _send(interaction, f"✈ here's what's flying over jax right now (within {radius}nm):",
                    file=discord.File(img))