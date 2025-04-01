import discord
import subprocess
from dotenv import load_dotenv
import os
import asyncio
import random
import pandas as pd
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline 
import torch

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TEST_ENV_CHANNEL_ID = int(os.getenv("TEST_ENV_CHANNEL_ID"))
TROLL_THIS_USER_ID = int(os.getenv("TROLL_THIS_USER_ID")) 

R_PATH = "/Users/jamescissel/discordBot/r/"
PYTHON_PATH = "/Users/jamescissel/discordBot/python/"
OUTPUT_PATH = "/Users/jamescissel/discordBot/outputs/"
BBOT_FOLDER = os.path.join(OUTPUT_PATH, "bb")

# Switch back to GPT-2
MODEL_NAME = "gpt2"

# Set device to CPU (change to "cuda" if running on GPU)
device = torch.device("cpu")

# Load GPT-2 model & tokenizer
print("Loading GPT-2 model...")  # Debugging print
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME).to(device)

# Create the GPT-2 text-generation pipeline
gpt2_pipeline = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=200,  # Adjust for longer/shorter responses
    do_sample=False, # was originally true
    temperature=1.0, 
    top_k=50, 
    top_p=0.90,
    device=-1  # -1 forces CPU mode
)

print("GPT-2 model loaded!")  # Debugging print

# Define the bot's personality
BOT_PERSONA = (
    "A really witty and very funny & helpful bot is joking around in a groupchat with a bunch of his buddies."
)

async def generate_ai_response(user_message):
    prompt = f"{BOT_PERSONA}\n\nUser: {user_message}\nBot:"

    print(f"Debug: Received user message: {user_message}")
    print(f"Debug: Generated prompt: {prompt}")

    try:
        response = gpt2_pipeline(
            prompt, 
            max_new_tokens=100, 
            do_sample=True, 
            temperature=0.75, 
            top_k=50, 
            top_p=0.90
        )

        print(f"Debug: Raw GPT-2 response object: {response}")  # Check if response exists

        if not response:  # Handle empty response
            print("GPT-2 returned an empty response!")
            return "what were we talking about again?"

        bot_reply = response[0]['generated_text'].split("Bot:")[-1].strip()

        # Only keep the first sentence to prevent looping weirdness
        bot_reply = bot_reply.split("User:")[0].strip()

        print(f"Debug: Extracted bot reply: {bot_reply}")  # Check if bot_reply is empty

        return bot_reply or "uhhhhh wait what?"

    except Exception as e:
        print(f"Error generating response: {e}")
        return "Oops, something went wrong!"

intents = discord.Intents.default()
intents.message_content = True

