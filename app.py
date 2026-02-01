from flask import Flask, request, jsonify
import os
import openai

app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    prompt = data.get("prompt", "")

    if not prompt:
        return jsonify({"error": "No prompt"}), 400

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You speak briefly and naturally."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=80
    )

    return response.choices[0].message.content.strip()

if __name__ == "__main__":
    app.run()
