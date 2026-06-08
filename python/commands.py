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

CURRENT_YEAR = datetime.now().year

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


# ── DJ voice state ────────────────────────────────────────────────────────────
# Keyed by guild_id. Each value: {'vc': VoiceClient, 'queue': [Path], 'now_playing': Path|None}
_dj_state: dict[int, dict] = {}

_AUDIO_EXTS  = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
_DJ_MUSIC    = Path(os.path.expanduser("~/discordBot/outputs/dj/music/"))
_DJ_MIXES    = Path(os.path.expanduser("~/discordBot/outputs/dj/mixes/"))
_DJ_XML      = Path(os.path.expanduser("~/discordBot/outputs/dj/rekordbox.xml"))

_dj_genre_index:  dict[str, list[Path]] | None = None  # built lazily on first use
_dj_artist_index: dict[str, list[Path]] | None = None  # built lazily on first use

_GENRE_NORMALIZE = {
    "Tech-House":       "Tech House",
    "UK-House":         "UK Garage / Bassline",
    "UK Garage / Bassline": "UK Garage",
    "Garage / Bassline / Grime": "UK Garage",
}

def _dj_build_genre_index() -> dict[str, list[Path]]:
    from mutagen.id3 import ID3
    index: dict[str, list[Path]] = {}
    for root, _, files in os.walk(_DJ_MUSIC):
        for f in files:
            if Path(f).suffix.lower() not in _AUDIO_EXTS or f.startswith("._"):
                continue
            path = Path(root) / f
            try:
                genre = str(ID3(str(path)).get("TCON") or "Unknown").strip()
                genre = _GENRE_NORMALIZE.get(genre, genre)
            except Exception:
                genre = "Unknown"
            index.setdefault(genre, []).append(path)
    return index

def _dj_genres() -> dict[str, list[Path]]:
    global _dj_genre_index
    if _dj_genre_index is None:
        _dj_genre_index = _dj_build_genre_index()
    return _dj_genre_index

def _dj_build_artist_index() -> dict[str, list[Path]]:
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    index: dict[str, list[Path]] = {}
    for root, _, files in os.walk(_DJ_MUSIC):
        for f in files:
            suffix = Path(f).suffix.lower()
            if suffix not in _AUDIO_EXTS or f.startswith("._"):
                continue
            path = Path(root) / f
            artist = None
            try:
                if suffix in {".mp3", ".wav"}:
                    a = ID3(str(path)).get("TPE1")
                    artist = str(a).strip() if a else None
                elif suffix in {".m4a", ".mp4"}:
                    a = MP4(str(path)).get("\xa9ART")
                    artist = str(a[0]).strip() if a else None
            except Exception:
                pass
            if not artist:
                # fallback: parse "Artist - Title" from filename
                stem = path.stem
                artist = stem.split(" - ")[0].strip() if " - " in stem else "Unknown"
            index.setdefault(artist, []).append(path)
    return index

def _dj_artists() -> dict[str, list[Path]]:
    global _dj_artist_index
    if _dj_artist_index is None:
        _dj_artist_index = _dj_build_artist_index()
    return _dj_artist_index

def _dj_mixes() -> list[Path]:
    if not _DJ_MIXES.exists():
        return []
    return sorted(f for f in _DJ_MIXES.iterdir() if f.suffix.lower() in _AUDIO_EXTS)

def _dj_playlists() -> dict[str, list[Path]]:
    """Read playlists from a rekordbox XML export at outputs/dj/rekordbox.xml."""
    if not _DJ_XML.exists():
        return {}
    import xml.etree.ElementTree as _ET
    from urllib.parse import unquote as _unquote
    try:
        tree = _ET.parse(str(_DJ_XML))
        root = tree.getroot()

        # build filename -> Path map from music folder
        name_map: dict[str, Path] = {}
        for f in _DJ_MUSIC.iterdir():
            if f.suffix.lower() in _AUDIO_EXTS:
                name_map[f.name.lower()] = f

        # build TrackID -> Path map from COLLECTION
        track_map: dict[str, Path] = {}
        collection = root.find('COLLECTION')
        if collection is not None:
            for track in collection.findall('TRACK'):
                tid   = track.get('TrackID')
                loc   = _unquote(track.get('Location', ''))
                fname = loc.split('/')[-1]
                if fname.lower() in name_map:
                    track_map[tid] = name_map[fname.lower()]

        # walk PLAYLISTS
        result: dict[str, list[Path]] = {}
        playlists_node = root.find('PLAYLISTS')
        if playlists_node is not None:
            for node in playlists_node.iter('NODE'):
                if node.get('Type') != '1':
                    continue  # skip folders
                name = node.get('Name', '').strip()
                if not name or name in ('ROOT', 'CUE Analysis Playlist'):
                    continue
                tracks = [track_map[t.get('Key')]
                          for t in node.findall('TRACK')
                          if t.get('Key') in track_map]
                if tracks:
                    result[name] = tracks

        print(f"[DJ] loaded {len(result)} playlists from XML "
              f"({sum(len(v) for v in result.values())} total tracks)")
        return result
    except Exception as e:
        print(f"[DJ] playlist read error: {e}")
        return {}

