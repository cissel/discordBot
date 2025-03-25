import discord
import subprocess
from dotenv import load_dotenv
import os
import asyncio
import random

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TEST_ENV_CHANNEL_ID = int(os.getenv("TEST_ENV_CHANNEL_ID"))
CHUCK_USER_ID = int(os.getenv("CHUCK_USER_ID")) 

R_PATH = "r/"
OUTPUT_PATH = "outputs/"
BBOT_FOLDER = os.path.join(OUTPUT_PATH, "bb")

intents = discord.Intents.default()
intents.message_content = True

class Client(discord.Client):

    async def on_ready(self):

        print("online!")

        channel = client.get_channel(TEST_ENV_CHANNEL_ID)
        if channel:
            await channel.send("going online!")

    async def on_message(self, message):

        if message.author == self.user:
            return
        
        if message.author.id == CHUCK_USER_ID:
            if random.random() < 0.1:
                await message.channel.send("shut up")
                await asyncio.sleep(1)
                await message.channel.send("idiot")
                await asyncio.sleep(2)
                await message.channel.send("jk here u go")
        
        if message.content.lower().startswith("hello"):
            await message.channel.send(f"yooooo what's up {message.author.display_name}")

        if message.content.lower() == 'ping':
            await message.channel.send(f"pong 🏓")

        if message.content.lower() == "good bot":
            await message.add_reaction("❤️")
            audio_folder = os.path.join(OUTPUT_PATH, "botSounds")

            audio_files = [os.path.join(audio_folder, f) for f in os.listdir(audio_folder) if f.endswith((".wav", ".mp3", ".mp4"))]

            if not audio_files:
                await message.channel.send("no audio files in folder")
                return
            
            selected_audio = random.choice(audio_files)

            await message.channel.send(file=discord.File(selected_audio))

        if message.content.lower() == '!surf':
            await message.channel.send("hey dude! give me a sec to check the waves")
            subprocess.run(["Rscript", os.path.join(R_PATH, "surf4castPlot.R")])
            await message.channel.send("here's the latest surf forecast:", file=discord.File(os.path.join(OUTPUT_PATH, "surf_fcst.png")))

        if message.content.lower() == '!jaxradar':
            await message.channel.send("pulling jax radar")
            subprocess.run(["Rscript", os.path.join(R_PATH, "jaxRada.R")])
            await message.channel.send("here's the latest radar loop:", file=discord.File(os.path.join(OUTPUT_PATH, "nwsJaxRadar.gif")))

        if message.content.lower() == '!flradar':
            await message.channel.send("pulling florida radar")
            subprocess.run(["Rscript", os.path.join(R_PATH, "flRada.R")])
            await message.channel.send("here's the latest radar loop:", file=discord.File(os.path.join(OUTPUT_PATH, "flRadar.gif")))

        if message.content.lower() == "!boobs":  # Replace with your trigger message
            # 50% chance to respond with "x" instead of a file
            if random.random() < 0.333:  # 33.3% probability
                await message.channel.send("gulag")
                return

            # Get all image & video files from the folder
            media_files = [os.path.join(BBOT_FOLDER, f) for f in os.listdir(BBOT_FOLDER) if f.endswith((".png", ".jpg", ".jpeg", ".gif", ".mov"))]

            if not media_files:
                await message.channel.send("no images or videos found in the folder")
                return

            selected_file = random.choice(media_files)

            await message.channel.send(file=discord.File(selected_file))
        
        if message.content.lower().startswith("gm"):
            await message.add_reaction("🌞")
            await message.channel.send("good morning! :)")

    async def send_goodbye_message(self):

        """Sends a shutdown message before the bot exits."""
        channel = self.get_channel(TEST_ENV_CHANNEL_ID)
        if channel:
            print("sending goodbye message")
            await channel.send("going offline!")
            await asyncio.sleep(2)
        else:
            print(f"Could not find channel {TEST_ENV_CHANNEL_ID}")

client = Client(intents=intents)

async def shutdown_handler():
    """Handles cleanup when the bot is shutting down."""
    print("shutting down bot...")
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