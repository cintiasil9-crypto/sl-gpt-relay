from flask import Flask, request
import os
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt")

    if not prompt:
        return "No prompt provided", 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a Second Life avatar. Be casual, short, and natural."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=80
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print("OPENAI ERROR:", e)
        return "OpenAI error", 500


if __name__ == "__main__":
    app.run()



