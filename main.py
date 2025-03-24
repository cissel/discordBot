import discord
import subprocess
from dotenv import load_dotenv
import os
import asyncio
import signal

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TEST_ENV_CHANNEL_ID = int(os.getenv("TEST_ENV_CHANNEL_ID"))
CHUCK_USER_ID = int(os.getenv("CHUCK_USER_ID")) 

intents = discord.Intents.default()
intents.message_content = True

class Client(discord.Client):

    async def on_ready(self):

        print(f'online!')

        channel = client.get_channel(TEST_ENV_CHANNEL_ID)
        if channel:
            await channel.send(f'going online!')

    async def on_message(self, message):

        if message.author == self.user:
            return
        
        if message.author == CHUCK_USER_ID:
            await message.channel.send(f"shut up chuck")
            return
        
        if message.content.lower().startswith('hello'):
            await message.channel.send(f"yooooo what's up {message.author.display_name}")

        if message.content.lower() == 'ping':
            await message.channel.send(f"pong 🏓")

        if message.content.lower() == "good bot":
            await message.add_reaction("❤️")

        if message.content.lower() == '!surf':
            await message.channel.send(f"hey dude! give me a sec to check the waves")
            subprocess.run(["Rscript", "surf4castPlot.R"])
            await message.channel.send("here's the latest surf forecast:", file=discord.File("surf_fcst.png"))

        if message.content.lower() == '!jaxradar':
            await message.channel.send(f'pulling jax radar')
            subprocess.run(["Rscript", "jaxRada.R"])
            await message.channel.send("here's the latest radar loop:", file=discord.File("nwsJaxRadar.gif"))

        if message.content.lower() == '!flradar':
            await message.channel.send(f'pulling florida radar')
            subprocess.run(["Rscript", "flRada.R"])
            await message.channel.send("here's the latest radar loop:", file=discord.File("flRadar.gif"))

    async def send_goodbye_message(self):

        """Sends a shutdown message before the bot exits."""
        channel = self.get_channel(TEST_ENV_CHANNEL_ID)
        if channel:
            print("Sending goodbye message")
            await channel.send(f'going offline!')
            await asyncio.sleep(2)
        else:
            print(f"Could not find channel {TEST_ENV_CHANNEL_ID}")

client = Client(intents=intents)

async def shutdown_handler():
    """Handles cleanup when the bot is shutting down."""
    print("Shutting down bot...")
    await client.send_goodbye_message()  # Send the goodbye message
    await client.close()  # Properly disconnect the bot

# Handle Ctrl+C manually to trigger the shutdown message
loop = asyncio.get_event_loop()

try:
    loop.run_until_complete(client.start(TOKEN))
except KeyboardInterrupt:
    loop.run_until_complete(shutdown_handler())
finally:
    loop.close()

client.run(TOKEN)