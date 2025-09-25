# pip install pandas python-dotenv discord.py
import os
from pathlib import Path
from datetime import datetime, timezone

import subprocess
import pandas as pd
import discord
from discord import app_commands
from dotenv import load_dotenv, find_dotenv

# ---------- env & client ----------
load_dotenv(find_dotenv(usecwd=True))
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
if not TOKEN:
    raise SystemExit("Set DISCORD_TOKEN in your .env")
if not GUILD_ID:
    raise SystemExit("Set GUILD_ID in your .env")

intents = discord.Intents.default()              # slash cmds don't need message_content
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
GUILD = discord.Object(id=GUILD_ID)

# ---------- data / formatting ----------
RSCRIPT = Path("~/discordBot/r/room40leaderboard.R").expanduser()
try:
    subprocess.run(["Rscript", str(RSCRIPT)], check=False, timeout=90)
except Exception as e:
    print(f"[boot] Rscript error: {e}")

CSV_PATH = Path("~/discordBot/outputs/sports/nfl/room40leaderboard.csv").expanduser()

print(f"[boot] commands.py path: {__file__}")
print(f"[boot] cwd: {os.getcwd()}")
print(f"[boot] CSV_PATH: {CSV_PATH}")
print(f"[boot] R script: {RSCRIPT}")

def _to_pct(val):
    s = str(val).strip()
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return float("nan")
    try:
        v = float(s)
        return v if 0.0 <= v <= 1.0 else v / 100.0
    except ValueError:
        return float("nan")

def _fmt_mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except FileNotFoundError:
        return "missing"

def load_df() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found at {CSV_PATH}")
    print(f"[load_df] reading CSV; mtime={_fmt_mtime(CSV_PATH)}")

    cols = ["team_name", "wins", "losses", "accuracy", "fpts", "fpts_against", "ppts"]
    df = pd.read_csv(CSV_PATH)[cols].copy()

    to_i = lambda s: pd.to_numeric(s, errors="coerce").fillna(0).astype(int)
    df["wins"]   = to_i(df["wins"])
    df["losses"] = to_i(df["losses"])
    df["fpts"]   = pd.to_numeric(df["fpts"], errors="coerce").fillna(0)
    df["fpa"]    = pd.to_numeric(df["fpts_against"], errors="coerce").fillna(0)
    df["ppts"]   = pd.to_numeric(df["ppts"], errors="coerce").fillna(0)

    df = df.sort_values(["wins", "fpts", "ppts"], ascending=[False, False, False]).reset_index(drop=True)

    RANK_ICONS = {0: ":trophy:", 1: "🥈", 2: "🥉", 3: ":star:", 4: "5️⃣", 5: "6️⃣", 6: "7️⃣", 7: "8️⃣", 8: "😰", 9: "🧻", 10: "💩", 11: "🚽"}

    rows = []
    for i, r in df.iterrows():
        prefix = RANK_ICONS.get(i, f"{i+1}")
        name   = str(r["team_name"])[:26]
        if i < 3:
            name = f"**{name}**"

        record = f"{int(r['wins'])}–{int(r['losses'])}"
        pfpa   = f"{r['fpts']:.0f}–{r['fpa']:.0f}"
        acc    = _to_pct(r["accuracy"])
        acc_s  = f"{acc:.1%}" if pd.notna(acc) else "—"

        rows.append({"Team": f"{prefix} {name} ({record})", "PFPA": pfpa, "Acc": acc_s})

    return pd.DataFrame(rows)

def make_pages(df: pd.DataFrame, rows_per_page: int = 15,
               title: str = ":football: Room 40 Standings :football:",
               color: discord.Color = discord.Color.green()) -> list[discord.Embed]:
    pages: list[discord.Embed] = []
    for start in range(0, len(df), rows_per_page):
        ch = df.iloc[start:start+rows_per_page]
        col = lambda s: "\n".join(s.astype(str)) or "—"

        emb = discord.Embed(title=title, color=color)
        emb.add_field(name="Team", value=col(ch["Team"]), inline=True)
        emb.add_field(name="PF–PA", value=col(ch["PFPA"]), inline=True)
        emb.add_field(name="Accuracy", value=col(ch["Acc"]), inline=True)
        emb.set_footer(text="scoreboard")
        pages.append(emb)
    return pages

# ---------- slash command ----------
@tree.command(name="standings", description="Show fantasy league standings", guild=GUILD)
async def standings(interaction: discord.Interaction):
    try:
        df = load_df()
    except FileNotFoundError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return

    pages = make_pages(df, rows_per_page=12)
    await interaction.response.send_message(embed=pages[0])
    for emb in pages[1:]:
        await interaction.followup.send(embed=emb)

# ---------- startup ----------
@bot.event
async def on_ready():
    synced = await tree.sync(guild=GUILD)  # instant sync to your server
    print(f"Logged in as {bot.user} | Synced {len(synced)} command(s) to guild {GUILD_ID} | CSV={CSV_PATH}")

bot.run(TOKEN)