async def _dj_advance(guild_id: int) -> None:
    state = _dj_state.get(guild_id)
    if not state:
        return
    vc: discord.VoiceClient | None = state.get("vc")
    if not vc or not vc.is_connected():
        state["now_playing"] = None
        return
    if vc.is_playing() or vc.is_paused():
        return  # TTS or another source already active - don't interrupt
    queue: list[Path] = state.get("queue", [])
    if not queue:
        state["now_playing"] = None
        return
    track = queue.pop(0)
    state["now_playing"] = track
    loop = asyncio.get_running_loop()

    source = discord.FFmpegPCMAudio(str(track))
    source = discord.PCMVolumeTransformer(source, volume=1.0)

    def _after(err):
        if err:
            print(f"[DJ] player error: {err}")
        asyncio.run_coroutine_threadsafe(_dj_advance(guild_id), loop)
    vc.play(source, after=_after)
    print(f"[DJ] now playing: {track.stem} ({len(queue)} left in queue)")


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
                "Acc":  f"{acc:.1%}" if pd.notna(acc) else "-",
            })
        return pd.DataFrame(rows)

    def _make_pages(df, rows_per_page=12, title=":football: Room 40 Standings",
                    color=discord.Color.green()):
        pages = []
        for start in range(0, len(df), rows_per_page):
            ch  = df.iloc[start:start+rows_per_page]
            col = lambda s: "\n".join(s.astype(str)) or "-"
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

    @weather_group.command(name="alerts", description="Active NWS weather alerts for Duval County")
    async def weather_alerts(interaction: discord.Interaction):
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "nwsAlerts.py")],
                capture_output=True, text=True, timeout=30
            )
        )
        csv_path = os.path.join(op, "weather/nwsAlerts.csv")
        if result.returncode != 0 or not os.path.exists(csv_path):
            await _send(interaction, "couldn't reach NWS alerts API :(", ephemeral=True)
            return
        df = pd.read_csv(csv_path)
        if df.empty:
            await _send(interaction, "no alert data returned")
            return
        # check if only placeholder "no alerts" row
        if len(df) == 1 and str(df.iloc[0].get("event", "")).lower() == "none":
            emb = discord.Embed(
                title="✅ No Active Alerts - Duval County",
                description="No active NWS weather alerts right now.",
                color=0x00CC00,
            )
            emb.set_footer(text="source: NWS / api.weather.gov")
            await _send(interaction, embed=emb)
            return
        SEVERITY_COLOR = {
            "Extreme":   0xFF0000, "Severe":    0xFF6600,
            "Moderate":  0xFFCC00, "Minor":     0xFFFF00,
            "Unknown":   0x888888,
        }
        SEVERITY_EMOJI = {
            "Extreme": "🚨", "Severe": "⚠️", "Moderate": "🟡", "Minor": "🔵", "Unknown": "ℹ️"
        }
        # use color/emoji of the worst alert
        worst_sev = df.iloc[0]["severity"] if "severity" in df.columns else "Unknown"
        color = SEVERITY_COLOR.get(str(worst_sev), 0xFF6600)
        emb = discord.Embed(
            title=f"⚠️ {len(df)} Active Alert{'s' if len(df) > 1 else ''} - Duval County",
            color=color,
        )
        for _, row in df.head(5).iterrows():
            sev   = str(row.get("severity", "Unknown"))
            emoji = SEVERITY_EMOJI.get(sev, "ℹ️")
            event = str(row.get("event", "Alert"))
            hl    = str(row.get("headline", ""))
            exp   = str(row.get("expires", ""))[:16].replace("T", " ")
            area  = str(row.get("area_desc", ""))[:60]
            instr = str(row.get("instruction", ""))
            instr = (instr[:200] + "…") if len(instr) > 200 else instr
            val   = f"**{hl[:120]}**\n"
            val  += f"Expires: {exp}  ·  {area}\n"
            if instr and instr not in ("nan", ""):
                val += f"*{instr}*"
            emb.add_field(name=f"{emoji} {event} ({sev})", value=val.strip(), inline=False)
        if len(df) > 5:
            emb.set_footer(text=f"Showing 5 of {len(df)} alerts  ·  source: NWS / api.weather.gov")
        else:
            emb.set_footer(text="source: NWS / api.weather.gov")
        await _send(interaction, embed=emb)

    tree.add_command(weather_group)
    tree.add_command(surf_group)

    # ─────────────────────────────────────────────────────────────────────────
    # HISTORY GROUP  /history <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    history_group = app_commands.Group(name="history", description="server history charts", guild_ids=[guild.id])

    @history_group.command(name="server", description="total server message history")
    async def hist_server(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "serverHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/allMessages.png")))

    @history_group.command(name="channel", description="channel message history")
    async def hist_channel(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "channelHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/channelMessages.png")))

    @history_group.command(name="user", description="user message history")
    async def hist_user(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "userHistory.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/userMessages.png")))

    @history_group.command(name="daily", description="daily messages plot")
    async def hist_daily(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "dailyMessage.R"))
        await _send(interaction, ":) <3",
                    file=discord.File(os.path.join(op, "metrics/dailyMessages.png")))

    @history_group.command(name="invitegraph", description="bubble network of who invited who to the server")
    async def hist_invitegraph(interaction: discord.Interaction):
        await _defer(interaction)

        log_path = os.path.expanduser("~/discordBot/outputs/server/invite_log.csv")
        if not os.path.exists(log_path):
            await _send(interaction,
                "📊 no invite data yet - the bot started tracking invites from when it was last restarted. "
                "invite history will build up as new members join.",
                ephemeral=True)
            return

        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "inviteGraph.py")],
                capture_output=True, text=True, timeout=30
            )
        )

        img = os.path.expanduser("~/discordBot/outputs/server/invite_graph.png")
        if not os.path.exists(img):
            err = result.stderr[-300:] if result.stderr else "no output"
            await _send(interaction, f"❌ graph failed\n```{err}```", ephemeral=True)
            return

        await _send(interaction, file=discord.File(img, filename="invite_graph.png"))

    @history_group.command(name="repograph", description="discordBot repo growth - lines of code and commits over time")
    async def hist_repograph(interaction: discord.Interaction):
        await _defer(interaction)

        # Step 1: generate CSV from git log (~20-30s for full history)
        try:
            proc = await asyncio.create_subprocess_exec(
                PYTHON, os.path.join(pp, "repoGraph.py"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                await _send(interaction, "⏱️ timed out building repo data, try again", ephemeral=True)
                return
        except Exception as e:
            await _send(interaction, f"❌ failed to build repo data: {e}", ephemeral=True)
            return

        if proc.returncode != 0 or b"error" in stdout.lower():
            err = stderr.decode()[-300:] if stderr else stdout.decode()[-300:]
            await _send(interaction, f"❌ repo data error\n```{err}```", ephemeral=True)
            return

        # Step 2: render the R plot
        try:
            proc2 = await asyncio.create_subprocess_exec(
                "Rscript", os.path.join(rp, "repoGraph.R"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc2.kill()
                await proc2.communicate()
                await _send(interaction, "⏱️ R plot timed out, try again", ephemeral=True)
                return
        except Exception as e:
            await _send(interaction, f"❌ R plot failed: {e}", ephemeral=True)
            return

        img = os.path.expanduser("~/discordBot/outputs/server/repo_graph.png")
        if not os.path.exists(img):
            err = stderr2.decode()[-300:] if stderr2 else "no output"
            await _send(interaction, f"❌ plot not generated\n```{err}```", ephemeral=True)
            return

        await _send(interaction, file=discord.File(img, filename="repo_graph.png"))

    tree.add_command(history_group)

    # ─────────────────────────────────────────────────────────────────────────
    # CATS GROUP  /cats <subcommand>  (games, schedule, reactions only)
    # Player image commands dropped for now - add back once bot is stable
    # ─────────────────────────────────────────────────────────────────────────
    cats_group        = app_commands.Group(name="cats",        description="Florida Panthers",              guild_ids=[guild.id])


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

    # ── /cats rats - single command with player choice ────────────────────────
    RATS = {
        "barkov":       (":)",                                                      "sports/nhl/barky.png"),
        "bobby":        ("BRICK WALL BOB",                                          "sports/nhl/brickwallbob.jpg"),
        "thuggybobby":  (None,                                                      "sports/nhl/bobbyChain.png"),
        "praisebobby":  ("bobby bless",                                             "sports/nhl/stbobby.jpeg"),
        "chucky":       (None,                                                      "sports/nhl/chucky.jpeg"),
        "reino":        ("i love you sam <3",                                       "sports/nhl/reino.png"),
        "ekblad":       ("BOOSTED EKKY",                                            "sports/nhl/ekblad.jpeg"),
        "swaggy":       ("NEVER FORGET",                                            "sports/nhl/buttpuck.mov"),
        "marchand":     ("ALL HAIL THE RAT KING\nPANTHERS LEGEND AND FUTURE HALL OF FAMER BRADLEY MARCHAND", "sports/nhl/marchand.png"),
        "drunkmarchand":("BRAD MF MARCHAND",                                        "sports/nhl/drunkMarchand.jpg"),
        "benny":        (None,                                                      "sports/nhl/benny.jpeg"),
        "eetu":         (None,                                                      "sports/nhl/eetu.png"),
        "gaddy":        ("HEY SIRI PLAY SICKO MODE",                               "sports/nhl/gadjo.jpg"),
        "sethjones":    ("SETH MF JONES",                                           "sports/nhl/sethjones.jpeg"),
        "lundy":        ("lundy a mf shooter fr",                                   "sports/nhl/lundell.png"),
        "forsling":     (None,                                                      "sports/nhl/forsling.png"),
        "jesper":       ("average jesper boqvist moment",                           "sports/nhl/jesper.png"),
        "schmidty":     ("NATE THE GREAT",                                          "sports/nhl/nateSchmidt.png"),
    }

    @cats_group.command(name="rats", description="Florida Panthers player pics")
    @app_commands.describe(player="which rat")
    @app_commands.choices(player=[
        app_commands.Choice(name="Barkov",            value="barkov"),
        app_commands.Choice(name="Bobrovsky",         value="bobby"),
        app_commands.Choice(name="Bobrovsky (iced)",  value="thuggybobby"),
        app_commands.Choice(name="Bobrovsky (saint)", value="praisebobby"),
        app_commands.Choice(name="Tkachuk",           value="chucky"),
        app_commands.Choice(name="Reinhart",          value="reino"),
        app_commands.Choice(name="Ekblad",            value="ekblad"),
        app_commands.Choice(name="Verhaeghe (swaggy)", value="swaggy"),
        app_commands.Choice(name="Marchand",          value="marchand"),
        app_commands.Choice(name="Marchand (drunk)",  value="drunkmarchand"),
        app_commands.Choice(name="Bennett",           value="benny"),
        app_commands.Choice(name="Luostarinen",       value="eetu"),
        app_commands.Choice(name="Gagnier",           value="gaddy"),
        app_commands.Choice(name="Seth Jones",        value="sethjones"),
        app_commands.Choice(name="Lundell",           value="lundy"),
        app_commands.Choice(name="Forsling",          value="forsling"),
        app_commands.Choice(name="Boqvist",           value="jesper"),
        app_commands.Choice(name="Nate Schmidt",      value="schmidty"),
    ])
    async def cats_rats(interaction: discord.Interaction, player: app_commands.Choice[str]):
        key = player.value
        text, relpath = RATS[key]
        filepath = os.path.join(op, relpath)
        if text:
            await _defer(interaction)
            await _send(interaction, text, file=discord.File(filepath))
        else:
            await interaction.response.send_message(file=discord.File(filepath))

    @cats_group.command(name="pantr", description="pantr hands")
    async def pantr(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/pantrHands.jpeg")))

    @cats_group.command(name="kodak", description="Kodak")
    async def kodak(interaction: discord.Interaction):
        await interaction.response.send_message(
            file=discord.File(os.path.join(op, "sports/nhl/kodak.jpg")))

    tree.add_command(cats_group)




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
        emb.set_thumbnail(url="attachment://nhl.png")
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        logo_path = os.path.expanduser("~/discordBot/stickers/nhl.png")
        await interaction.followup.send(embed=emb, file=discord.File(logo_path, filename="nhl.png"))

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
        emb.set_thumbnail(url="attachment://nhl.png")
        for _, row in df.iterrows():
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        logo_path = os.path.expanduser("~/discordBot/stickers/nhl.png")
        await interaction.followup.send(embed=emb, file=discord.File(logo_path, filename="nhl.png"))

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
        await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "nbaToday.py"))
        csv_today = os.path.join(op, "sports/nba/gamesToday.csv")
        if not os.path.exists(csv_today):
            await _send(interaction, "no hoops today :("); return
        today_df = pd.read_csv(csv_today)
        live_scores = {}
        csv_live = os.path.join(op, "sports/nba/liveScoreboard.csv")
        if os.path.exists(csv_live) and os.path.getsize(csv_live) > 0:
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
            emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
        emb.set_thumbnail(url="attachment://nba.png")
        logo_path = os.path.expanduser("~/discordBot/stickers/nba.png")
        await interaction.followup.send(embed=emb, file=discord.File(logo_path, filename="nba.png"))

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
    # PGA GROUP  /pga <subcommand>
    # ─────────────────────────────────────────────────────────────────────────
    pga_group = app_commands.Group(name="pga", description="PGA Tour golf", guild_ids=[guild.id])

    def _fetch_pga_data():
        subprocess.run([PYTHON, os.path.join(pp, "pgaLeaderboard.py")],
                    check=False, timeout=30)
        tourn_csv = os.path.join(op, "sports/pga/tournament.csv")
        lb_csv    = os.path.join(op, "sports/pga/leaderboard.csv")
        if not os.path.exists(tourn_csv) or not os.path.exists(lb_csv):
            return None, None
        t  = pd.read_csv(tourn_csv)
        lb = pd.read_csv(lb_csv)
        if t.empty or lb.empty or not t.iloc[0].get("name"):
            return None, None
        return t.iloc[0], lb

    @pga_group.command(name="tournament", description="live leaderboard for the current PGA tournament")
    async def pga_tournament(interaction: discord.Interaction):
        await _defer(interaction)
        t, lb = await asyncio.to_thread(_fetch_pga_data)
        if t is None:
            await _send(interaction, "⛳ no PGA tournament in progress right now")
            return

        detail = str(t.get("detail") or t.get("status") or "").strip()
        status = str(t.get("status") or "").strip()
        course = t.get("course", "")
        city   = t.get("city", "")
        state  = t.get("state", "")
        location_parts = [x for x in [course, city, state]
                        if x and str(x).strip() and str(x).strip().lower() != "nan"]
        location_line = "  -  ".join(location_parts)

        desc = f"{location_line}\n*{detail}*".strip() if location_line else f"*{detail}*"
        emb = discord.Embed(
            title=f"⛳ {t['name']}",
            description=desc,
            color=0x2E7D32,
        )
        emb.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/en/thumb/9/9e/PGA_Tour_logo.svg/1200px-PGA_Tour_logo.svg.png")

        # check if tournament has actual scores yet
        real_scores = lb[
            lb["score"].notna() &
            (lb["score"].astype(str).str.strip() != "-") &
            (lb["score"].astype(str).str.strip() != "–")
        ] if not lb.empty else pd.DataFrame()

        if not real_scores.empty:
            for i, (_, row) in enumerate(real_scores.iterrows(), start=1):
                pos   = str(row.get("position", "")).strip()
                name  = str(row.get("name", "-"))
                score = str(row.get("score", "-"))
                today = str(row.get("today", "")).strip()
                thru  = str(row.get("thru", "")).strip()

                parts = [f"**{score}**"]
                if today and today not in ("-", "–", "nan", ""):
                    parts.append(f"today: {today}")
                if thru and thru not in ("-", "–", "nan", ""):
                    parts.append(f"thru {thru}")

                label = f"{pos} {name}" if pos and pos not in ("-", "–", "nan") else f"{i}. {name}"
                emb.add_field(name=label, value="  ".join(parts), inline=False)
        else:
            # scheduled or between rounds - show tee time info if available
            emb.add_field(
                name="📅 Status",
                value=detail if detail else "Scheduled - tee times not yet posted",
                inline=False,
            )
            if not lb.empty:
                # show field (players with names even if no scores)
                emb.add_field(
                    name=f"👥 Field ({len(lb)} players)",
                    value=", ".join(lb["name"].head(10).tolist()) + ("..." if len(lb) > 10 else ""),
                    inline=False,
                )

        await _send(interaction, embed=emb)

    @pga_group.command(name="standings", description="PGA Tour season standings - FedEx Cup points")
    async def pga_standings(interaction: discord.Interaction):
        await _defer(interaction)

        def _fetch_standings():
            subprocess.run([PYTHON, os.path.join(pp, "pgaSeasonStandings.py")],
                        check=False, timeout=20)
            csv_path = os.path.join(op, "sports/pga/season_standings.csv")
            if not os.path.exists(csv_path):
                return None
            return pd.read_csv(csv_path)

        df = await asyncio.to_thread(_fetch_standings)
        if df is None or df.empty:
            await _send(interaction, "⛳ couldn't fetch PGA season standings right now")
            return

        import datetime as _dt
        year = _dt.date.today().year
        emb = discord.Embed(
            title=f"⛳ PGA Tour {year} Season Standings",
            description="FedEx Cup Points",
            color=0x2E7D32,
        )
        emb.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/en/thumb/9/9e/PGA_Tour_logo.svg/1200px-PGA_Tour_logo.svg.png")

        medals = ["🥇","🥈","🥉"]
        for _, row in df.iterrows():
            rank  = int(row["rank"])
            name  = str(row["name"])
            pts   = str(row["fedex_pts"])
            earn  = str(row["earnings"])
            avg   = str(row["scoring_avg"])
            wins  = str(row["wins"])
            top10 = str(row["top10s"])

            prefix = medals[rank-1] if rank <= 3 else f"{rank}."
            val = f"**{pts} pts** - {earn}"
            if avg and avg != "-":
                val += f" - avg {avg}"
            if wins and wins not in ("0", "-"):
                val += f" - {wins}W"
            if top10 and top10 not in ("0", "-"):
                val += f" / {top10} top10s"

            emb.add_field(name=f"{prefix} {name}", value=val, inline=False)

        await _send(interaction, embed=emb)

    tree.add_command(pga_group)

    # ─────────────────────────────────────────────────────────────────────────
    # /ball  -  all sports today in one message
    # ─────────────────────────────────────────────────────────────────────────
    @tree.command(name="ball", description="sports happening today or tomorrow", guild=guild)
    @app_commands.describe(day="today (default) or tomorrow")
    @app_commands.choices(day=[
        app_commands.Choice(name="Today",    value="today"),
        app_commands.Choice(name="Tomorrow", value="tomorrow"),
    ])
    async def ball(interaction: discord.Interaction, day: str = "today"):
        await _defer(interaction)

        is_tomorrow = day == "tomorrow"
        day_label   = "Tomorrow" if is_tomorrow else "Today"

        # Run all schedule scripts concurrently
        if is_tomorrow:
            await asyncio.gather(
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "nhlTomorrow.py")),
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "mlbTomorrow.py")),
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "pgaLeaderboard.py")),
                asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nbaTomorrow.R")),
                asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nextNFL.R")),
            )
        else:
            await asyncio.gather(
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "nhlToday.py")),
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "mlbToday.py")),
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "pgaLeaderboard.py")),
                asyncio.to_thread(_run, PYTHON,    os.path.join(pp, "nbaToday.py")),
                asyncio.to_thread(_run, "Rscript", os.path.join(rp, "nextNFL.R")),
            )

        embeds = []

        # ── NBA ──────────────────────────────────────────────────────────────
        nba_csv = os.path.join(op, "sports/nba/gamesTomorrow.csv" if is_tomorrow else "sports/nba/gamesToday.csv")
        if os.path.exists(nba_csv):
            nba_df = pd.read_csv(nba_csv)
            if not nba_df.empty:
                emb = discord.Embed(title=f"🏀 NBA - {day_label}", color=0xC9082A)
                emb.set_thumbnail(url="https://a.espncdn.com/i/teamlogos/leagues/500/nba.png")
                for _, row in nba_df.iterrows():
                    matchup  = str(row["matchup"])
                    time_val = str(row["time"])
                    if not is_tomorrow and " - " in matchup:
                        name_part, score_part = matchup.rsplit(" - ", 1)
                        emb.add_field(name=name_part, value=f"🔴 {score_part}", inline=False)
                    else:
                        emb.add_field(name=matchup, value=time_val, inline=False)
                embeds.append(emb)

        # ── NHL ──────────────────────────────────────────────────────────────
        nhl_csv = os.path.join(op, "sports/nhl/gamesTomorrow.csv" if is_tomorrow else "sports/nhl/gamesToday.csv")
        if os.path.exists(nhl_csv):
            nhl_df = pd.read_csv(nhl_csv)
            if not nhl_df.empty:
                emb = discord.Embed(title=f"🏒 NHL - {day_label}", color=0x000000)
                emb.set_thumbnail(url="https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png")
                for _, row in nhl_df.iterrows():
                    emb.add_field(name=row["matchup"], value=str(row["time"]), inline=False)
                embeds.append(emb)

        # ── MLB ──────────────────────────────────────────────────────────────
        mlb_csv = os.path.join(op, "sports/mlb/gamesTomorrow.csv" if is_tomorrow else "sports/mlb/gamesToday.csv")
        if os.path.exists(mlb_csv):
            mlb_df = pd.read_csv(mlb_csv)
            if not mlb_df.empty:
                emb = discord.Embed(title=f"⚾ MLB - {day_label}", color=0x002D72)
                emb.set_thumbnail(url="https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png")
                for _, row in mlb_df.iterrows():
                    matchup  = str(row["matchup"])
                    time_val = str(row["time"])
                    if not is_tomorrow and "LIVE" in time_val:
                        emb.add_field(name=matchup, value=f"🔴 {time_val}", inline=False)
                    elif not is_tomorrow and "Final" in time_val:
                        emb.add_field(name=matchup, value=f"✅ {time_val}", inline=False)
                    else:
                        emb.add_field(name=matchup, value=time_val, inline=False)
                embeds.append(emb)

        # ── PGA ──────────────────────────────────────────────────────────────
        tourn_csv = os.path.join(op, "sports/pga/tournament.csv")
        lb_csv    = os.path.join(op, "sports/pga/leaderboard.csv")
        if os.path.exists(tourn_csv) and os.path.exists(lb_csv):
            t  = pd.read_csv(tourn_csv).iloc[0]
            lb = pd.read_csv(lb_csv)
            if t.get("name") and str(t.get("name","")).strip().lower() not in ("", "nan"):
                status_line = str(t.get("detail") or t.get("status") or "").strip()
                emb = discord.Embed(
                    title=f"⛳ {t['name']}",
                    description=f"*{status_line}*" if status_line else "",
                    color=0x2E7D32,
                )
                # only show leaderboard if there are real scores
                real_scores = lb[lb["score"].notna() & (lb["score"].astype(str) != "-") & (lb["score"].astype(str) != "–")] if not lb.empty else pd.DataFrame()
                if not real_scores.empty:
                    for _, row in real_scores.head(5).iterrows():
                        pos   = str(row.get("position", "")).strip()
                        name  = str(row.get("name", "-"))
                        score = str(row.get("score", "-"))
                        today = str(row.get("today", "")).strip()
                        thru  = str(row.get("thru", "")).strip()
                        label = pos if pos and pos not in ("-", "–", "nan") else ""
                        val_parts = [f"**{score}**"]
                        if today and today not in ("-", "–", "nan", ""):
                            val_parts.append(f"today: {today}")
                        if thru and thru not in ("-", "–", "nan", ""):
                            val_parts.append(f"thru {thru}")
                        emb.add_field(name=f"{label} {name}".strip(), value="  ".join(val_parts), inline=True)
                else:
                    # pre-tournament or no scores yet
                    emb.add_field(name="📅 Tee times", value="Tournament starts soon - no scores yet", inline=False)
                embeds.append(emb)

        # ── NFL ──────────────────────────────────────────────────────────────
        nfl_csv = os.path.join(op, "sports/nfl/nextGame.csv")
        if os.path.exists(nfl_csv):
            nfl_df = pd.read_csv(nfl_csv)
            if not nfl_df.empty:
                row = nfl_df.iloc[0]
                try:
                    days_until = int(row["daysUntil"])
                except (ValueError, KeyError):
                    days_until = 99
                target_days = 1 if is_tomorrow else 0
                if days_until == target_days:
                    away, home = row["away_team"], row["home_team"]
                    emb = discord.Embed(title=f"🏈 NFL - {day_label}", color=0x013369)
                    emb.set_thumbnail(url="https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png")
                    emb.add_field(
                        name=f"{away} @ {home}",
                        value=f"{row['gametime']}  -  {row['stadium']}",
                        inline=False,
                    )
                    embeds.append(emb)

        if not embeds:
            await _send(interaction, f"nothing going on in the sports world {day_label.lower()} 😔")
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

    @osrs_group.command(name="hiscores", description="crew hiscores leaderboard - total level or any skill")
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
            await _send(interaction, f"😔 couldn't find `{rsn}` on the hiscores - are they ranked?", ephemeral=True)
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
    # JAXCAMS  /jaxcams
    # ─────────────────────────────────────────────────────────────────────────
    @tree.command(name="jaxcams", description="Live JAX traffic cams from FL511", guild=guild)
    @app_commands.describe(
        group="Which area to show (default: random)",
        mode="grid = 2x2 composite (default) | single = one full-res shot",
        camera="Search for a specific camera by name (e.g. 'acosta' or 'beach blvd')",
    )
    @app_commands.choices(
        group=[
            app_commands.Choice(name="Random",              value="random"),
            app_commands.Choice(name="I-95",                value="i95"),
            app_commands.Choice(name="I-10",                value="i10"),
            app_commands.Choice(name="I-295",               value="i295"),
            app_commands.Choice(name="JTB / Butler Blvd",  value="jtb"),
            app_commands.Choice(name="Beaches",             value="beaches"),
            app_commands.Choice(name="Bridges",             value="bridges"),
            app_commands.Choice(name="Downtown",            value="downtown"),
            app_commands.Choice(name="Northside",           value="northside"),
            app_commands.Choice(name="Southside",           value="southside"),
            app_commands.Choice(name="Westside",            value="westside"),
        ],
        mode=[
            app_commands.Choice(name="Grid  - 2x2 composite (default)", value="grid"),
            app_commands.Choice(name="Single - one camera, full res",   value="single"),
        ],
    )
    async def jaxcams(interaction: discord.Interaction,
                      group:  app_commands.Choice[str] = None,
                      mode:   app_commands.Choice[str] = None,
                      camera: str = ""):
        await _defer(interaction)

        group_val  = group.value if group else "random"
        mode_val   = mode.value  if mode  else "grid"
        camera_val = camera.strip()

        cmd = [PYTHON, os.path.join(pp, "jaxS.py"), group_val, mode_val]
        if camera_val:
            cmd.append(camera_val)

        result = await asyncio.to_thread(
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        )

        # handle search-specific exit codes
        if camera_val and result.returncode == 2:
            stdout = result.stdout.strip()
            if stdout.startswith("NO_MATCH"):
                await _send(interaction,
                    f"❌ No camera matched **\"{camera_val}\"**\n"
                    f"Try a road name or landmark, e.g. `beach blvd`, `dames point`, `acosta`, `atlantic`, `university`",
                    ephemeral=True)
                return
            elif stdout.startswith("TOO_MANY"):
                lines     = stdout.split("\n")
                count     = lines[0].split(":")[1]
                cam_names = "\n".join(l.strip() for l in lines[1:] if l.strip())
                await _send(interaction,
                    f"📷 **{count} cameras** matched **\"{camera_val}\"** - be more specific:\n```{cam_names[:1800]}```",
                    ephemeral=True)
                return

        csv_path = os.path.join(op, "cams/cams.csv")
        if not os.path.exists(csv_path):
            await _send(interaction, "😔 couldn't reach FL511 right now", ephemeral=True)
            return

        df   = pd.read_csv(csv_path)
        good = df[df["ok"] == True]
        if good.empty:
            await _send(interaction, "📷 no cameras returned images - FL511 may be down", ephemeral=True)
            return

        # image path: for grid it's cam_grid.jpg, for single it's cam_0.jpg
        img_path = good.iloc[0]["path"]
        if not os.path.exists(img_path):
            await _send(interaction, "😔 image file missing after fetch", ephemeral=True)
            return

        group_label = (group.name if group else "Random")
        n_ok  = len(good)
        n_tot = len(df)
        ts    = pd.Timestamp.now().strftime("%I:%M %p")

        if mode_val == "single":
            row  = good.iloc[0]
            emb  = discord.Embed(
                title=f"🎥 JAX Cams - {group_label}",
                description=f"**{row['name']}**  ·  {ts}",
                color=0x005F9E,
            )
            emb.set_image(url="attachment://cam.jpg")
            emb.set_footer(text="source: FL511 / FDOT  ·  live feed")
            await _send(interaction, embed=emb, file=discord.File(img_path, filename="cam.jpg"))
        else:
            names = "  ·  ".join(good["name"].tolist()[:4])
            emb   = discord.Embed(
                title=f"🎥 JAX Cams - {group_label}  ({n_ok}/{n_tot})",
                description=f"{names}\n{ts}",
                color=0x005F9E,
            )
            emb.set_image(url="attachment://cam_grid.jpg")
            emb.set_footer(text="source: FL511 / FDOT  ·  live feed")
            await _send(interaction, embed=emb, file=discord.File(img_path, filename="cam_grid.jpg"))

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

    @markets_group.command(name="yieldspread", description="U.S. Treasury yields by maturity")
    @app_commands.describe(timeframe="how far back to show (default: full history)")
    @app_commands.choices(timeframe=[
        app_commands.Choice(name="1 Month",      value="30"),
        app_commands.Choice(name="2 Months",     value="60"),
        app_commands.Choice(name="3 Months",     value="90"),
        app_commands.Choice(name="6 Months",     value="180"),
        app_commands.Choice(name="1 Year",       value="365"),
        app_commands.Choice(name="2 Years",      value="730"),
        app_commands.Choice(name="5 Years",      value="1825"),
        app_commands.Choice(name="Full History", value="all"),
    ])
    async def yieldspread(interaction: discord.Interaction, timeframe: str = "all"):
        await _defer(interaction)
        tf = timeframe if timeframe else "all"
        args = ["Rscript", os.path.join(rp, "yieldSpread.R")]
        if tf != "all":
            args.append(tf)
        await asyncio.to_thread(_run, *args)
        # map value back to a readable label
        tf_labels = {"30":"1 Month","60":"2 Months","90":"3 Months","180":"6 Months",
                     "365":"1 Year","730":"2 Years","1825":"5 Years","all":"Full History"}
        label = tf_labels.get(tf, tf)
        await _send(interaction, f"yield spreads - {label}:",
                    file=discord.File(os.path.join(op, "markets/yield_spread.png")))


    @markets_group.command(name="crudeoil", description="west texas intermediate - cushing, oklahoma")
    async def crudeoil(interaction: discord.Interaction):
        await _defer(interaction)
        await asyncio.to_thread(_run, "Rscript", os.path.join(rp, "crude.R"))
        await _send(interaction, "full price history for crude oil wti @ cushing, oklahoma:",
                    file=discord.File(os.path.join(op, "markets/crudewti.png")))
        
    CHART_TIMEFRAME_CHOICES = [
    app_commands.Choice(name="Intraday - today 1min bars",  value="intraday"),
    app_commands.Choice(name="1 Week",                       value="1w"),
    app_commands.Choice(name="1 Month",                      value="1mo"),
    app_commands.Choice(name="3 Months",                     value="3mo"),
    app_commands.Choice(name="6 Months",                     value="6mo"),
    app_commands.Choice(name="1 Year",                       value="1y"),
    app_commands.Choice(name="2 Years",                      value="2y"),
    app_commands.Choice(name="5 Years",                      value="5y"),
    app_commands.Choice(name="10 Years",                     value="10y"),
    app_commands.Choice(name="Max",                          value="max"),
]

    @markets_group.command(name="chart", description="return stock price chart")
    @app_commands.describe(ticker="ticker symbol (e.g. SPY, AAPL)", timeframe="time window to display (default: 6mo)")
    @app_commands.choices(timeframe=CHART_TIMEFRAME_CHOICES)
    async def chart(interaction: discord.Interaction, ticker: str, timeframe: app_commands.Choice[str] = None):

        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 10:
            await interaction.response.send_message("Invalid ticker symbol.", ephemeral=True)
            return

        tf = timeframe.value if timeframe else "6mo"

        await interaction.response.defer()

        # Step 1: Fetch bar data from Alpaca
        fetch_result = subprocess.run(
            [PYTHON, os.path.join(pp, "fetchStockBars.py"), ticker, tf],
            capture_output=True,
            text=True
        )
        if fetch_result.returncode != 0:
            await interaction.followup.send(f"Error fetching data for **${ticker}**.\n```{fetch_result.stderr[-1500:]}```")
            print("FETCH ERROR:", fetch_result.stderr)
            return

        # Step 2: Generate chart
        result = subprocess.run(
            ["Rscript", os.path.join(rp, "stockChart.R"), ticker, tf],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            await interaction.followup.send(f"Error generating chart for **${ticker}**.")
            print(result.stderr)
            return

        output_path = os.path.join(op, "markets/stockchart.png")
        if os.path.exists(output_path):
            await interaction.followup.send(
                content=f"**${ticker}**",
                file=discord.File(output_path)
            )
        else:
            await interaction.followup.send("Chart file not found.")

    TIMEFRAME_CHOICES = [
    app_commands.Choice(name="Recent - last hour of trading",         value="recent"),
    app_commands.Choice(name="Close - last hour of market (3-4 PM)",  value="close"),
    app_commands.Choice(name="Open - first hour of market (9:30-10:30 AM)", value="open"),
    app_commands.Choice(name="Day - regular session (9:30 AM - 4 PM)", value="day"),
    app_commands.Choice(name="Full - pre-market through after-hours",  value="full"),
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
                content=f"**${ticker}** - {timeframe.name}",
                file=discord.File(output_path)
            )
        else:
            await interaction.followup.send("Chart file not found.")

    @markets_group.command(name="fear", description="CNN Fear & Greed Index")
    async def markets_fear(interaction: discord.Interaction):
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "marketFear.py")],
                capture_output=True, text=True, timeout=30
            )
        )
        csv_path = os.path.join(op, "markets/feargreed.csv")
        if result.returncode != 0 or not os.path.exists(csv_path):
            await _send(interaction, f"couldn't fetch Fear & Greed data :(", ephemeral=True)
            return
        df = pd.read_csv(csv_path)
        if df.empty:
            await _send(interaction, "no data returned :(", ephemeral=True)
            return
        row = df.iloc[0]
        score = float(row["score"])
        rating = str(row["rating"])
        RATING_EMOJI = {
            "Extreme Fear": "😱", "Fear": "😨", "Neutral": "😐",
            "Greed": "😏", "Extreme Greed": "🤑"
        }
        RATING_COLOR = {
            "Extreme Fear": 0xFF0000, "Fear": 0xFF6600, "Neutral": 0xFFFF00,
            "Greed": 0x66FF00, "Extreme Greed": 0x00CC00
        }
        emoji = RATING_EMOJI.get(rating, "📊")
        color = RATING_COLOR.get(rating, 0xFFFFFF)
        bar_filled = round(score / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        emb = discord.Embed(
            title=f"{emoji} CNN Fear & Greed Index",
            description=f"**{rating}**   `{bar}`   **{score:.0f} / 100**",
            color=color,
        )
        def _hist_field(score_val, rating_val):
            if pd.isna(score_val) or pd.isna(rating_val):
                return "-"
            e = RATING_EMOJI.get(str(rating_val), "")
            return f"{e} **{float(score_val):.0f}** - {rating_val}"
        emb.add_field(name="Previous Close", value=_hist_field(row.get("prev_close_score"), row.get("prev_close_rating")), inline=True)
        emb.add_field(name="1 Week Ago",     value=_hist_field(row.get("one_week_score"),   row.get("one_week_rating")),   inline=True)
        emb.add_field(name="1 Month Ago",    value=_hist_field(row.get("one_month_score"),  row.get("one_month_rating")),  inline=True)
        emb.set_footer(text="source: CNN Business")
        await _send(interaction, embed=emb)

    @markets_group.command(name="movers", description="Top 5 gainers and losers today")
    async def markets_movers(interaction: discord.Interaction):
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "marketMovers.py")],
                capture_output=True, text=True, timeout=60
            )
        )
        g_csv = os.path.join(op, "markets/gainers.csv")
        l_csv = os.path.join(op, "markets/losers.csv")
        if not os.path.exists(g_csv) or not os.path.exists(l_csv):
            await _send(interaction, "couldn't fetch movers data :(", ephemeral=True)
            return
        gainers = pd.read_csv(g_csv)
        losers  = pd.read_csv(l_csv)
        emb = discord.Embed(title="📈📉 Market Movers", color=0x2ECC71)
        def _movers_field(df):
            lines = []
            for _, r in df.iterrows():
                pct = float(r["change_pct"])
                arrow = "🟢" if pct >= 0 else "🔴"
                lines.append(f"{arrow} **${r['symbol']}** {pct:+.1f}%  `${float(r['price']):.2f}`  {str(r['name'])[:22]}")
            return "\n".join(lines) or "-"
        emb.add_field(name="🚀 Top Gainers", value=_movers_field(gainers), inline=False)
        emb.add_field(name="💀 Top Losers",  value=_movers_field(losers),  inline=False)
        emb.set_footer(text="source: Yahoo Finance · delayed ~15min")
        await _send(interaction, embed=emb)

    @markets_group.command(name="earnings", description="Upcoming earnings (7 days) or earnings history for a ticker")
    @app_commands.describe(ticker="optional: ticker to look up (e.g. AAPL). Leave blank for next 7 days.")
    async def markets_earnings(interaction: discord.Interaction, ticker: str = ""):
        await _defer(interaction)
        args = [PYTHON, os.path.join(pp, "marketEarnings.py")]
        if ticker.strip():
            args.append(ticker.upper().strip())
        result = await asyncio.to_thread(
            lambda: subprocess.run(args, capture_output=True, text=True, timeout=120)
        )
        if ticker.strip():
            csv_path = os.path.join(op, "markets/earnings_ticker.csv")
            if not os.path.exists(csv_path):
                await _send(interaction, f"couldn't fetch earnings for **${ticker.upper()}** :(", ephemeral=True)
                return
            df = pd.read_csv(csv_path)
            if df.empty:
                await _send(interaction, f"no earnings data found for **${ticker.upper()}**")
                return
            emb = discord.Embed(title=f"📅 Earnings - ${ticker.upper()}", color=0xF39C12)
            for _, row in df.iterrows():
                date_str = str(row["date"])[:10]
                est  = f"`{float(row['eps_estimate']):.2f}`" if pd.notna(row.get("eps_estimate")) and str(row.get("eps_estimate")) not in ("nan","") else "-"
                rep  = f"`{float(row['reported_eps']):.2f}`" if pd.notna(row.get("reported_eps")) and str(row.get("reported_eps")) not in ("nan","") else "-"
                surp = f"`{float(row['surprise_pct']):.1f}%`" if pd.notna(row.get("surprise_pct")) and str(row.get("surprise_pct")) not in ("nan","") else "-"
                emb.add_field(name=date_str, value=f"Est: {est}  Rep: {rep}  Surp: {surp}", inline=False)
            emb.set_footer(text="source: Yahoo Finance")
            await _send(interaction, embed=emb)
        else:
            csv_path = os.path.join(op, "markets/earnings_upcoming.csv")
            if not os.path.exists(csv_path):
                await _send(interaction, "couldn't fetch upcoming earnings :(", ephemeral=True)
                return
            df = pd.read_csv(csv_path)
            emb = discord.Embed(title="📅 Earnings - Next 7 Days", color=0xF39C12)
            if df.empty:
                emb.description = "No major earnings in the next 7 days"
            else:
                for _, row in df.iterrows():
                    date_str = str(row["date"])[:10]
                    est = f"`{float(row['eps_estimate']):.2f}`" if pd.notna(row.get("eps_estimate")) and str(row.get("eps_estimate")) not in ("nan","") else "-"
                    emb.add_field(name=f"**${row['ticker']}** - {row['company_name']}", value=f"{date_str}  Est EPS: {est}", inline=False)
            emb.set_footer(text="source: Yahoo Finance · major tickers only")
            await _send(interaction, embed=emb)

    @markets_group.command(name="options", description="Options chain for a ticker (top calls & puts by volume)")
    @app_commands.describe(ticker="ticker symbol (e.g. AAPL)", expiry="expiry date e.g. 2026-06-20 (default: nearest)")
    async def markets_options(interaction: discord.Interaction, ticker: str, expiry: str = ""):
        ticker = ticker.upper().strip()
        await _defer(interaction)
        args = [PYTHON, os.path.join(pp, "marketOptions.py"), ticker]
        if expiry.strip():
            args.append(expiry.strip())
        result = await asyncio.to_thread(
            lambda: subprocess.run(args, capture_output=True, text=True, timeout=60)
        )
        meta_csv  = os.path.join(op, "markets/options_meta.csv")
        calls_csv = os.path.join(op, "markets/options_calls.csv")
        puts_csv  = os.path.join(op, "markets/options_puts.csv")
        if not os.path.exists(meta_csv):
            await _send(interaction, f"couldn't fetch options for **${ticker}** - check the ticker or try again :(", ephemeral=True)
            return
        meta   = pd.read_csv(meta_csv).iloc[0]
        calls  = pd.read_csv(calls_csv)  if os.path.exists(calls_csv)  else pd.DataFrame()
        puts   = pd.read_csv(puts_csv)   if os.path.exists(puts_csv)   else pd.DataFrame()
        cur_px = float(meta["current_price"])
        emb = discord.Embed(
            title=f"⚙️ Options - ${ticker}",
            description=f"Expiry: **{meta['expiry_used']}**  ·  Current: **${cur_px:.2f}**  ·  Strikes ±15%",
            color=0x9B59B6,
        )
        def _opts_field(df, label):
            if df.empty:
                return "-"
            lines = []
            for _, r in df.head(8).iterrows():
                itm  = "✅" if str(r.get("itm","")).lower() == "true" else "  "
                vol  = int(r["volume"])  if pd.notna(r.get("volume"))  else 0
                oi   = int(r["oi"])      if pd.notna(r.get("oi"))      else 0
                iv   = float(r["iv"])    if pd.notna(r.get("iv"))      else 0
                bid  = float(r["bid"])   if pd.notna(r.get("bid"))     else 0
                ask  = float(r["ask"])   if pd.notna(r.get("ask"))     else 0
                lines.append(f"{itm}`${float(r['strike']):.1f}` b/a `{bid:.2f}/{ask:.2f}` vol `{vol:,}` oi `{oi:,}` iv `{iv:.0%}`")
            return "\n".join(lines)
        emb.add_field(name="📗 Calls (top by volume)", value=_opts_field(calls, "calls"), inline=False)
        emb.add_field(name="📕 Puts  (top by volume)", value=_opts_field(puts,  "puts"),  inline=False)
        emb.set_footer(text="✅ = in the money  ·  source: Yahoo Finance")
        await _send(interaction, embed=emb)

    @markets_group.command(name="short", description="Short interest & float data for a ticker")
    @app_commands.describe(ticker="ticker symbol (e.g. GME, TSLA)")
    async def markets_short(interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "marketShort.py"), ticker],
                capture_output=True, text=True, timeout=30
            )
        )
        csv_path = os.path.join(op, "markets/short.csv")
        if result.returncode != 0 or not os.path.exists(csv_path):
            await _send(interaction, f"couldn't fetch short data for **${ticker}** :(", ephemeral=True)
            return
        row = pd.read_csv(csv_path).iloc[0]
        sf  = float(row["short_float_pct"]) if pd.notna(row.get("short_float_pct")) else None
        dtc = float(row["days_to_cover"])   if pd.notna(row.get("days_to_cover"))   else None
        ss  = int(row["shares_short"])      if pd.notna(row.get("shares_short"))    else None
        sf_ = int(row["shares_float"])      if pd.notna(row.get("shares_float"))    else None
        px  = float(row["price"])           if pd.notna(row.get("price"))           else None
        vol = int(row["avg_volume"])        if pd.notna(row.get("avg_volume"))      else None
        ds  = str(row.get("date_short_interest", ""))
        # squeeze meter: simple color based on short float %
        if sf is not None:
            if sf >= 20:   color, meter = 0xFF0000, "🔥🔥🔥 High short interest"
            elif sf >= 10: color, meter = 0xFF6600, "🔥🔥 Elevated"
            elif sf >= 5:  color, meter = 0xFFFF00, "🔥 Moderate"
            else:          color, meter = 0x00CC00, "Normal"
        else:
            color, meter = 0x888888, "-"
        emb = discord.Embed(title=f"📊 Short Interest - ${ticker}", description=meter, color=color)
        emb.add_field(name="💲 Price",          value=f"${px:.2f}"      if px  else "-", inline=True)
        emb.add_field(name="📉 Short Float %",  value=f"{sf:.2f}%"      if sf  is not None else "-", inline=True)
        emb.add_field(name="📅 Days to Cover",  value=f"{dtc:.1f} days" if dtc is not None else "-", inline=True)
        emb.add_field(name="🔢 Shares Short",   value=f"{ss:,}"         if ss  else "-", inline=True)
        emb.add_field(name="🏊 Shares Float",   value=f"{sf_:,}"        if sf_ else "-", inline=True)
        emb.add_field(name="📊 Avg Volume",      value=f"{vol:,}"        if vol else "-", inline=True)
        if ds and ds not in ("nan", ""):
            emb.set_footer(text=f"Short interest as of: {ds[:10]}  ·  source: Yahoo Finance")
        else:
            emb.set_footer(text="source: Yahoo Finance")
        await _send(interaction, embed=emb)

    # ─────────────────────────────────────────────────────────────────────────
    # /markets forecast  - GARCH/SARIMA animated price forecast
    # ─────────────────────────────────────────────────────────────────────────

    @markets_group.command(name="forecast", description="Animated price/macro forecast - GJR-GARCH, EGARCH, SARIMA with Monte Carlo bands")
    @app_commands.describe(
        category="what to forecast",
        symbol="ticker, coin, or FRED series (e.g. AAPL, BTC, CPI, T10Y2Y)",
        timeframe="bar granularity for stocks/crypto (ignored for economic)",
        horizon="how far out to forecast",
        model="model to use (default: auto selects GJR-GARCH or EGARCH + Monte Carlo)",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Stocks",           value="stocks"),
            app_commands.Choice(name="Crypto",           value="crypto"),
            app_commands.Choice(name="Economic (FRED)",  value="economic"),
        ],
        timeframe=[
            app_commands.Choice(name="1 Minute",   value="1min"),
            app_commands.Choice(name="5 Minutes",  value="5min"),
            app_commands.Choice(name="15 Minutes", value="15min"),
            app_commands.Choice(name="1 Hour",     value="1h"),
            app_commands.Choice(name="Daily",      value="1d"),
        ],
        horizon=[
            app_commands.Choice(name="1 Hour",    value="1h"),
            app_commands.Choice(name="4 Hours",   value="4h"),
            app_commands.Choice(name="1 Day",     value="1d"),
            app_commands.Choice(name="1 Week",    value="1w"),
            app_commands.Choice(name="1 Month",   value="1mo"),
            app_commands.Choice(name="3 Months",  value="3mo"),
            app_commands.Choice(name="6 Months",  value="6mo"),
            app_commands.Choice(name="1 Year",    value="1yr"),
        ],
        model=[
            app_commands.Choice(name="Auto (GJR-GARCH / EGARCH + Monte Carlo)", value="auto"),
            app_commands.Choice(name="NNETAR (Neural Network AR - no MC)",       value="nnetar"),
        ],
    )
    async def markets_forecast(
        interaction: discord.Interaction,
        category: str,
        symbol: str,
        timeframe: str = "1d",
        horizon: str = "3mo",
        model: str = "auto",
    ):
        symbol = symbol.upper().strip()
        await _defer(interaction)

        # ── step 1: fetch data ──────────────────────────────────────────────
        await interaction.followup.send(
            f"📊 fetching data for **{symbol}** ({category}) - this may take a minute...",
            ephemeral=True
        )

        fetch_result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "fetchForecastData.py"),
                 category, symbol, timeframe, horizon, model],
                capture_output=True, text=True, timeout=120
            )
        )

        if fetch_result.returncode != 0:
            err = fetch_result.stderr[-1500:] if fetch_result.stderr else "unknown error"
            await interaction.followup.send(
                f"error fetching data for **{symbol}**:\n```{err}```"
            )
            return

        # parse metadata from stdout
        import json as _json
        meta_line = fetch_result.stdout.strip().splitlines()[-1] if fetch_result.stdout.strip() else ""
        try:
            meta = _json.loads(meta_line)
        except Exception:
            await interaction.followup.send(
                f"error parsing data metadata for **{symbol}**\n```{fetch_result.stdout[-800:]}```"
            )
            return

        csv_path_out = meta.get("out_path", "")
        if not csv_path_out or not os.path.exists(csv_path_out):
            await interaction.followup.send(f"data file not found for **{symbol}** :(")
            return

        n_bars      = meta.get("n_bars") or meta.get("n_obs", 0)
        last_price  = meta.get("last_close") or meta.get("last_value", 0)
        model_used  = meta.get("model", "unknown")
        mc_sims     = meta.get("mc_sims", 500)
        horizon_bars = meta.get("horizon_bars", 20)
        series_name = meta.get("series_name", symbol)
        display_sym = series_name if category == "economic" else symbol

        safe_sym    = symbol.replace("/", "")
        gif_out     = os.path.join(op, f"markets/forecast_{safe_sym}_{timeframe}_{horizon}.gif")
        os.makedirs(os.path.dirname(gif_out), exist_ok=True)

        # ── step 2: run R ───────────────────────────────────────────────────
        await interaction.followup.send(
            f"🔢 fitting **{model_used}** {'+ running ' + str(mc_sims) + ' Monte Carlo simulations' if model != 'nnetar' else '(NNETAR - no MC)'} - hang tight...",
            ephemeral=True
        )

        r_result = await asyncio.to_thread(
            lambda: subprocess.run(
                ["Rscript", os.path.join(rp, "marketForecast.R"),
                 csv_path_out, category, symbol, str(horizon_bars),
                 model_used, str(mc_sims), gif_out, display_sym],
                capture_output=True, text=True, timeout=600
            )
        )

        if r_result.returncode != 0:
            err = r_result.stderr[-2000:] if r_result.stderr else r_result.stdout[-2000:]
            await interaction.followup.send(
                f"R error while generating forecast for **{symbol}**:\n```{err}```"
            )
            return

        # parse R output JSON
        r_out_json = {}
        for line in r_result.stdout.splitlines():
            if line.startswith("OUTPUT_JSON:"):
                try:
                    r_out_json = _json.loads(line[len("OUTPUT_JSON:"):])
                except Exception:
                    pass
                break

        # ── step 3: build embed + send GIFs ────────────────────────────────
        if category == "stocks":
            model_desc = "GJR-GARCH(1,1) w/ student-t innovations"
            color      = 0x00BFFF
            emoji      = "📈"
            price_label = "Last Close"
        elif category == "crypto":
            model_desc = "EGARCH(1,1) w/ student-t innovations"
            color      = 0xF7931A
            emoji      = "🪙"
            price_label = "Last Price"
        else:
            model_desc = f"auto.ARIMA (SARIMA) - {r_out_json.get('model_str', 'auto')}"
            color      = 0x9B59B6
            emoji      = "📉"
            price_label = "Last Value"

        emb = discord.Embed(
            title=f"{emoji} {display_sym} - Forecast ({horizon})",
            description=f"**Model:** {model_desc}\n**Bars used:** {n_bars:,}  |  **Forecast steps:** {horizon_bars}",
            color=color,
        )

        if category in ("stocks", "crypto"):
            p50  = r_out_json.get("p50_end")
            p10  = r_out_json.get("p10_end")
            p90  = r_out_json.get("p90_end")
            p25  = r_out_json.get("p25_end")
            p75  = r_out_json.get("p75_end")
            mu   = r_out_json.get("mu_ret")
            sig  = r_out_json.get("sig_ret")
            kurt = r_out_json.get("kurt")

            emb.add_field(name=price_label,       value=f"`${last_price:,.4f}`",    inline=True)
            emb.add_field(name="Median Forecast",  value=f"`${p50:,.4f}`" if p50 else "-", inline=True)
            emb.add_field(name="\u200b",           value="\u200b",                  inline=True)
            emb.add_field(name="10th / 90th pct",  value=f"`${p10:,.2f}` / `${p90:,.2f}`" if p10 and p90 else "-", inline=False)
            emb.add_field(name="25th / 75th pct",  value=f"`${p25:,.2f}` / `${p75:,.2f}`" if p25 and p75 else "-", inline=False)
            if mu is not None and sig is not None:
                emb.add_field(name="Daily log return - mu / sigma",
                              value=f"`{mu:.6f}` / `{sig:.6f}`", inline=False)
            if kurt is not None:
                excess_k = kurt
                emb.add_field(name="Excess kurtosis",
                              value=f"`{excess_k:.3f}` {'- fat tails confirmed' if excess_k > 1 else ''}",
                              inline=False)
        else:
            last_v = r_out_json.get("last_value")
            mean_e = r_out_json.get("mean_end")
            lo80   = r_out_json.get("lo80_end")
            hi80   = r_out_json.get("hi80_end")
            emb.add_field(name="Last Value",       value=f"`{last_v:,.4f}`"  if last_v else "-", inline=True)
            emb.add_field(name="Forecast (mean)",  value=f"`{mean_e:,.4f}`"  if mean_e else "-", inline=True)
            emb.add_field(name="\u200b",           value="\u200b",           inline=True)
            emb.add_field(name="80% CI",           value=f"`{lo80:,.4f}` to `{hi80:,.4f}`" if lo80 and hi80 else "-", inline=False)

        emb.set_footer(text=f"source: {'Alpaca Markets' if category != 'economic' else 'FRED / St. Louis Fed'} | not financial advice")

        # send GIF 1 (MC paths or rolling for economic)
        gif1 = r_out_json.get("gif1", "")
        gif2 = r_out_json.get("gif2", "")
        dist_png = r_out_json.get("distpng") or r_out_json.get("staticpng", "")

        files_to_send = []
        if gif1 and os.path.exists(gif1):
            files_to_send.append(discord.File(gif1, filename=os.path.basename(gif1)))
        if gif2 and gif2 != gif1 and os.path.exists(gif2):
            files_to_send.append(discord.File(gif2, filename=os.path.basename(gif2)))

        if files_to_send:
            await interaction.followup.send(embed=emb, files=files_to_send[:2])
        else:
            await interaction.followup.send(embed=emb)

        # distribution PNG generated but not sent - available locally if needed

    tree.add_command(markets_group)

    # ─────────────────────────────────────────────────────────────────────────
    # /markets crypto  (moved under markets group)
    # ─────────────────────────────────────────────────────────────────────────
    CRYPTO_TIMEFRAME_CHOICES = [
        app_commands.Choice(name="Intraday - today 1min bars",  value="intraday"),
        app_commands.Choice(name="1 Week",                       value="1w"),
        app_commands.Choice(name="1 Month",                      value="1mo"),
        app_commands.Choice(name="3 Months",                     value="3mo"),
        app_commands.Choice(name="6 Months",                     value="6mo"),
        app_commands.Choice(name="1 Year",                       value="1y"),
        app_commands.Choice(name="2 Years",                      value="2y"),
        app_commands.Choice(name="5 Years",                      value="5y"),
        app_commands.Choice(name="10 Years",                     value="10y"),
        app_commands.Choice(name="Max",                          value="max"),
    ]

    @markets_group.command(name="crypto", description="Crypto price chart - BTC, ETH, SOL, DOGE")
    @app_commands.describe(coin="which coin", timeframe="time window (default: 6mo)")
    @app_commands.choices(
        coin=[
            app_commands.Choice(name="Bitcoin  (BTC)",  value="BTC"),
            app_commands.Choice(name="Ethereum (ETH)",  value="ETH"),
            app_commands.Choice(name="Solana   (SOL)",  value="SOL"),
            app_commands.Choice(name="Dogecoin (DOGE)", value="DOGE"),
        ],
        timeframe=CRYPTO_TIMEFRAME_CHOICES,
    )
    async def markets_crypto(interaction: discord.Interaction,
                             coin: app_commands.Choice[str],
                             timeframe: app_commands.Choice[str] = None):
        symbol = coin.value
        tf     = timeframe.value if timeframe else "6mo"
        await interaction.response.defer()
        fetch_result = subprocess.run(
            [PYTHON, os.path.join(pp, "fetchCryptoBars.py"), symbol, tf],
            capture_output=True, text=True
        )
        if fetch_result.returncode != 0:
            await interaction.followup.send(f"Error fetching data for **{symbol}**.\n```{fetch_result.stderr[-1500:]}```")
            return
        chart_result = subprocess.run(
            ["Rscript", os.path.join(rp, "cryptoChart.R"), symbol, tf],
            capture_output=True, text=True
        )
        if chart_result.returncode != 0:
            await interaction.followup.send(f"Error generating chart for **{symbol}**.\n```{chart_result.stderr[-1500:]}```")
            return
        out = os.path.join(op, "markets/cryptochart.png")
        if os.path.exists(out):
            await interaction.followup.send(content=f"**{symbol}/USD**", file=discord.File(out))
        else:
            await interaction.followup.send("Chart file not found.")

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
            description=f"**{circuit}** - {locality}, {country}",
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
            title=f"⚾ MLB Games - {today_str}",
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
                    f"{away_w}**{away}** - {away_score}\n"
                    f"{home_w}**{home}** - {home_score}\n"
                    f"*Final • {venue}*"
                )
            elif state == "Live":
                away_score = int(row["teams_away_score"])
                home_score = int(row["teams_home_score"])
                value = (
                    f"🔴 **{away}** - {away_score}\n"
                    f"🔴 **{home}** - {home_score}\n"
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
            title=f"⚾ MLB Games - {tomorrow_str}",
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

    @mlb_group.command(name="gmscore", description="World Sillies GM report - grades every manager's decisions last week")
    async def mlb_gmscore(interaction: discord.Interaction):
        await _defer(interaction)

        proc = await asyncio.create_subprocess_exec(
            PYTHON, os.path.join(pp, "worldSilliesGMScore.py"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            await _send(interaction, "⏱️ GM score timed out - try again in a moment", ephemeral=True)
            return

        import json as _json
        gm_path = os.path.join(op, "sports/mlb/fantasy/gm_scores.json")
        if not os.path.exists(gm_path):
            err = stderr.decode()[-400:] if stderr else "no output"
            await _send(interaction, f"❌ GM score failed\n```{err}```", ephemeral=True)
            return

        with open(gm_path) as f:
            data = _json.load(f)

        teams  = data.get("teams", [])
        period = data.get("matchup_period", "?")

        if not teams:
            await _send(interaction, "❌ no GM score data found", ephemeral=True)
            return

        grade_colors = {
            "A+": 0x00C853, "A": 0x00C853, "A-": 0x64DD17,
            "B+": 0xAEEA00, "B": 0xC6FF00, "B-": 0xFFFF00,
            "C+": 0xFFD600, "C": 0xFFAB00, "C-": 0xFF6D00,
            "D+": 0xFF3D00, "D": 0xDD2C00, "F": 0xB71C1C,
        }
        # use dock ellis color for embed
        dock = next((t for t in teams if "dock" in t["team_name"].lower()), None)
        emb_color = grade_colors.get(dock["grade"], 0x002D72) if dock else 0x002D72

        emb = discord.Embed(
            title=f"⚾ World Sillies GM Report - Week {period}",
            description="Grades based on start/sit decisions, roster quality, weekly performance, and waiver smarts",
            color=emb_color,
        )

        grade_emoji = {
            "A+": "🏆", "A": "🌟", "A-": "✅",
            "B+": "👍", "B": "👍", "B-": "👍",
            "C+": "➡️", "C": "➡️", "C-": "➡️",
            "D+": "⚠️", "D": "⚠️", "F": "💀",
        }

        for i, t in enumerate(teams):
            rank    = i + 1
            name    = t["team_name"]
            grade   = t["grade"]
            score   = t["total_score"]
            win     = t["win"]
            pts     = t["weekly_pts"]
            opp     = t["opp_name"]
            opp_pts = t["opp_score"]
            margin  = t["margin"]
            top_p   = t.get("top_starter")
            top_pts = t.get("top_starter_pts", 0)
            bench_p = t.get("best_bench")
            bench_pts_val = t.get("best_bench_pts", 0)
            mistakes = t.get("mistakes", [])
            ss      = t["ss_score"]
            rs      = t["roster_score"]
            wl      = t["wl_score"]

            emoji   = grade_emoji.get(grade, "➡️")
            wl_str  = f"W +{margin:.0f}" if win else f"L {margin:.0f}"
            rank_medal = ["🥇","🥈","🥉"][i] if i < 3 else f"{rank}."

            lines = [f"**{pts:.0f} pts** vs {opp} ({opp_pts:.0f}) - {wl_str}"]
            lines.append(f"`S/S {ss:.0f}` `Roster {rs:.0f}` `W/L {wl:.0f}`")

            if top_p:
                lines.append(f"⭐ best start: {top_p} ({top_pts:.0f} pts)")
            if mistakes:
                m = mistakes[0]
                lines.append(f"❌ left {m['name']} on bench ({m['pts']:.0f} pts)")

            emb.add_field(
                name=f"{rank_medal} {emoji} **{grade}** ({score})  {name}",
                value="\n".join(lines),
                inline=False,
            )

        emb.set_footer(text="S/S = start-sit accuracy  Roster = roster quality  W/L = weekly performance")
        await _send(interaction, embed=emb)

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

        # team names for autocomplete - update if your league changes
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
                value="\n".join(fmt_player(r) for r in active) or "-",
                inline=False,
            )
        if bench:
            embed.add_field(
                name="🪑 Bench",
                value="\n".join(fmt_player(r) for r in bench) or "-",
                inline=False,
            )
        if il:
            embed.add_field(
                name="🏥 IL",
                value="\n".join(fmt_player(r) for r in il) or "-",
                inline=False,
            )
 
        embed.set_footer(text="Data via ESPN Fantasy API")
        await _send(interaction, embed=embed)

    @mlb_group.command(name="fantasyfa", description="World Sillies top 10 free agents by position")
    @app_commands.describe(position="player position to look up")
    @app_commands.choices(position=[
        app_commands.Choice(name="PT - Probable Pitchers Today",    value="PT"),
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

        # for PT (probable today), filter FA list to only today's starters
        if position == "PT":
            import unicodedata
            def _norm(s): return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").strip().lower()
            prob_csv = os.path.join(op, "sports/mlb/probableStartersToday.csv")
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbProbPitchers.py"), "today")
            if os.path.exists(prob_csv):
                with open(prob_csv, newline="") as f:
                    today_names = {_norm(r["pitcher_name"]) for r in _csv.DictReader(f) if r.get("pitcher_name")}
                rows = [r for r in rows if _norm(r.get("player_name", "")) in today_names]

        if not rows:
            await _send(interaction, f"no free agents found at position **{position}**.", ephemeral=True)
            return

        POSITION_EMOJI = {
            "SP": "🤾", "RP": "🔥", "C": "🎯",
            "1B": "1️⃣", "2B": "2️⃣", "3B": "3️⃣",
            "SS": "⚡", "OF": "🌿",
        }
        emoji = POSITION_EMOJI.get(position, "⚾")
        is_pitcher = position in ("SP", "RP", "PP", "PT")
        is_pp      = position == "PP"
        is_pt      = position == "PT"

        embed = discord.Embed(
            title=f"⚾ World Sillies - {'Probable Pitcher' if is_pp or is_pt else f'Top {position}'} Free Agents",
            description=("Starting today · Sorted by % owned" if is_pt else "Starting tomorrow · Sorted by % owned" if is_pp else "Sorted by % owned · Top 10 available" + (" · ⚾ = starting tomorrow" if is_pitcher else "")),
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
    
    @mlb_group.command(name="mismatch", description="Top batter/pitcher mismatches for today's or tomorrow's games")
    @app_commands.describe(
        mode="Which mismatches to show (default: both)",
        day="Today's or tomorrow's games (default: tomorrow)"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Both (Top 5 each)",       value="both"),
            app_commands.Choice(name="Top 10 Batter Favored",   value="batters"),
            app_commands.Choice(name="Top 10 Pitcher Favored",  value="pitchers"),
        ],
        day=[
            app_commands.Choice(name="Tomorrow", value="tomorrow"),
            app_commands.Choice(name="Today",    value="today"),
        ]
    )
    async def mlb_mismatch(interaction: discord.Interaction, mode: str = "both", day: str = "tomorrow"):
        await _defer(interaction)
        print(f"[DEBUG] /mlb mismatch called with mode='{mode}' day='{day}'")

        csv_path = os.path.join(op, "sports/mlb/mismatchToday.csv" if day == "today" else "sports/mlb/mismatch.csv")

        # Only regenerate if CSV is missing or more than 3 hours old
        needs_refresh = True
        if os.path.exists(csv_path):
            age_hours = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(csv_path))).total_seconds() / 3600
            if age_hours < 3:
                needs_refresh = False
                print(f"[DEBUG] Using cached CSV ({age_hours:.1f}h old)")

        if needs_refresh:
            print("[DEBUG] Regenerating mismatch data...")
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbProbPitchers.py"), day)
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbMismatch.py"), day)

        try:
            df = pd.read_csv(csv_path)
        except FileNotFoundError:
            await _send(interaction, "❌ Could not generate mismatch data. Check the logs.")
            return

        if df.empty:
            await _send(interaction, "No matchup data found with sufficient PA history (min. 5 PA).")
            return

        date_str = datetime.today().strftime("%B %d, %Y") if day == "today" else (datetime.today() + timedelta(days=1)).strftime("%B %d, %Y")

        embed = discord.Embed(
            title=f"⚾ Pitcher/Batter Mismatches - {date_str}",
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
                    name=f"{base_name}",
                    value="".join(formatter(row) for _, row in rows.iloc[:half].iterrows()).strip(),
                    inline=False
                )
                embed.add_field(
                    name="\u200b",
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

    # ── /mlb fantasyrisk ──────────────────────────────────────────────────────
    @mlb_group.command(name="fantasyrisk", description="Fantasy risk analysis plot for a position (World Sillies scoring)")
    @app_commands.describe(
        position="Position to analyze",
        scope="All players at this position, or free agents only"
    )
    @app_commands.choices(
        position=[
            app_commands.Choice(name="SP - Starting Pitcher", value="SP"),
            app_commands.Choice(name="RP - Relief Pitcher",   value="RP"),
            app_commands.Choice(name="C  - Catcher",          value="C"),
            app_commands.Choice(name="1B - First Base",       value="1B"),
            app_commands.Choice(name="2B - Second Base",      value="2B"),
            app_commands.Choice(name="3B - Third Base",       value="3B"),
            app_commands.Choice(name="SS - Shortstop",        value="SS"),
            app_commands.Choice(name="OF - Outfield",         value="OF"),
        ],
        scope=[
            app_commands.Choice(name="All Players",            value="all"),
            app_commands.Choice(name="Free Agents Only",       value="fa"),
            app_commands.Choice(name="Today's SP Starters",    value="today_sp"),
            app_commands.Choice(name="Tomorrow's SP Starters", value="tomorrow_sp"),
        ]
    )
    async def mlb_fantasyrisk(interaction: discord.Interaction, position: str, scope: str = "all"):
        await _defer(interaction)

        out_img  = os.path.join(op, "sports/mlb/fantasy/fantasyRisk.png")
        fa_csv   = os.path.join(op, "sports/mlb/fantasy/freeagents.csv")
        os.makedirs(os.path.dirname(out_img), exist_ok=True)

        # if FA only, refresh the free agents CSV first then extract names
        fa_names_arg = "ALL"
        if scope == "fa":
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesFA.py"), position)
            if not os.path.exists(fa_csv):
                await _send(interaction, "❌ couldn't fetch free agent data.", ephemeral=True)
                return
            import csv as _csv
            with open(fa_csv, newline="") as f:
                fa_names = [r["player_name"] for r in _csv.DictReader(f) if r.get("player_name")]
            if not fa_names:
                await _send(interaction, f"no free agents found at **{position}**.", ephemeral=True)
                return
            fa_names_arg = "|".join(fa_names)

        elif scope in ("today_sp", "tomorrow_sp"):
            which = "today" if scope == "today_sp" else "tomorrow"
            # refresh FA list for SP
            await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "worldSilliesFA.py"), "SP")
            if not os.path.exists(fa_csv):
                await _send(interaction, "❌ couldn't fetch free agent data.", ephemeral=True)
                return
            import csv as _csv
            with open(fa_csv, newline="") as f:
                fa_rows = [r for r in _csv.DictReader(f) if r.get("player_name")]

            if scope == "tomorrow_sp":
                # FA CSV already has starting_tomorrow flag - no extra API call needed
                sp_names = [r["player_name"] for r in fa_rows if r.get("starting_tomorrow") == "True"]
            else:
                # fetch today's starters and intersect with FA list
                prob_csv = os.path.join(op, "sports/mlb/probableStartersToday.csv")
                await asyncio.to_thread(_run, PYTHON, os.path.join(pp, "mlbProbPitchers.py"), "today")
                if not os.path.exists(prob_csv):
                    await _send(interaction, "❌ couldn't fetch today's probable starters.", ephemeral=True)
                    return
                import unicodedata
                def _norm(s): return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").strip().lower()
                with open(prob_csv, newline="") as f:
                    today_starters = {r["pitcher_name"] for r in _csv.DictReader(f) if r.get("pitcher_name")}
                fa_norm = {_norm(r["player_name"]): r["player_name"] for r in fa_rows}
                sp_names = [fa_norm[_norm(n)] for n in today_starters if _norm(n) in fa_norm]

            if not sp_names:
                await _send(interaction, f"no FA probable starters found for {which}.", ephemeral=True)
                return
            fa_names_arg = "|".join(sp_names)

        await asyncio.to_thread(
            _run, "Rscript", os.path.join(rp, "mlbFantasyRiskPlotte.R"), position, out_img, fa_names_arg
        )

        if not os.path.exists(out_img):
            await _send(interaction, f"❌ couldn't generate the risk plot for **{position}** - check the R logs.", ephemeral=True)
            return

        scope_label = "Free Agents Only" if scope == "fa" else "Today's SP Starters" if scope == "today_sp" else "Tomorrow's SP Starters" if scope == "tomorrow_sp" else "All Players"
        await _send(
            interaction,
            f"📊 fantasy risk - **{position}** · {scope_label}",
            file=discord.File(out_img),
        )

    @mlb_group.command(name="playertrends", description="Last 7 games vs season average for a player")
    @app_commands.describe(player="Player name (e.g. 'Paul Skenes', 'Ronald Acuna')")
    async def mlb_playertrends(interaction: discord.Interaction, player: str):
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "mlbPlayerTrends.py"), player],
                capture_output=True, text=True, timeout=30
            )
        )
        csv_path = os.path.join(op, "sports/mlb/fantasy/playerTrends.csv")
        if result.returncode != 0 or not os.path.exists(csv_path):
            msg = result.stdout.strip() if result.stdout.strip() else f"couldn't find data for **{player}** - check spelling"
            await _send(interaction, f"❌ {msg}", ephemeral=True)
            return
        df = pd.read_csv(csv_path)
        if df.empty:
            await _send(interaction, f"no data found for **{player}**", ephemeral=True)
            return
        row = df.iloc[0]
        ptype      = str(row["player_type"])
        pname      = str(row["player_name"])
        avg_s      = float(row["avg_pts_season"]) if pd.notna(row.get("avg_pts_season")) else 0
        avg_7      = float(row["avg_pts_last7"])  if pd.notna(row.get("avg_pts_last7"))  else 0
        trend_pct  = float(row["pts_trend_pct"])  if pd.notna(row.get("pts_trend_pct"))  else 0
        games_tot  = int(row["games_total"])       if pd.notna(row.get("games_total"))    else 0
        games_7    = int(row["games_last7"])       if pd.notna(row.get("games_last7"))    else 0
        trend_arrow = "🔥" if trend_pct >= 15 else ("❄️" if trend_pct <= -15 else "➡️")
        trend_str   = f"{trend_arrow} **{trend_pct:+.1f}%** vs season avg"
        color = 0xFF4500 if trend_pct >= 15 else (0x1E90FF if trend_pct <= -15 else 0x888888)
        emb = discord.Embed(
            title=f"⚾ {pname} - Fantasy Trends",
            description=trend_str,
            color=color,
        )
        emb.add_field(name="📅 Last 7 Avg Pts",  value=f"**{avg_7:.1f}**",  inline=True)
        emb.add_field(name="📈 Season Avg Pts",  value=f"**{avg_s:.1f}**",  inline=True)
        emb.add_field(name="🎮 Games (7 / total)", value=f"{games_7} / {games_tot}", inline=True)
        if ptype == "batter":
            hr7  = row.get("hr_last7");   rbi7  = row.get("rbi_last7");  h7   = row.get("h_last7");   woba7 = row.get("woba_last7")
            hrs  = row.get("hr_season");  rbis  = row.get("rbi_season"); wobas = row.get("woba_season")
            emb.add_field(name="🔟 Last 7",   value=f"HR: {int(hr7) if pd.notna(hr7) else '-'}  RBI: {int(rbi7) if pd.notna(rbi7) else '-'}  H: {int(h7) if pd.notna(h7) else '-'}  wOBA: {float(woba7):.3f if pd.notna(woba7) else '-'}", inline=False)
            emb.add_field(name="📊 Season",   value=f"HR: {int(hrs) if pd.notna(hrs) else '-'}  RBI: {int(rbis) if pd.notna(rbis) else '-'}  wOBA: {float(wobas):.3f if pd.notna(wobas) else '-'}", inline=False)
        else:
            k7   = row.get("k_last7");   w7   = row.get("w_last7");   era7  = row.get("era_last7")
            ks   = row.get("k_season");  ws   = row.get("w_season");  eras  = row.get("era_season")
            emb.add_field(name="🔟 Last 7",  value=f"K: {int(k7) if pd.notna(k7) else '-'}  W: {int(w7) if pd.notna(w7) else '-'}  ERA: {float(era7):.2f if pd.notna(era7) else '-'}", inline=False)
            emb.add_field(name="📊 Season",  value=f"K: {int(ks) if pd.notna(ks) else '-'}  W: {int(ws) if pd.notna(ws) else '-'}  ERA: {float(eras):.2f if pd.notna(eras) else '-'}", inline=False)
        emb.set_footer(text="World Sillies scoring · data via Baseball Savant")
        await _send(interaction, embed=emb)

    @mlb_group.command(name="hotcold", description="Who's hot and who's cold in the last 7 days")
    async def mlb_hotcold(interaction: discord.Interaction):
        await _defer(interaction)
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "mlbHotCold.py")],
                capture_output=True, text=True, timeout=60
            )
        )
        base = os.path.join(op, "sports/mlb/fantasy")
        def _load(fname):
            p = os.path.join(base, fname)
            return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
        hot_b  = _load("hotBatters.csv")
        cold_b = _load("coldBatters.csv")
        hot_p  = _load("hotPitchers.csv")
        cold_p = _load("coldPitchers.csv")
        if all(df.empty for df in [hot_b, cold_b, hot_p, cold_p]):
            await _send(interaction, "couldn't generate hot/cold data :(", ephemeral=True)
            return
        def _fmt_row(r):
            trend = float(r["trend_pct"]) if pd.notna(r.get("trend_pct")) else 0
            arrow = f"{trend:+.0f}%"
            return f"**{r['player_name']}** ({r['team']})  `{float(r['avg_pts_last7']):.1f} pts/g`  {arrow}"
        emb = discord.Embed(title="⚾ World Sillies - Hot & Cold Last 7 Days", color=0xFF4500)
        if not hot_b.empty:
            emb.add_field(name="🔥 Hot Bats",   value="\n".join(_fmt_row(r) for _, r in hot_b.iterrows()),  inline=False)
        if not cold_b.empty:
            emb.add_field(name="❄️ Cold Bats",  value="\n".join(_fmt_row(r) for _, r in cold_b.iterrows()), inline=False)
        if not hot_p.empty:
            emb.add_field(name="🔥 Hot Arms",   value="\n".join(_fmt_row(r) for _, r in hot_p.iterrows()),  inline=False)
        if not cold_p.empty:
            emb.add_field(name="❄️ Cold Arms",  value="\n".join(_fmt_row(r) for _, r in cold_p.iterrows()), inline=False)
        emb.set_footer(text="Min 5 games total · 3 games in last 7 · World Sillies scoring")
        await _send(interaction, embed=emb)

    @mlb_group.command(name="zonemap", description="Pitch location plot by pitch type for any pitcher")
    @app_commands.describe(
        pitcher="pitcher name",
        window="time window for pitch data (default: season)",
    )
    @app_commands.choices(window=[
        app_commands.Choice(name="Full season",  value="season"),
        app_commands.Choice(name="Last 30 days", value="last30"),
        app_commands.Choice(name="Last 7 days",  value="last7"),
    ])
    async def mlb_zonemap(interaction: discord.Interaction,
                          pitcher: str,
                          window:  str = "season"):
        await _defer(interaction)

        proc = await asyncio.create_subprocess_exec(
            PYTHON, os.path.join(pp, "pitchZoneMap.py"), pitcher, window,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            await _send(interaction, "⏱️ pitch data fetch timed out - Baseball Savant may be slow, try again", ephemeral=True)
            return

        out_text = stdout.decode().strip()
        if proc.returncode != 0 or "ok:" not in out_text:
            err = stderr.decode()[-400:] if stderr else out_text[-400:]
            await _send(interaction, f"❌ couldn't generate zone map\n```{err}```", ephemeral=True)
            return

        # parse output path from "ok: /path/to/file.png"
        img_path = out_text.split("ok:")[-1].strip()
        if not os.path.exists(img_path):
            await _send(interaction, "❌ image file not found after generation", ephemeral=True)
            return

        window_labels = {"season": f"{CURRENT_YEAR} season", "last30": "last 30 days", "last7": "last 7 days"}
        await _send(interaction,
            content=f"⚾ **{pitcher}** pitch locations - {window_labels.get(window, window)}",
            file=discord.File(img_path, filename="zonemap.png")
        )

    @mlb_group.command(name="lineup", description="Daily start/sit card for dock ellis fan club")
    @app_commands.describe(day="Today's games (default) or tomorrow's")
    @app_commands.choices(day=[
        app_commands.Choice(name="Today",    value="today"),
        app_commands.Choice(name="Tomorrow", value="tomorrow"),
    ])
    async def mlb_lineup(interaction: discord.Interaction, day: str = "today"):
        await _defer(interaction)

        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [PYTHON, os.path.join(pp, "mlbLineup.py"), day],
                capture_output=True, text=True, timeout=240
            )
        )

        import json as _json
        lineup_path = os.path.join(op, "sports/mlb/fantasy/lineup.json")
        if not os.path.exists(lineup_path):
            await _send(interaction, "❌ couldn't generate lineup card", ephemeral=True)
            return

        with open(lineup_path) as f:
            data = _json.load(f)

        day_label = "Today" if day == "today" else "Tomorrow"

        SIGNAL_ORDER = {"START ✅": 0, "WATCH 👀": 1, "SIT ❌": 2, "BENCHED": 3}

        def fmt_player(p, show_matchup=True):
            sig   = p.get("signal", "-")
            name  = p["name"]
            slot  = p["slot"]
            score = p.get("score", 0)
            r_avg = p.get("recent_avg")
            trend = p.get("trend_pct")
            inj   = p.get("injury_status","")

            # injury badge
            inj_badge = ""
            if inj not in ("ACTIVE","NORMAL",""):
                inj_badge = " 🤕"

            # trend arrow
            if trend is not None:
                arrow = "🔥" if trend >= 15 else ("❄️" if trend <= -15 else "➡️")
                trend_str = f"{arrow} `{r_avg:.1f}` pts/g"
            else:
                trend_str = "`-`"

            # matchup snippet
            matchup_str = ""
            if show_matchup:
                ops = p.get("matchup_ops")
                pa  = p.get("matchup_pa")
                pit = p.get("opponent_pitcher") or p.get("opponent")
                if ops is not None and pa and pa >= 5:
                    opp_str = f"vs {pit[:18]}" if pit else "matchup"
                    matchup_str = f"  ·  `{ops:.3f}` OPS/{pa}PA {opp_str}"
                elif pit:
                    matchup_str = f"  ·  vs {pit[:22]}"

            return f"{sig}{inj_badge} **{name}** `{slot}`  {trend_str}{matchup_str}"

        # ── batter embed ──────────────────────────────────────────────────────
        batters = sorted(data.get("batters", []), key=lambda x: SIGNAL_ORDER.get(x.get("signal",""), 9))
        emb_b = discord.Embed(
            title=f"⚾ dock ellis fan club - {day_label} Lineup",
            color=0x002D72,
        )
        if batters:
            emb_b.add_field(
                name="🏏 Batters",
                value="\n".join(fmt_player(p) for p in batters) or "-",
                inline=False,
            )

        # ── pitcher embed ─────────────────────────────────────────────────────
        pitchers = sorted(data.get("pitchers", []), key=lambda x: SIGNAL_ORDER.get(x.get("signal",""), 9))
        if pitchers:
            emb_b.add_field(
                name="⚾ Pitchers",
                value="\n".join(fmt_player(p, show_matchup=False) for p in pitchers) or "-",
                inline=False,
            )

        # ── bench / IL ────────────────────────────────────────────────────────
        bench = data.get("bench", [])
        if bench:
            bench_lines = []
            for p in bench:
                inj = p.get("injury_status","")
                badge = " 🤕" if inj not in ("ACTIVE","NORMAL","") else ""
                bench_lines.append(f"`{p['slot']}` **{p['name']}**{badge}")
            emb_b.add_field(
                name="🪑 Bench / IL",
                value="  ·  ".join(bench_lines) or "-",
                inline=False,
            )

        emb_b.set_footer(text="START ✅ ≥65  ·  WATCH 👀 42-64  ·  SIT ❌ <42  ·  trend = last 7 games  ·  World Sillies scoring")
        await _send(interaction, embed=emb_b)

    @mlb_group.command(name="compare", description="Sabermetric comparison of up to 4 players - stream & roster value")
    @app_commands.describe(
        player1="first player",
        player2="second player",
        player3="third player (optional)",
        player4="fourth player (optional)",
        day="today or tomorrow (default: today)",
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="Today",    value="today"),
        app_commands.Choice(name="Tomorrow", value="tomorrow"),
    ])
    async def mlb_compare(interaction: discord.Interaction,
                          player1: str,
                          player2: str,
                          player3: str = "",
                          player4: str = "",
                          day:     str = "today"):
        await _defer(interaction)

        names = [p.strip() for p in [player1, player2, player3, player4] if p.strip()]

        # Use asyncio subprocess with hard 45s timeout - subprocess.run inside
        # to_thread can't be killed if a socket hangs inside the child process.
        try:
            proc = await asyncio.create_subprocess_exec(
                PYTHON, os.path.join(pp, "mlbCompare.py"), *names, day,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                await _send(interaction, "⏱️ comparison timed out - Baseball stats API may be slow, try again in a moment", ephemeral=True)
                return
        except Exception as e:
            await _send(interaction, f"❌ failed to run comparison: {e}", ephemeral=True)
            return

        import json as _json
        cmp_path = os.path.join(op, "sports/mlb/fantasy/compare.json")
        if not os.path.exists(cmp_path):
            err = stderr.decode()[-500:] if stderr else "no output"
            await _send(interaction, f"❌ couldn't run comparison\n```{err}```", ephemeral=True)
            return

        with open(cmp_path) as f:
            data = _json.load(f)

        players  = data.get("players", [])
        day_label = "Today" if day == "today" else "Tomorrow"

        if not players:
            await _send(interaction, "❌ no results returned - check player name spelling", ephemeral=True)
            return

        # ── stream ranking embed ──────────────────────────────────────────────
        emb = discord.Embed(
            title=f"⚾ Player Comparison - {day_label}",
            description=f"Ranked by stream score - {len(players)} players - {CURRENT_YEAR} season",
            color=0x002D72,
        )

        for i, p in enumerate(players):
            name    = p["name"]
            team    = p.get("team","?")
            pos     = p.get("pos","?")
            ss      = p["stream_score"]
            rs      = p["roster_score"]
            s_sig   = p["stream_signal"]
            r_sig   = p["roster_signal"]
            s_reas  = p.get("stream_reasons", [])
            r_reas  = p.get("roster_reasons", [])
            err     = p.get("error")

            if err:
                emb.add_field(
                    name=f"{'🥇🥈🥉4️⃣'[min(i,3)]} {p['input_name']}",
                    value=f"❓ {err}",
                    inline=False,
                )
                continue

            medal = ["🥇","🥈","🥉","4️⃣"][min(i, 3)]

            # stat line - use whatever keys are present
            if not p.get("is_pitcher"):
                ops   = p.get("season_ops")
                iso   = p.get("season_iso")
                obp   = p.get("season_obp")
                r_ops = p.get("recent_ops")
                parts = []
                if ops:   parts.append(f"`OPS {ops:.3f}`")
                if iso:   parts.append(f"`ISO {iso:.3f}`")
                if obp:   parts.append(f"`OBP {obp:.3f}`")
                if r_ops: parts.append(f"`L14 OPS {r_ops:.3f}`")
                stat_line = "  ".join(parts)
            else:
                era  = p.get("season_era")
                whip = p.get("season_whip")
                k9   = p.get("season_k9")
                r_era = p.get("recent_era")
                parts = []
                if era:   parts.append(f"`ERA {era:.2f}`")
                if whip:  parts.append(f"`WHIP {whip:.2f}`")
                if k9:    parts.append(f"`K/9 {k9:.1f}`")
                if r_era: parts.append(f"`L14 ERA {r_era:.2f}`")
                stat_line = "  ".join(parts)

            stream_block = "\n".join(f"  · {r}" for r in s_reas[:3]) if s_reas else "  · no data"
            roster_block = "\n".join(f"  · {r}" for r in r_reas[:3]) if r_reas else "  · no data"

            val = (
                f"**{team}  ·  {pos}**\n"
                f"{stat_line or '-'}\n"
                f"**Stream `{ss}`** - {s_sig}\n{stream_block}\n"
                f"**Roster `{rs}`** - {r_sig}\n{roster_block}"
            )

            emb.add_field(
                name=f"{medal} {name}",
                value=val,
                inline=False,
            )

        emb.set_footer(text=f"Stream = start value for {day_label}  ·  Roster = long-term add value  ·  data: Baseball Savant")
        await _send(interaction, embed=emb)

    @mlb_group.command(name="pickup", description="top free agent pickups for World Sillies - sabermetric ranked")
    @app_commands.describe(
        day="score pickups for today or tomorrow (default: today)",
        focus="batters only, pitchers only, or all (default: all)",
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="Today",    value="today"),
        app_commands.Choice(name="Tomorrow", value="tomorrow"),
    ])
    @app_commands.choices(focus=[
        app_commands.Choice(name="All",      value="all"),
        app_commands.Choice(name="Batters",  value="batters"),
        app_commands.Choice(name="Pitchers", value="pitchers"),
    ])
    async def mlb_pickup(interaction: discord.Interaction,
                         day:   str = "today",
                         focus: str = "all"):
        await _defer(interaction)

        # refresh FA list first (covers all positions)
        positions = ["C","1B","2B","3B","SS","OF","SP","RP"] if focus in ("all","batters","pitchers") else []
        batter_pos  = ["C","1B","2B","3B","SS","OF"]
        pitcher_pos = ["SP","RP"]

        fetch_pos = []
        if focus in ("all", "batters"):
            fetch_pos += batter_pos
        if focus in ("all", "pitchers"):
            fetch_pos += pitcher_pos

        # run worldSilliesFA.py for each position group concurrently
        import tempfile, json as _json

        fa_combined_path = os.path.join(op, "sports/mlb/fantasy/freeagents_all.csv")

        async def _fetch_all_fas():
            import pandas as _pd
            frames = []
            for pos in fetch_pos:
                try:
                    r = await asyncio.to_thread(
                        lambda p=pos: subprocess.run(
                            [PYTHON, os.path.join(pp, "worldSilliesFA.py"), p, "30"],
                            capture_output=True, text=True, timeout=30
                        )
                    )
                    fa_csv = os.path.join(op, "sports/mlb/fantasy/freeagents.csv")
                    if os.path.exists(fa_csv):
                        frames.append(_pd.read_csv(fa_csv))
                except Exception:
                    pass
            if frames:
                combined = _pd.concat(frames).drop_duplicates(subset=["player_name"])
                combined.to_csv(fa_combined_path, index=False)
            return os.path.exists(fa_combined_path)

        await _fetch_all_fas()

        # now run the pickup scorer
        proc = await asyncio.create_subprocess_exec(
            PYTHON, os.path.join(pp, "mlbPickup.py"), day, focus,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            await _send(interaction, "⏱️ pickup scan timed out - try again in a moment", ephemeral=True)
            return

        pickup_path = os.path.join(op, "sports/mlb/fantasy/pickup.json")
        if not os.path.exists(pickup_path):
            await _send(interaction, "❌ pickup scan failed - no output produced", ephemeral=True)
            return

        with open(pickup_path) as f:
            data = _json.load(f)

        day_label    = "Today" if day == "today" else "Tomorrow"
        top_batters  = data.get("top_batters",  [])[:3]
        top_pitchers = data.get("top_pitchers", [])[:3]
        total_scored = data.get("total_scored", 0)

        if not top_batters and not top_pitchers:
            await _send(interaction, "⚾ no free agents found to score - check that freeagents.csv is populated")
            return

        emb = discord.Embed(
            title=f"⚾ World Sillies Pickup Recommendations - {day_label}",
            description=f"Sabermetric scan of {total_scored} free agents - top adds ranked by stream + roster value",
            color=0x002D72,
        )

        medals = ["🥇", "🥈", "🥉"]

        if top_batters:
            emb.add_field(name="─── 🏏 Top Batter Adds ───", value="\u200b", inline=False)
            for i, p in enumerate(top_batters):
                ss   = p["stream_score"]
                rs   = p["roster_score"]
                name = p["name"]
                team = p.get("team", "")
                pos  = p.get("pos", "")
                pct  = p.get("pct_owned", 0)
                ops_s  = p.get("season_ops")
                ops_r  = p.get("recent_ops")
                s_sig  = p["stream_signal"]
                r_sig  = p["roster_signal"]
                reasons = p.get("stream_reasons", [])

                stat_line = ""
                if ops_s:
                    stat_line += f"`OPS {ops_s:.3f}`"
                if ops_r:
                    stat_line += f"  `L14 {ops_r:.3f}`"
                iso = p.get("season_iso")
                if iso:
                    stat_line += f"  `ISO {iso:.3f}`"

                reason_block = "\n".join(f"  · {r}" for r in reasons) if reasons else "  · no recent data"

                val = (
                    f"**{team}  ·  {pos}**  ·  {pct:.0f}% owned\n"
                    f"{stat_line or '-'}\n"
                    f"**Stream `{ss}`** - {s_sig}\n"
                    f"**Roster `{rs}`** - {r_sig}\n"
                    f"{reason_block}"
                )
                emb.add_field(name=f"{medals[i]} {name}", value=val, inline=False)

        if top_pitchers:
            emb.add_field(name="─── 🎯 Top Pitcher Adds ───", value="\u200b", inline=False)
            for i, p in enumerate(top_pitchers):
                ss   = p["stream_score"]
                rs   = p["roster_score"]
                name = p["name"]
                team = p.get("team", "")
                pos  = p.get("pos", "")
                pct  = p.get("pct_owned", 0)
                era_s  = p.get("season_era")
                whip_s = p.get("season_whip")
                k9     = p.get("season_k9")
                opp    = p.get("opponent")
                starting = p.get("starting", False)
                s_sig  = p["stream_signal"]
                r_sig  = p["roster_signal"]
                reasons = p.get("stream_reasons", [])

                stat_line = ""
                if era_s:
                    stat_line += f"`ERA {era_s:.2f}`"
                if whip_s:
                    stat_line += f"  `WHIP {whip_s:.2f}`"
                if k9:
                    stat_line += f"  `K/9 {k9:.1f}`"

                start_tag = f"  🟢 starts {day_label.lower()}" if starting else ""
                opp_tag   = f" vs {opp}" if opp else ""
                reason_block = "\n".join(f"  · {r}" for r in reasons) if reasons else "  · no recent data"

                val = (
                    f"**{team}  ·  {pos}**  ·  {pct:.0f}% owned{start_tag}{opp_tag}\n"
                    f"{stat_line or '-'}\n"
                    f"**Stream `{ss}`** - {s_sig}\n"
                    f"**Roster `{rs}`** - {r_sig}\n"
                    f"{reason_block}"
                )
                emb.add_field(name=f"{medals[i]} {name}", value=val, inline=False)

        emb.set_footer(text=f"World Sillies league  ·  {CURRENT_YEAR} season stats  ·  stream = start value {day_label.lower()}")
        await _send(interaction, embed=emb)

    tree.add_command(mlb_group)

    # ── /dj ───────────────────────────────────────────────────────────────────
    dj_group = app_commands.Group(name="dj", description="let DJ several bots spin a few tunes", guild_ids=[guild.id])

    @dj_group.command(name="join", description="Bot joins your voice channel")
    async def dj_join(interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await _quick(interaction, "you gotta be in a voice channel first", ephemeral=True)
            return
        await _defer(interaction)
        channel = interaction.user.voice.channel
        state = _dj_state.setdefault(interaction.guild_id, {"vc": None, "queue": [], "now_playing": None})
        vc = state.get("vc")
        try:
            if vc and vc.is_connected():
                if vc.channel.id == channel.id:
                    await _send(interaction, f"already in **{channel.name}**", ephemeral=True)
                    return
                await vc.move_to(channel)
            else:
                state["vc"] = await channel.connect(timeout=30.0, reconnect=False, self_deaf=False, self_mute=False)
        except Exception as e:
            print(f"[DJ] connect error: {type(e).__name__}: {e}")
            await _send(interaction, f"failed to join: {e}", ephemeral=True)
            return
        print(f"[DJ] joined {channel.name}")
        await _send(interaction, f"joined **{channel.name}** - use `/dj genre`, `/dj playlist`, or `/dj mix`")

    @dj_group.command(name="leave", description="Bot leaves the voice channel and clears queue")
    async def dj_leave(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "not in a voice channel", ephemeral=True)
            return
        await vc.disconnect()
        _dj_state.pop(interaction.guild_id, None)
        await _quick(interaction, "later")

    # ── /dj genre ─────────────────────────────────────────────────────────────
    @dj_group.command(name="genre", description="Queue all tracks for a genre, shuffled")
    @app_commands.describe(name="Genre name - start typing to search")
    async def dj_genre(interaction: discord.Interaction, name: str):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "use `/dj join` first", ephemeral=True)
            return
        await _defer(interaction)
        genres = await asyncio.to_thread(_dj_genres)
        # case-insensitive match
        match = next((g for g in genres if g.lower() == name.lower()), None)
        if not match:
            await _send(interaction, f"genre **{name}** not found - available: {', '.join(sorted(genres))}", ephemeral=True)
            return
        tracks = genres[match].copy()
        random.shuffle(tracks)
        state["queue"].extend(tracks)
        was_idle = not vc.is_playing() and state["now_playing"] is None
        if was_idle:
            await _send(interaction, f"playing **{match}** - {len(tracks)} tracks queued")
            await _dj_advance(interaction.guild_id)
        else:
            await _send(interaction, f"added {len(tracks)} **{match}** tracks to queue")

    @dj_genre.autocomplete("name")
    async def dj_genre_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        genres = await asyncio.to_thread(_dj_genres)
        matches = [g for g in sorted(genres) if current.lower() in g.lower()][:25]
        return [app_commands.Choice(name=g, value=g) for g in matches]

    # ── /dj artist ────────────────────────────────────────────────────────────
    @dj_group.command(name="artist", description="Queue all tracks by an artist, shuffled")
    @app_commands.describe(name="Artist name - start typing to search")
    async def dj_artist(interaction: discord.Interaction, name: str):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "use `/dj join` first", ephemeral=True)
            return
        await _defer(interaction)
        artists = await asyncio.to_thread(_dj_artists)
        # exact match first, then case-insensitive
        match = next((a for a in artists if a == name), None) or \
                next((a for a in artists if a.lower() == name.lower()), None)
        if not match:
            await _send(interaction, f"artist **{name}** not found - start typing to see suggestions", ephemeral=True)
            return
        tracks = artists[match].copy()
        random.shuffle(tracks)
        state["queue"].extend(tracks)
        was_idle = not vc.is_playing() and state["now_playing"] is None
        if was_idle:
            await _send(interaction, f"playing **{match}** - {len(tracks)} track{'s' if len(tracks) != 1 else ''} queued")
            await _dj_advance(interaction.guild_id)
        else:
            await _send(interaction, f"added {len(tracks)} track{'s' if len(tracks) != 1 else ''} by **{match}** to queue")

    @dj_artist.autocomplete("name")
    async def dj_artist_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        artists = await asyncio.to_thread(_dj_artists)
        matches = [a for a in sorted(artists) if current.lower() in a.lower()][:25]
        return [app_commands.Choice(name=f"{a} ({len(artists[a])} track{'s' if len(artists[a]) != 1 else ''})"[:100], value=a) for a in matches]

    # ── /dj playlist ──────────────────────────────────────────────────────────
    @dj_group.command(name="playlist", description="Queue a rekordbox playlist in order")
    @app_commands.describe(name="Playlist name - start typing to search")
    async def dj_playlist(interaction: discord.Interaction, name: str):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "use `/dj join` first", ephemeral=True)
            return
        if not _DJ_XML.exists():
            await _quick(interaction, "no rekordbox.xml found - export your collection from rekordbox (File → Export Collection in xml format) and place it at `outputs/dj/rekordbox.xml`", ephemeral=True)
            return
        await _defer(interaction)
        playlists = await asyncio.to_thread(_dj_playlists)
        match = next((p for p in playlists if p.lower() == name.lower()), None)
        if not match:
            names = ', '.join(sorted(playlists)) if playlists else "none found"
            await _send(interaction, f"playlist **{name}** not found - available: {names}", ephemeral=True)
            return
        tracks = playlists[match]
        state["queue"].extend(tracks)
        was_idle = not vc.is_playing() and state["now_playing"] is None
        if was_idle:
            await _send(interaction, f"playing playlist **{match}** - {len(tracks)} tracks")
            await _dj_advance(interaction.guild_id)
        else:
            await _send(interaction, f"added {len(tracks)} tracks from **{match}** to queue")

    @dj_playlist.autocomplete("name")
    async def dj_playlist_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not _DJ_XML.exists():
            return []
        playlists = await asyncio.to_thread(_dj_playlists)
        matches = [p for p in sorted(playlists) if current.lower() in p.lower()][:25]
        return [app_commands.Choice(name=p, value=p) for p in matches]

    # ── /dj mix ───────────────────────────────────────────────────────────────
    @dj_group.command(name="mix", description="Play a prerecorded DJ set")
    @app_commands.describe(name="Mix name - start typing to search")
    async def dj_mix(interaction: discord.Interaction, name: str):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "use `/dj join` first", ephemeral=True)
            return
        mixes = _dj_mixes()
        match = next((m for m in mixes if m.stem.lower() == name.lower() or m.name.lower() == name.lower()), None)
        if not match:
            stems = ', '.join(m.stem for m in mixes) if mixes else "none found"
            await _quick(interaction, f"mix **{name}** not found - available: {stems}", ephemeral=True)
            return
        state["queue"].insert(0, match)
        state["queue"] = [match] + [t for t in state["queue"] if t != match][1:]
        was_idle = not vc.is_playing() and state["now_playing"] is None
        if was_idle:
            await _quick(interaction, f"playing mix **{match.stem}**")
            await _dj_advance(interaction.guild_id)
        else:
            # stop current and play mix immediately
            state["queue"] = [match]
            state["now_playing"] = None
            vc.stop()
            await _quick(interaction, f"switching to mix **{match.stem}**")

    @dj_mix.autocomplete("name")
    async def dj_mix_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        mixes = _dj_mixes()
        matches = [m for m in mixes if current.lower() in m.stem.lower()][:25]
        return [app_commands.Choice(name=m.stem[:100], value=m.stem) for m in matches]

    @dj_group.command(name="skip", description="Skip the current track")
    async def dj_skip(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected() or not vc.is_playing():
            await _quick(interaction, "nothing is playing", ephemeral=True)
            return
        current = state.get("now_playing")
        vc.stop()  # triggers the after callback which advances the queue
        label = current.stem if current else "track"
        await _quick(interaction, f"skipped **{label}**")

    @dj_group.command(name="pause", description="Pause playback - queue stays intact")
    async def dj_pause(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "not in a voice channel", ephemeral=True)
            return
        if vc.is_paused():
            await _quick(interaction, "already paused - use `/dj play` to resume", ephemeral=True)
            return
        if not vc.is_playing():
            await _quick(interaction, "nothing is playing", ephemeral=True)
            return
        vc.pause()
        now = state.get("now_playing")
        label = now.stem if now else "track"
        await _quick(interaction, f"paused **{label}**")

    @dj_group.command(name="play", description="Resume after a pause")
    async def dj_play(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "not in a voice channel", ephemeral=True)
            return
        if vc.is_paused():
            vc.resume()
            now = state.get("now_playing")
            label = now.stem if now else "track"
            await _quick(interaction, f"resumed **{label}**")
        elif not vc.is_playing() and state and state.get("queue"):
            # nothing playing but queue has tracks - kick it off
            await _quick(interaction, "starting queue")
            await _dj_advance(interaction.guild_id)
        else:
            await _quick(interaction, "nothing is paused", ephemeral=True)

    @dj_group.command(name="stop", description="Stop playing and clear the queue (stays in channel)")
    async def dj_stop(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "not in a voice channel", ephemeral=True)
            return
        state["queue"].clear()
        state["now_playing"] = None
        if vc.is_playing():
            vc.stop()
        await _quick(interaction, "stopped and queue cleared")

    @dj_group.command(name="nowplaying", description="Show the current track")
    async def dj_nowplaying(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        now = state.get("now_playing") if state else None
        if not now:
            await _quick(interaction, "nothing is playing right now")
        else:
            await _quick(interaction, f"now playing: **{now.stem}**")

    @dj_group.command(name="queue", description="Show the current queue")
    async def dj_queue(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        if not state:
            await _quick(interaction, "nothing queued", ephemeral=True)
            return
        now = state.get("now_playing")
        queue: list[Path] = state.get("queue", [])
        lines = []
        if now:
            lines.append(f"**now playing:** {now.stem}")
        if queue:
            lines.append(f"**up next ({len(queue)} tracks):**")
            lines += [f"{i+1}. {t.stem}" for i, t in enumerate(queue[:20])]
            if len(queue) > 20:
                lines.append(f"*...and {len(queue) - 20} more*")
        if not lines:
            await _quick(interaction, "queue is empty", ephemeral=True)
        else:
            await _quick(interaction, "\n".join(lines))

    @dj_group.command(name="backspin", description="Restart the current track from the beginning")
    async def dj_backspin(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "not in a voice channel", ephemeral=True)
            return
        current = state.get("now_playing")
        if not current:
            await _quick(interaction, "nothing is playing", ephemeral=True)
            return
        # re-insert current track at front of queue so _after callback replays it
        state["queue"].insert(0, current)
        state["now_playing"] = None
        vc.stop()
        await _quick(interaction, f"rewinding **{current.stem}**")

    @dj_group.command(name="yoink", description="Grab the currently playing track as a file")
    async def dj_yoink(interaction: discord.Interaction):
        state = _dj_state.get(interaction.guild_id)
        current: Path | None = state.get("now_playing") if state else None
        if not current:
            await _quick(interaction, "nothing is playing", ephemeral=True)
            return
        await _defer(interaction)
        size_mb = current.stat().st_size / (1024 * 1024)
        if size_mb > 25:
            await _send(interaction, f"**{current.stem}** is {size_mb:.1f}MB - too big for Discord (25MB limit)", ephemeral=True)
            return
        await _send(interaction, f"here ya go - **{current.stem}**", file=discord.File(str(current)))

    # ── /dj mic ───────────────────────────────────────────────────────────────
    @dj_group.command(name="mic", description="Say something over the music via robot text-to-speech")
    @app_commands.describe(message="What to say")
    async def dj_mic(interaction: discord.Interaction, message: str):
        import tempfile

        state = _dj_state.get(interaction.guild_id)
        vc = state.get("vc") if state else None
        if not vc or not vc.is_connected():
            await _quick(interaction, "use `/dj join` first", ephemeral=True)
            return

        await _defer(interaction)

        tts_path = Path(tempfile.mktemp(suffix=".wav"))
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["espeak-ng", "-w", str(tts_path), message],
                check=True, timeout=10
            )
        except FileNotFoundError:
            await _send(interaction, "espeak-ng not installed - run `sudo apt install espeak-ng` on the Pi", ephemeral=True)
            return
        except Exception as e:
            await _send(interaction, f"TTS failed: {e}", ephemeral=True)
            return

        # Re-insert current track at front so music resumes after TTS
        current = state.get("now_playing")
        if current:
            state["queue"].insert(0, current)
            state["now_playing"] = None

        # Stop music - _after fires but _dj_advance will see vc.is_playing()=True
        # (from the TTS we're about to start) and bail out
        if vc.is_playing() or vc.is_paused():
            vc.stop()

        loop = asyncio.get_running_loop()
        source = discord.FFmpegPCMAudio(str(tts_path))

        def after_tts(err):
            tts_path.unlink(missing_ok=True)
            asyncio.run_coroutine_threadsafe(_dj_advance(interaction.guild_id), loop)

        vc.play(source, after=after_tts)
        await _send(interaction, f"🎤 *\"{message}\"*")

    tree.add_command(dj_group)

    # ── /jaxships ─────────────────────────────────────────────────────────────
    @tree.command(name="jaxships", description="ships in and around Jacksonville / JAXPORT right now", guild=guild)
    async def jaxships(interaction: discord.Interaction):
        await _defer(interaction)
        mst_key = os.environ.get("MYSHIPTRACKING_KEY", "")
        if not mst_key:
            await _send(interaction, "⚠️ MYSHIPTRACKING_KEY env var not set - grab a free trial key at https://myshiptracking.com", ephemeral=True)
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