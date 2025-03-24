import discord
import subprocess
from dotenv import load_dotenv
import os

class Client(discord.Client):

    async def on_ready(self):

        print(f'{self.user} online!')

    async def on_message(self, message):

        if message.author == self.user:
            return
        
        if message.author == '448619126659743744':
            await message.channel.send(f"shut up chuck")
            return
        
        if message.content.startswith('hello'):
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

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = Client(intents=intents)
client.run(TOKEN)