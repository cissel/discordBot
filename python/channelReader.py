import discord
import os
import asyncio
import csv
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
ALLOWED_CHANNELS = set(map(int, os.getenv("ALLOWED_CHANNELS", "").split(",")))

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
        print("Bot is not in the specified server!")
        return

    print(f"Fetching messages from: {guild.name}")

    # Prepare to write CSV
    with open("server_messages.csv", "w", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["datetime", "user", "channel", "message"])

        for channel in guild.text_channels:
            if channel.id in ALLOWED_CHANNELS:
                try:
                    print(f"Fetching messages from #{channel.name}...")
                    async for message in channel.history(limit=None):
                        writer.writerow([
                            message.created_at.isoformat(),
                            message.author.name,
                            message.author.display_name,
                            channel.name,
                            message.content.replace("\n", "\\n")
                        ])
                except Exception as e:
                    print(f"Could not fetch messages from {channel.name}: {e}")

    print("✅ Finished writing messages to server_messages.csv")
    await client.close()

client.run(TOKEN)

