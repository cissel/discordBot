import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env into the environment

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def chat_with_gpt():
    print("Welcome to the GPT chatbot! Type 'exit' to quit.")

    conversation = [
        {"role": "system", "content": "You are a chill bot just hanging out in a groupchat with some of your closest friends - they are surfers, gamers, coders, traders, gamblers, musicians, athletes, artists, alchoholics, and stoners"}
    ]

    while True:
        user_input = input("You: ")
        if user_input.lower() in {"exit", "quit"}:
            break

        conversation.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=conversation
            )
            reply = response.choices[0].message.content
            print(f"GPT: {reply}")
            conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            print("⚠️ Error:", e)

if __name__ == "__main__":
    chat_with_gpt()
