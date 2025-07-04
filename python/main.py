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
    "A really clever and very funny & kind bot is joking around in a groupchat with a bunch of his buddies."
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
        
        #if message.author.id == TROLL_THIS_USER_ID:
        #    if random.random() < 0.01:
        #        await message.channel.send("shut up")
        #        await asyncio.sleep(1)
        #        await message.channel.send("idiot")
        #        await asyncio.sleep(1)
        #        await message.channel.send("jk lol here you go")
                #return
        
        # Trigger GPT-2 only if "mr bot" is in the message
        if ("mr" in message.content.lower() and "bot" in message.content.lower()) or "jarvis" in message.content.lower() or "siri" in message.content.lower():
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

        if message.content.lower() == 'ping':
            await message.channel.send("pong 🏓")

        if "r2" in message.content.lower():
            audio_folder = os.path.join(OUTPUT_PATH, "botSounds")

            audio_files = [os.path.join(audio_folder, f) for f in os.listdir(audio_folder) if f.endswith((".wav", ".mp3", ".mp4"))]

            if not audio_files:
                await message.channel.send("no audio files in folder")
                return
            
            selected_audio = random.choice(audio_files)

            await message.channel.send(file=discord.File(selected_audio))            

        if "type shit" in message.content.lower() or "typeshit" in message.content.lower() or message.content.lower() == "ts":
            await message.channel.send("ong fr")

        if message.content.lower() == "type":
            await message.channel.send("shit")

        if message.content.lower() == "gm" or message.content.lower() == "good morning":
            await message.add_reaction("🌞")
            await message.channel.send("good morning! :)")

        if "good bot" in message.content.lower() or "goodbot" in message.content.lower():
            await message.add_reaction("❤️")
            audio_folder = os.path.join(OUTPUT_PATH, "botSounds")

            audio_files = [os.path.join(audio_folder, f) for f in os.listdir(audio_folder) if f.endswith((".wav", ".mp3", ".mp4"))]

            if not audio_files:
                await message.channel.send("no audio files in folder")
                return
            
            selected_audio = random.choice(audio_files)

            await message.channel.send(file=discord.File(selected_audio))

        if "server history" in message.content.lower() or "serverhistory" in message.content.lower() or "servhist" in message.content.lower():
            await message.channel.send("*going back in time*")
            subprocess.run(["python3", os.path.join(PYTHON_PATH, "channelReader.py")])
            subprocess.run(["Rscript", os.path.join(R_PATH, "serverHistory.R")])
            await message.channel.send(":) <3", file=discord.File(os.path.join(OUTPUT_PATH, "metrics/allMessages.png")))

        if "channel history" in message.content.lower() or "channelhistory" in message.content.lower() or "chanhist" in message.content.lower() or "channelhist" in message.content.lower():
            await message.channel.send("*opening all channels*")
            subprocess.run(["python3", os.path.join(PYTHON_PATH, "channelReader.py")])
            subprocess.run(["Rscript", os.path.join(R_PATH, "channelHistory.R")])
            await message.channel.send(":) <3", file=discord.File(os.path.join(OUTPUT_PATH, "metrics/channelMessages.png")))

        if "user history" in message.content.lower() or "userhistory" in message.content.lower() or "userhist" in message.content.lower():
            await message.channel.send("*checking attendance records*")
            subprocess.run(["python3", os.path.join(PYTHON_PATH, "channelReader.py")])
            subprocess.run(["Rscript", os.path.join(R_PATH, "userHistory.R")])
            await message.channel.send(":) <3", file=discord.File(os.path.join(OUTPUT_PATH, "metrics/userMessages.png")))

        if message.content.lower() == "duval":
            await message.channel.send("bang em")

        if message.content.lower() == "westside":
            await message.channel.send("jville")

        if "weather tomorrow" in message.content.lower() or "weathertomorrow" in message.content.lower() or "weathertm" in message.content.lower():
            await message.channel.send("checking nws")
            subprocess.run(["Rscript", os.path.join(R_PATH, "weatherTm.R")])
            await message.channel.send("here ya go:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/weatherTm.png")))

        if "weather today" in message.content.lower() or "weathertoday" in message.content.lower() or "weathertd" in message.content.lower():
            await message.channel.send("walking outside")
            subprocess.run(["Rscript", os.path.join(R_PATH, "weathe.R")])
            await message.channel.send("have a great day :)", file=discord.File(os.path.join(OUTPUT_PATH, "weather/weatherTd.png")))

        if "surf" in message.content.lower():
            await message.channel.send("hey dude! give me a sec to check the waves")
            subprocess.run(["Rscript", os.path.join(R_PATH, "surf4castPlot.R")])
            await message.channel.send("here's the latest surf forecast:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/surf_fcst.png")))

        if "wave map" in message.content.lower() or "wavemap" in message.content.lower():
            await message.channel.send("pulling wave forecast (this one is pretty slow sorry dude)")
            subprocess.run(["Rscript", os.path.join(R_PATH, "surfMap.R")])
            await message.channel.send("here's the latest forecast map:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/wave_animation.gif")))

        if "wind map" in message.content.lower() or "windmap" in message.content.lower():
            await message.channel.send("pulling wind forecast (this is slow too sorry homie)")
            subprocess.run(["Rscript", os.path.join(R_PATH, "windMap.R")])
            await message.channel.send("here's the latest wind forecast map:", file = discord.File(os.path.join(OUTPUT_PATH, "weather/wind_animation.gif")))

        if "jax rad" in message.content.lower() or "jaxrad" in message.content.lower():
            await message.channel.send("pulling jax radar")
            subprocess.run(["Rscript", os.path.join(R_PATH, "jaxRada.R")])
            await message.channel.send("here's the latest radar loop:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/nwsJaxRadar.gif")))

        if "fl rad" in message.content.lower() or "flrad" in message.content.lower():
            await message.channel.send("pulling florida radar")
            subprocess.run(["Rscript", os.path.join(R_PATH, "flRada.R")])
            await message.channel.send("here's the latest radar loop:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/flRadar.gif")))

        if "us rad" in message.content.lower() or "usrad" in message.content.lower():
            await message.channel.send("pulling us radar")
            subprocess.run(["Rscript", os.path.join(R_PATH, "usRada.R")])
            await message.channel.send("here's the latest radar loop:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/usRadar.gif")))

        if "jax sat" in message.content.lower() or "jaxsat" in message.content.lower():
            await message.channel.send("*going to space brb w/ a gif*")
            subprocess.run(["Rscript", os.path.join(R_PATH, "jaxSat.R")])
            await message.channel.send("here's the latest satellite view:", file = discord.File(os.path.join(OUTPUT_PATH, "weather/nwsJaxSat.gif")))

        if "buoy waves" in message.content.lower() or "buoywaves" in message.content.lower() or "wave plot" in message.content.lower() or "waveplot" in message.content.lower():
            await message.channel.send("pulling buoy data")
            subprocess.run(["Rscript", os.path.join(R_PATH, "noaaBuoy.R")])
            await message.channel.send("generating plot")
            subprocess.run(["Rscript", os.path.join(R_PATH, "buoyWavePlot.R")])
            await message.channel.send("latest observations from NOAA buoy #41117:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/buoyWaves.png")))

        if "tide plot" in message.content.lower() or "tideplot" in message.content.lower() or "mayport tide" in message.content.lower() or "mayporttide" in message.content.lower():
            await message.channel.send("checking mayport bar pilots dock")
            subprocess.run(["Rscript", os.path.join(R_PATH, "tidePlot.R")])
            await message.channel.send("current tides:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/mayportTides.png")))

        if "wind plot" in message.content.lower() or "windplot" in message.content.lower() or "mayport wind" in message.content.lower() or "mayportwind" in message.content.lower():
            await message.channel.send("*licks finger*")
            subprocess.run(["Rscript", os.path.join(R_PATH, "mayportWind.R")])
            await message.channel.send("current winds:", file=discord.File(os.path.join(OUTPUT_PATH, "weather/mayportWinds.png")))

        if "two7" in message.content.lower() or message.content.lower() == "27" or "hurricane forecast" in message.content.lower():
            await message.channel.send("checking NOAA")
            subprocess.run(["Rscript", os.path.join(R_PATH, "hurricane.R")])
            await message.channel.send("here's the 7 day tropical weather outlook", file=discord.File(os.path.join(OUTPUT_PATH, "weather/two7d.png")))

        if "bully zooksy" in message.content.lower() or "bullyzooksy" in message.content.lower() or "bully chuck" in message.content.lower() or "bullychuck" in message.content.lower():
            await message.channel.send("!kingcap")
            await asyncio.sleep(1)
            await message.channel.send("🤣🫵")

        if "bully fish" in message.content.lower() or "bullyfish" in message.content.lower():
            await message.channel.send("fuck you fish")
            await asyncio.sleep(1)
            await message.channel.send("🤣🫵")

        if "bully bryce" in message.content.lower() or "bullybryce" in message.content.lower():
            await message.channel.send("no")
            await asyncio.sleep(1)
            await message.channel.send("squidly rulez")

        if "bully james" in message.content.lower() or "bullyjames" in message.content.lower():
            await message.channel.send("which one bro there are several james in here")
            await asyncio.sleep(1)
            await message.channel.send("you know what")
            await asyncio.sleep(1)
            await message.channel.send("fuck you james")
            await asyncio.sleep(1)
            await message.channel.send("fuck you too james")

        if "bully cissel" in message.content.lower() or "bullycissel" in message.content.lower():
            await message.channel.send("fuck you james")
            await asyncio.sleep(1)
            await message.channel.send("🤣🫵")

        if "bully jp" in message.content.lower() or "bullyjp" in message.content.lower() or "bully p" in message.content.lower() or "bullyp" in message.content.lower() or "bully peyton" in message.content.lower() or "bullypeyton" in message.content.lower():
            await message.channel.send("fuck you p")
            await asyncio.sleep(1)
            await message.channel.send("🤣🫵")

        if "bully eli" in message.content.lower() or "bullyeli" in message.content.lower():
            await message.channel.send("silky johnson player hater of the year 2025")

        if "bully brandon" in message.content.lower() or "bullybrandon" in message.content.lower() or "bully vapedad" in message.content.lower() or "bullyvapedad" in message.content.lower():
            await message.channel.send("hell nah")
            await asyncio.sleep(1)
            await message.channel.send("we did hard time together")
            await asyncio.sleep(1)

        if "bully jordan" in message.content.lower() or "bullyjordan" in message.content.lower():
            await message.channel.send("nah jordan's only ever been nice to me")

        if "bully chevy" in message.content.lower() or "bullychevy" in message.content.lower():
            await message.channel.send("yo fuck you bubba")
            await asyncio.sleep(1)
            await message.channel.send("🤣🫵")
            await asyncio.sleep(1)
            await message.channel.send("ayo u know im jk i love u bubba")

        if "bully verv" in message.content.lower() or "bullyverv" in message.content.lower():
            await message.channel.send("nah dude")
            await asyncio.sleep(1)
            await message.channel.send("i'm good")
            await asyncio.sleep(1)
            await message.channel.send("that boy too kind")

        if "bully tyler" in message.content.lower() or "bullytyler" in message.content.lower() or "bully tyjo" in message.content.lower() or "bullytyjo" in message.content.lower():
            await message.channel.send("fuck you tyler")
            await asyncio.sleep(1)
            await message.channel.send("get your tall strong handsome ass outta here smh")

        if "boobs" in message.content.lower():
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

        if "scrote" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nba/traeYoung.png")))

        if "denver nuggets" in message.content.lower():
            await message.channel.send("joke around and find out")
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nba/thugJokic.jpg")))

        if "hali" in message.content.lower():
            await message.channel.send("the haliban strikes again")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nba/hali.png")))

        if message.content.lower() == "stanley cup 2024!":
            await message.channel.send("vamos gatos")
            await asyncio.sleep(1)
            await message.channel.send("pulling data from 2024 stanley cup game 7")
            subprocess.run(["Rscript", os.path.join(R_PATH, "floridaPanthe.R")])
            await message.channel.send("champions", file = discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/catsCup.png")))

        if "cats win" in message.content.lower() or "catswin" in message.content.lower():
            await message.channel.send("W", file = discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/catsWin.png")))

        if "cats lost" in message.content.lower() or "catslost" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/sickAssPanther.webp")))

        if "kodak" in message.content.lower():
            await message.channel.send(file = discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/kodak.jpg")))

        if "barkov" in message.content.lower() or "barky" in message.content.lower():
            await message.channel.send(":)", file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/barky.png")))

        if "praise bobby" in message.content.lower() or "in bobby we trust" in message.content.lower() or "in bob we trust" in message.content.lower():
            await message.channel.send("bobby bless")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/stbobby.jpeg")))
            return

        if "thug bobby" in message.content.lower() or "iced out bobby" in message.content.lower() or "icy bobby" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/bobbyChain.png")))
            return

        if "bobrovsky" in message.content.lower() or "sergei" in message.content.lower() or "bobby" in message.content.lower():
            await message.channel.send("BRICK WALL BOB")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/brickwallbob.jpg")))

        if "chucky" in message.content.lower() or "tkachuk" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/chucky.jpeg")))

        if "reino" in message.content.lower() or "reinhart" in message.content.lower():
            await message.channel.send("i love you sam <3")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/reino.png")))

        if "aaron ekblad" in message.content.lower() or "ekblad" in message.content.lower() or "ekky" in message.content.lower():
            await message.channel.send("BOOSTED EKKY")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/ekblad.jpeg")))

        if "swaggy" in message.content.lower() or "carter" in message.content.lower() or "verhaeghe" in message.content.lower() or "swaggy verhaeghe" in message.content.lower() or "swaggycarter" in message.content.lower():
            await message.channel.send("NEVER FORGET", file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/buttpuck.mov")))

        if "drunk marchand" in message.content.lower() or "drunk marchy" in message.content.lower() or "drunkmarchand" in message.content.lower() or "drunkmarchy" in message.content.lower():
            await message.channel.send("BRAD MF MARCHAND")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/drunkMarchand.jpg")))
            return

        if "marchand" in message.content.lower() or "marchy" in message.content.lower():
            await message.channel.send("ALL HAIL THE RAT KING")
            asyncio.sleep(1)
            await message.channel.send("PANTHERS LEGEND AND FUTURE HALL OF FAMER BRADLEY MARCHAND", file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/marchand.png")))

        if "benny" in message.content.lower() or "bennett" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/benny.jpeg")))

        if "eetu" in message.content.lower() or "luostarinen" in message.content.lower() or "luosty" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/eetu.png")))

        if "gadjovich" in message.content.lower() or "gaddy" in message.content.lower() or "jonah" in message.content.lower():
            await message.channel.send("HEY SIRI PLAY SICKO MODE")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/gadjo.jpg")))

        if "seth jones" in message.content.lower() or "sethjones" in message.content.lower():
            await message.channel.send("SETH MF JONES")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/sethjones.jpeg")))

        if "lundy" in message.content.lower() or "lundell" in message.content.lower():
            await message.channel.send("lundy a mf shooter fr")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/lundell.png")))

        if "gustav" in message.content.lower() or "forsling" in message.content.lower() or "goosey" in message.content.lower() or "forsy" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/forsling.png")))

        if "jesper" in message.content.lower() or "boqvist" in message.content.lower():
            await message.channel.send("average jesper boqvist moment")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/jesper.png")))

        if "schmidty" in message.content.lower() or "nate schmidt" in message.content.lower() or "nate fucking schmidt" in message.content.lower():
            await message.channel.send("NATE THE GREAT")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/nateSchmidt.png")))

        if "pantr" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/pantrHands.jpeg")))

        if "i like the panthers" in message.content.lower():
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/iLikeThePanthers.png")))

        if "please florida panthers" in message.content.lower() or "stunt cats" in message.content.lower() or "stuntcats" in message.content.lower():
            await message.channel.send("please florida panthers")
            asyncio.sleep(2)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/stunt.png")))
            return

        if "florida panthers!" in message.content.lower() or "floridapanthers!" in message.content.lower():
            await message.channel.send("༼ つ ◕◕ ༽つ FLORIDA PANTHERS TAKE MY ENERGY ༼ つ ◕◕ ༽つ")

        if "wen cats" in message.content.lower() or "wencats" in message.content.lower() or "when cats" in message.content.lower() or "whencats" in message.content.lower():
            await message.channel.send("checking schedule")

            subprocess.run(["python3", os.path.join(PYTHON_PATH, "nextCats.py")])

            CSV_PATH = os.path.join(OUTPUT_PATH, "sports/nhl/nextTeamGame.csv")

            if not os.path.exists(CSV_PATH):
                await message.channel.send(f"couldnt find game info 😿")
                return

            print(".csv found")
            
            df = pd.read_csv(CSV_PATH)

            row = df.iloc[0]
            print(row)

            time = row["time"]
            matchup = row["matchup"]
            venue = row["venue"]

            clean_time = time

            embed = discord.Embed(
                title="Next Florida Panthers Game",
                description=f"**{matchup}**",
                color=0xB9975B  # Panthers gold-ish
            )
            embed.add_field(name="📅 When", value=clean_time, inline=False)
            embed.add_field(name="🏟️ Where", value=venue, inline=False)
            embed.set_footer(text="vamos gatos")

            # Optional: Add thumbnail
            embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/en/5/5d/Florida_Panthers_2023_logo.svg")

            await message.channel.send(embed=embed)

        if "fuck edm" in message.content.lower() or "fuckedm" in message.content.lower():
            await message.channel.send("FUCK EDM")
            asyncio.sleep(1)
            await message.channel.send("🤣🫵")

        if "curse edm" in message.content.lower() or "curseedm" in message.content.lower():
            await message.channel.send("༼ つ •̀_•́ ༽つ ቹጋጮዐክፕዐክ ዐጎረቹዪነ ፕልኡቹ ጮሃ ፪ልጋ ፓ፱ፓ፱ ༼ つ •̀_•́ ༽つ")
            asyncio.sleep(1)
            await message.channel.send(file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/curseEdm.mp4")))

        if "we the best" in message.content.lower() or "dj khaled" in message.content.lower() or "djkhaled" in message.content.lower():
            await message.channel.send("WE THE BEST HOCKEY TEAM!")
            asyncio.sleep(1)
            await message.channel.send("<3", file=discord.File(os.path.join(OUTPUT_PATH, "sports/nhl/djkhaled.png")))

        if "hockey today" in message.content.lower() or "hockeytoday" in message.content.lower() or "hockey td" in message.content.lower() or "hockeytd" in message.content.lower():
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

        if "hockey tomorrow" in message.content.lower() or "hockeytomorrow" in message.content.lower() or "hockey tm" in message.content.lower() or "hockeytm" in message.content.lower():
            await message.channel.send("i'll look")

            subprocess.run(["python3", os.path.join(PYTHON_PATH, "nhlTomorrow.py")])

            csv_path = os.path.join(OUTPUT_PATH, "sports/nhl/gamesTomorrow.csv")

            if not os.path.exists(csv_path):
                await(message.channel.send("no hockey tomorrow :("))
                return
            
            print(".csv found")
            await message.channel.send("hockey tomorrow:")

            df = pd.read_csv(csv_path)

            embed = discord.Embed(title="🏒 Tomorrow's NHL Matchups", color=0x3498db)

            for i, row in df.iterrows():
                matchup_text = f"{row['time']}"
                embed.add_field(name=row['matchup'], value=matchup_text, inline=False)

            await message.channel.send(embed=embed)

        if "nba scoreboard" in message.content.lower() or "nbasb" in message.content.lower() or "live hoops" in message.content.lower() or "livehoops" in message.content.lower() or "hoops rn" in message.content.lower() or "hoopsrn" in message.content.lower():
            await message.channel.send("*pulling up from half court*")

            subprocess.run(["Rscript", os.path.join(R_PATH, "nbaLiveScore.R")])

            CSV_PATH = os.path.join(OUTPUT_PATH, "sports/nba/liveScoreboard.csv")

            # Check file exists
            if not os.path.exists(CSV_PATH):
                await message.channel.send("😢 Couldn't find live NBA scores.")
                return

            # Read the CSV
            df = pd.read_csv(CSV_PATH)

            # Group by game_id
            grouped = df.groupby("game_id")

            for game_id, group in grouped:
                teams = group.sort_values("TEAM_NAME")  # Sort for consistency

                team1 = teams.iloc[0]
                team2 = teams.iloc[1]

                embed = discord.Embed(
                    title=f"🏀 {team1['team_name']} vs {team2['team_name']}",
                    #description=f"{team1['PTS']} - {team2['PTS']}",
                    color=0x5865F2
                )

                embed.add_field(name=team1["TEAM_ABBREVIATION"], value=f"{team1['PTS']}", inline=True)
                embed.add_field(name=team2["TEAM_ABBREVIATION"], value=f"{team2['PTS']}", inline=True)
                embed.add_field(name="Game Status", value=team1["game_status_text"].strip(), inline=False)

                await message.channel.send(embed=embed)

        if "hoops today" in message.content.lower() or "hoopstoday" in message.content.lower() or "hoops td" in message.content.lower() or "hoopstd" in message.content.lower():  
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

        if "hoops tomorrow" in message.content.lower() or "hoopstomorrow" in message.content.lower() or "hoops tm" in message.content.lower() or "hoopstm" in message.content.lower():
            await message.channel.send("lemme see")

            subprocess.run(["Rscript", os.path.join(R_PATH, "nbaTomorrow.R")])
            
            csv_path = os.path.join(OUTPUT_PATH, "sports/nba/gamesTomorrow.csv")

            # Check if CSV was created
            if not os.path.exists(csv_path):
                await message.channel.send("no hoops tomorrow :(")
                return
            
            print(".csv found")
            await message.channel.send("hoops tomorrow:")

            df = pd.read_csv(csv_path)

            embed = discord.Embed(title = "🏀 Tomorrow's NBA Matchups", color=0x3498db)
            
            # Loop through each row and add a field for each game
            for i, row in df.iterrows():
                matchup_text = f"{row['time']}" 
                embed.add_field(name=row["matchup"], value=matchup_text, inline=False)

            # Send the embed to Discord
            await message.channel.send(embed=embed)

        if "wen nfl" in message.content.lower() or "next nfl" in message.content.lower():
            await message.channel.send("checking schedules")
            subprocess.run(["Rscript", os.path.join(R_PATH, "nextNFL.R")])
            
            CSV_PATH = os.path.join(OUTPUT_PATH, "sports/nfl/nextGame.csv")

            if not os.path.exists(CSV_PATH):
                await message.channel.send("couldn't find game info :(")
                return
            
            print(".csv found")

            df = pd.read_csv(CSV_PATH)
            row = df.iloc[0]

            date = row["gameday"]
            time = row["gametime"]
            home = row["home_team"]
            away = row["away_team"]
            daysUntil = row["daysUntil"]
            stadium = row["stadium"]
            homeML = row["home_moneyline"]
            awayML = row["away_moneyline"]
            spreadline = row["spread_line"]
            homeSpreadOdds = row["home_spread_odds"]
            awaySpreadOdds = row["away_spread_odds"]
            ouLine = row["total_line"]
            underOdds = row["under_odds"]
            overOdds = row["over_odds"]

            matchup = f"{away} ({awayML}) @ {home} ({homeML})"
            spread = f"{away}: {awaySpreadOdds} | {home}: {homeSpreadOdds}"
            ouOdds = f"Under: {underOdds} | Over: {overOdds}"

            embed = discord.Embed(
                title=f"🏈 {daysUntil} Days Until Next NFL Game",
                description=f"**{matchup}**",
                color=0x013369  # NFL blue
            )
            embed.add_field(name="📅 When", value=f"{date} at {time}", inline=True)
            embed.add_field(name="🏟️ Where", value=stadium, inline=True)
            embed.add_field(name="", value="", inline=False)
            embed.add_field(name=f"🎲 Betting Spread: {spreadline}", value=spread, inline=True)
            embed.add_field(name=f"Total O/U: {ouLine}", value=ouOdds, inline=True)
            embed.set_footer(text="source: i know ball")

            await message.channel.send(embed=embed)

        if "wen jags" in message.content.lower() or "wenjags" in message.content.lower() or "when jags" in message.content.lower() or "whenjags" in message.content.lower():
            await message.channel.send("checking schedule")

            subprocess.run(["Rscript", os.path.join(R_PATH, "nextJagua.R")])

            CSV_PATH = os.path.join(OUTPUT_PATH, "sports/nfl/nextJags.csv")

            if not os.path.exists(CSV_PATH):
                await message.channel.send("couldn’t find game info 😔")
                return

            print(".csv found")

            df = pd.read_csv(CSV_PATH)
            row = df.iloc[0]

            date = row["gameday"]
            time = row["gametime"]
            home = row["home_team"]
            away = row["away_team"]
            daysUntil = row["daysUntil"]
            stadium = row["stadium"]
            homeML = row["home_moneyline"]
            awayML = row["away_moneyline"]
            spreadline = row["spread_line"]
            homeSpreadOdds = row["home_spread_odds"]
            awaySpreadOdds = row["away_spread_odds"]
            ouLine = row["total_line"]
            underOdds = row["under_odds"]
            overOdds = row["over_odds"]

            matchup = f"{away} ({awayML}) @ {home} ({homeML})"
            spread = f"{away}: {awaySpreadOdds} | {home}: {homeSpreadOdds}"
            ouOdds = f"Under: {underOdds} | Over: {overOdds}"

            embed = discord.Embed(
                title=f"🏈 {daysUntil} Days Until Next Jacksonville Jaguars Game",
                description=f"**{matchup}**",
                color=0x006778  # Teal or Jags color scheme
            )
            embed.add_field(name="📅 When", value=f"{date} at {time}", inline=True)
            embed.add_field(name="🏟️ Where", value=stadium, inline=True)
            embed.add_field(name="", value="", inline=False)
            embed.add_field(name=f"🎲 Betting Spread: {spreadline}", value=spread, inline=True)
            embed.add_field(name=f"Total O/U: {ouLine}", value=ouOdds, inline=True)
            embed.set_footer(text="duval bangem westside jville")

            await message.channel.send(embed=embed)

        if "next launch" in message.content.lower() or "nextlaunch" in message.content.lower():
            await message.channel.send("checking spaceflight schedules")

            subprocess.run(["python3", os.path.join(PYTHON_PATH, "spaceLaunches.py")])

            csv_path = os.path.join(OUTPUT_PATH, "space/next_launch.csv")

            if not os.path.exists(csv_path):
                await message.channel.send("🚫 Couldn't find launch data.")
                return

            df = pd.read_csv(csv_path)
            row = df.iloc[0]

            embed = discord.Embed(
                title=f"⏳ **T - {row['T-minus']}**",
                description="Next Launch from Kennedy Space Center",
                color=0x5865F2
            )

            mission_desc = row['Mission']
            if len(mission_desc) > 1000:
                mission_desc = mission_desc[:997] + "..."

            embed.add_field(
                name="🚀 Mission",
                value=f"{row['Name']}\n{mission_desc}",
                inline=False
            )
            embed.add_field(name="📋 Status", value=row['Status'], inline=True)
            embed.add_field(name="🗓️ Launch Window Opens", value=row['Window (ET)'], inline=True)
            embed.add_field(name="🏢 Agency", value=row['Provider'], inline=True)
            embed.add_field(name="📍 Launch Pad", value=row['Pad'], inline=False)

            # Add launch image if it exists
            if isinstance(row['Image'], str) and row['Image'].startswith("http"):
                print(f"Image URL: {row['Image']!r}")
                embed.set_image(url=row['Image'])

            await message.channel.send(embed=embed)

        if "fed rate" in message.content.lower() or "fedrate" in message.content.lower():
            await message.channel.send("pulling fed data")
            subprocess.run(["Rscript", os.path.join(R_PATH, "fedTarget.R")])
            await message.channel.send("here's the federal funds target rate:", file=discord.File(os.path.join(OUTPUT_PATH, "markets/dfedtaru.png")))

        if "yield curve" in message.content.lower() or "yieldcurve" in message.content.lower():
            await message.channel.send("pulling data from the fed")
            subprocess.run(["Rscript", os.path.join(R_PATH, "yieldCurve.R")])
            await message.channel.send("this is what the yield curve looks like right now:", file=discord.File(os.path.join(OUTPUT_PATH, "markets/yield_curve.png")))

        if message.content.lower() == "yield spread" or message.content.lower() == "yieldspread":
            await message.channel.send("pulling fed data")
            subprocess.run(["Rscript", os.path.join(R_PATH, "yieldSpreade.R")])
            await message.channel.send("historical yield spreads:", file=discord.File(os.path.join(OUTPUT_PATH, "markets/yield_spread.png")))

        if "yield spread short" in message.content.lower() or "yieldspreadshort" in message.content.lower() or "yss" in message.content.lower():
            await message.channel.send("pulling data from fed")
            subprocess.run(["Rscript", os.path.join(R_PATH, "yieldSpreadShort.R")])
            await message.channel.send("last 2 months of yield spreads:", file=discord.File(os.path.join(OUTPUT_PATH, "markets/yield_spread_2mo.png")))

        if "jax planes" in message.content.lower():
            await message.channel.send("*looking up*")
            subprocess.run(["python3", os.path.join(PYTHON_PATH, "overJax.py")])
            await message.channel.send("saw planes, making plot")
            #subprocess.run(["Rscript", os.path.join(R_PATH, "planePlot.R")])
            await message.channel.send("here you go:", file=discord.File(os.path.join(OUTPUT_PATH, "aerospace/adsb250nm_map.html")))

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