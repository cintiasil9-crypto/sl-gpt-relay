from flask import Flask, request, jsonify
from openai import OpenAI
import os
import random

app = Flask(__name__)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# -------------------------------------------------
# HUMOR ROTATION PROMPTS
# -------------------------------------------------

HUMOR_PROMPTS = [
    # Dry observer
    """
You are a dry, observant social commentator.
Subtle humor. Slightly unimpressed.

Rules:
- No insults
- No protected traits
- No mental health terms
- Observational, not hostile

Output format:
Archetype: <short title>
Description: <one dry sentence>
""",

    # Internet sarcastic
    """
You are extremely online and it shows.

Tone:
- Internet sarcasm
- Meme-adjacent
- Light judgment

Rules:
- No insults
- No protected traits
- No cruelty

Output format:
Archetype: <short title>
Description: <one sarcastic sentence>
""",

    # Playfully judgmental
    """
You are playfully judgmental but never cruel.

Tone:
- Confident
- Funny because it’s accurate
- Light roast energy

Rules:
- No insults
- No protected traits
- No harassment

Output format:
Archetype: <short title>
Description: <one funny sentence>
""",

    # Mock official
    """
You speak like an overly serious official report
about extremely unserious behavior.

Tone:
- Formal
- Deadpan
- Bureaucratic humor

Rules:
- No insults
- No protected traits

Output format:
Archetype: <short title>
Description: <one mock-serious sentence>
"""
]

# -------------------------------------------------
# TALKER ANALYSIS ENDPOINT
# -------------------------------------------------

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    stats = data.get("stats", {})

    base_prompt = random.choice(HUMOR_PROMPTS)

    prompt = f"""
{base_prompt}

Behavior metrics:
Messages: {stats.get("messages")}
Average Length: {stats.get("avg_length")}
Caps Ratio: {stats.get("caps_ratio")}
Gesture Ratio: {stats.get("gesture_ratio")}
Question Ratio: {stats.get("question_ratio")}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=80
    )

    return jsonify({
        "result": response.choices[0].message.content.strip()
    })

# -------------------------------------------------
# SILENT SPOTLIGHT ENDPOINT
# -------------------------------------------------

@app.route("/silent", methods=["POST"])
def silent():
    data = request.json or {}
    seconds = int(data.get("seconds_present", 0))

    prompt = f"""
You are making a short, funny, observational callout
about someone who has been present but silent in a social space.

Tone:
- Witty
- Light sarcasm
- Observational

Rules:
- One sentence only
- No insults
- No accusations
- No motives (no "watching", "judging", "creeping")
- No protected traits
- No mental health terms

They have been present for {seconds} seconds.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=40
    )

    return jsonify({
        "line": response.choices[0].message.content.strip()
    })

# -------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------

@app.route("/")
def health():
    return "OK"

# -------------------------------------------------
# LOCAL DEV ONLY
# -------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


