import discord
import os
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

# Read allowed channels from .env and convert them into a set of integers
ALLOWED_CHANNELS = set(map(int, os.getenv("ALLOWED_CHANNELS", "").split(",")))

intents = discord.Intents.default()
intents.messages = True  
intents.guilds = True
intents.message_content = True  

client = discord.Client(intents=intents)

all_messages = []

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("Bot is not in the specified server!")
        return

    print(f"Fetching messages from: {guild.name}")

    for channel in guild.text_channels:
        if channel.id in ALLOWED_CHANNELS:  # ✅ Only fetch messages from allowed channels
            try:
                print(f"Fetching messages from #{channel.name}...")
                async for message in channel.history(limit=None):
                    all_messages.append((message.author.name, message.content))

            except Exception as e:
                print(f"Could not fetch messages from {channel.name}: {e}")

    with open("server_messages.txt", "w", encoding="utf-8") as f:
        for author, content in all_messages:
            f.write(f"{author}: {content}\n")

    print(f"✅ Finished! Collected {len(all_messages)} messages.")
    await client.close()

client.run(TOKEN)

