# main.py  ──  startup, lifecycle, GPT-2 "mr bot" listener
# pip install discord.py python-dotenv transformers torch pandas
#
# All slash commands live in commands.py.
# This file only handles:
#   • GPT-2 "mr bot / jarvis / siri" keyword listener  (kept as on_message)
#   • on_ready startup message
#   • graceful Ctrl-C shutdown

import asyncio
import os

import discord
from discord import app_commands
from dotenv import load_dotenv
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
import torch

# ── env ────────────────────────────────────────────────────────────────────────
load_dotenv()
TOKEN               = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID            = int(os.getenv("GUILD_ID", "0"))
TEST_ENV_CHANNEL_ID = int(os.getenv("TEST_ENV_CHANNEL_ID", "0"))
TROLL_THIS_USER_ID  = int(os.getenv("TROLL_THIS_USER_ID", "0"))

if not TOKEN:    raise SystemExit("Set DISCORD_TOKEN in your .env")
if not GUILD_ID: raise SystemExit("Set GUILD_ID in your .env")

GUILD = discord.Object(id=GUILD_ID)

# ── path constants (shared with commands.py) ───────────────────────────────────
R_PATH      = os.path.expanduser("~/discordBot/r/")
PYTHON_PATH = os.path.expanduser("~/discordBot/python/")
OUTPUT_PATH = os.path.expanduser("~/discordBot/outputs/")

# ── GPT-2 ──────────────────────────────────────────────────────────────────────
MODEL_NAME = "gpt2"
device     = torch.device("cpu")

print("Loading GPT-2 model...")
tokenizer      = AutoTokenizer.from_pretrained(MODEL_NAME)
model          = AutoModelForCausalLM.from_pretrained(MODEL_NAME).to(device)
gpt2_pipeline  = pipeline(
    "text-generation", model=model, tokenizer=tokenizer,
    max_new_tokens=200, do_sample=False, temperature=1.0,
    top_k=50, top_p=0.90, device=-1,
)
print("GPT-2 model loaded!")

BOT_PERSONA = (
    "A really clever and very funny & kind bot is joking around "
    "in a groupchat with a bunch of his buddies."
)

async def generate_ai_response(user_message: str) -> str:
    prompt = f"{BOT_PERSONA}\n\nUser: {user_message}\nBot:"
    try:
        response = await asyncio.to_thread(
            gpt2_pipeline, prompt,
            max_new_tokens=100, do_sample=True,
            temperature=0.75, top_k=50, top_p=0.90,
        )
        if not response:
            return "what were we talking about again?"
        bot_reply = response[0]["generated_text"].split("Bot:")[-1].strip()
        bot_reply = bot_reply.split("User:")[0].strip()
        return bot_reply or "uhhhhh wait what?"
    except Exception as e:
        print(f"GPT-2 error: {e}")
        return "Oops, something went wrong!"

# ── bot / tree ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True          # needed for the GPT-2 on_message trigger

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Import and register all slash commands from commands.py
        from commands import register_commands
        register_commands(self.tree, GUILD,
                          R_PATH=R_PATH,
                          PYTHON_PATH=PYTHON_PATH,
                          OUTPUT_PATH=OUTPUT_PATH)
        synced = await self.tree.sync(guild=GUILD)
        print(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}")

    async def on_ready(self):
        print(f"Online as {self.user}")
        ch = self.get_channel(TEST_ENV_CHANNEL_ID)
        if ch:
            await ch.send("going online!")

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        content = message.content.lower()

        # ── GPT-2 trigger (keyword, not slash) ────────────────────────────────
        if (
            ("mr" in content and "bot" in content)
            or "jarvis" in content
            or "siri" in content
        ):
            print(f"GPT-2 triggered by: {message.content}")
            try:
                reply = await generate_ai_response(message.content)
                if not reply.strip():
                    await message.channel.send("wait what did u say sorry i got really stoned earlier")
                    return
                await message.channel.send(reply)
            except Exception as e:
                print(f"GPT-2 on_message error: {e}")
                await message.channel.send("error - something went wrong")

    async def send_goodbye_message(self):
        ch = self.get_channel(TEST_ENV_CHANNEL_ID)
        if ch:
            print("sending goodbye message")
            await ch.send("going offline!")
            await asyncio.sleep(2)

client = BotClient()

# ── run ─────────────────────────────────────────────────────────────────────────
loop = asyncio.get_event_loop()
try:
    loop.run_until_complete(client.start(TOKEN))
except KeyboardInterrupt:
    async def _shutdown():
        await client.send_goodbye_message()
        await client.close()
    loop.run_until_complete(_shutdown())
finally:
    loop.close()
