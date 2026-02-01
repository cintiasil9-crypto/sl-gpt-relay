from flask import Flask, request, jsonify
from openai import OpenAI
import os

app = Flask(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json

    stats = data.get("stats", {})

    prompt = f"""
You are assigning a humorous but non-hostile social archetype
based ONLY on behavioral metrics.

Rules:
- No insults
- No mental health terms
- No sexuality, gender, or protected traits
- Tone: witty, observational, light

Metrics:
Messages: {stats.get("messages")}
Average Length: {stats.get("avg_length")}
Caps Ratio: {stats.get("caps_ratio")}
Emoji Ratio: {stats.get("emoji_ratio")}
Question Ratio: {stats.get("question_ratio")}

Return EXACTLY this format:
Archetype: <short title>
Description: <one sentence>
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=80
    )

    text = response.choices[0].message.content

    return jsonify({"result": text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