class Client(discord.Client):

    # initialize last_sent_meme
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_sent_meme = None  # ✅ Store in the bot instance

    async def on_ready(self):

        print("online!")

        channel = client.get_channel(TEST_ENV_CHANNEL_ID)
        if channel:
            await channel.send("going online!")

    async def on_message(self, message):

        if message.author == self.user:
            return
        
        if message.author.id == TROLL_THIS_USER_ID:
            if random.random() < 0.1:
                await message.channel.send("shut up")
                await asyncio.sleep(1)
                await message.channel.send("idiot")
                await asyncio.sleep(2)
                await message.channel.send("lol jk here u go")
        
        # Trigger GPT-2 only if "bot" is in the message
        if ("mr" in message.content.lower() and "bot" in message.content.lower()) or "jarvis" in message.content.lower():
            print(f"GPT-2 Triggered by: {message.content}")  # ✅ Debugging line
            #await message.channel.send("thinking...")

            try:
                ai_response = await generate_ai_response(message.content)
                
                if not ai_response.strip():  # ✅ Check if response is empty
                    await message.channel.send("wait what did u say sorry i got really stoned earlier")
                    return

                await message.channel.send(ai_response)
                print(f"Sent GPT-2 Response: {ai_response}")  # ✅ Debugging line

            except Exception as e:
                print(f"error generating response: {e}")
                await message.channel.send("error - something went wrong")

        #if "how are you" in message.content.lower():
        #    await message.channel.send("happy to be here :)")
        #    await asyncio.sleep(1)
        #    await message.channel.send("thank you for asking :)")

        #if message.content.lower().startswith("hello"):
        #    await message.channel.send(f"yooooo what's up {message.author.display_name}")

        if message.content.lower() == 'ping':
            await message.channel.send("pong 🏓")

        if "gm" in message.content.lower():
            await message.add_reaction("🌞")
            await message.channel.send("good morning! :)")

        if message.content.lower() == "good bot":
            await message.add_reaction("❤️")
            audio_folder = os.path.join(OUTPUT_PATH, "botSounds")

            audio_files = [os.path.join(audio_folder, f) for f in os.listdir(audio_folder) if f.endswith((".wav", ".mp3", ".mp4"))]

            if not audio_files:
                await message.channel.send("no audio files in folder")
                return
            
            selected_audio = random.choice(audio_files)

            await message.channel.send(file=discord.File(selected_audio))

        if message.content.lower() == "duval":
            await message.channel.send("bang em")

        if message.content.lower() == "westside":
            await message.channel.send("jville")

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

        if message.content.lower() == "!boobs":
            # 33.3% chance to send "gulag" instead of an image
            if random.random() < 0.333:
                await message.channel.send("gulag")
                return

            # Get all media files from the folder
            media_files = [os.path.join(BBOT_FOLDER, f) for f in os.listdir(BBOT_FOLDER) if f.endswith((".png", ".jpg", ".jpeg", ".gif", ".mov"))]

            if not media_files:
                await message.channel.send("no images or videos found in the folder")
                return

            # Remove the last sent file from selection if possible
            available_files = [f for f in media_files if f != self.last_sent_meme]

            # If all files were removed, reset and allow any file
            if not available_files:
                available_files = media_files

            # Pick a new random file
            selected_file = random.choice(available_files)

            # ✅ Update the last sent file in the bot instance
            self.last_sent_meme = selected_file  

            await message.channel.send(file=discord.File(selected_file))

        if message.content.lower() == "cats!":
            await message.channel.send("vamos gatos")
            await asyncio.sleep(1)
            await message.channel.send("pulling data from 2024 stanley cup game 7")
            subprocess.run(["Rscript", os.path.join(R_PATH, "floridaPanthe.R")])
            await message.channel.send("champions", file = discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/catsWin.png")))

        if message.content.lower() == "hoops today?":  # Command to trigger CSV generation
            await message.channel.send("lemme check")

            # Run the R script to generate the CSV
            subprocess.run(["Rscript", os.path.join(R_PATH, "nbaToday.R")])

            csv_path = os.path.join(OUTPUT_PATH, "sports/nba/gamesToday.csv")

            # Check if CSV was created
            if not os.path.exists(csv_path):
                await message.channel.send("no hoops today :(")
                return
            
            print(".csv found")
            await message.channel.send("hoops today:")

            # Read CSV into a DataFrame
            df = pd.read_csv(csv_path)

            # Create an embed message
            embed = discord.Embed(title="🏀 Today's NBA Matchups", color=0x3498db)

            # Loop through each row and add a field for each game
            for i, row in df.iterrows():
                matchup_text = f"{row['time']}" 
                embed.add_field(name=row["matchup"], value=matchup_text, inline=False)

            # Send the embed to Discord
            await message.channel.send(embed=embed)

        if "hockey today?" in message.content.lower():
            await message.channel.send("one sec")

            subprocess.run(["python3", os.path.join(PYTHON_PATH, "nhlToday.py")])

            csv_path = os.path.join(OUTPUT_PATH, "sports/nhl/gamesToday.csv")

            # Check if CSV was created
            if not os.path.exists(csv_path):
                await message.channel.send("no hockey today :(")
                return
            
            print(".csv found")
            await message.channel.send("hockey today:")

            # Read CSV into a DataFrame
            df = pd.read_csv(csv_path)

            # Create an embed message
            embed = discord.Embed(title="🏒 Today's NHL Matchups", color=0x3498db)

            # Loop through each row and add a field for each game
            for i, row in df.iterrows():
                matchup_text = f"{row['time']}" 
                embed.add_field(name=row["matchup"], value=matchup_text, inline=False)

            # Send the embed to Discord
            await message.channel.send(embed=embed)

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