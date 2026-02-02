from flask import Flask, request, jsonify
from openai import OpenAI
import os, random
import requests


app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# -------------------------------------------------
# ROTATING HUMOR PERSONAS
# -------------------------------------------------

HUMOR_STYLES = [

    # Dry observer
    """
You are a dry, observant social commentator.
You notice patterns and small behaviors and describe them
with subtle, slightly unimpressed humor.

Rules:
- Observational, not hostile
- No insults
- No protected traits
- No mental health terms
- Do not invent topics
""",

    # Internet sarcastic
    """
You are extremely online and it shows.
You speak with internet-native sarcasm and meme-adjacent phrasing,
but you are never cruel.

Rules:
- Witty, not insulting
- No protected traits
- No mental health terms
- Keep it playful
""",

    # Mock official / analyst
    """
You speak like an overly serious official report
analyzing a very unserious social situation.

Tone:
- Formal
- Deadpan
- Bureaucratic humor

Rules:
- No insults
- No protected traits
- No mental health terms
""",

    # Playful instigator (safe)
    """
You enjoy lightly stirring interaction.
You highlight behavior in a way that invites replies,
without embarrassing or targeting.

Rules:
- Light sarcasm allowed
- No insults
- No protected traits
- No mental health terms
"""
]

# -------------------------------------------------
# TALKER ANALYSIS
# -------------------------------------------------

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    stats = data.get("stats", {})
    context = data.get("context", "")

    persona = random.choice(HUMOR_STYLES)

    prompt = f"""
{persona}

You are generating a humorous but accurate social archetype
for ONE person in a group chat.

You MUST base your output primarily on what was actually discussed.

Conversation (5-minute window):
{context}

Behavior metrics:
Messages: {stats.get("messages")}
Caps Ratio: {stats.get("caps_ratio")}
Short Message Ratio: {stats.get("short_ratio")}
Question Ratio: {stats.get("question_ratio")}

Rules (do not break):
- Must reflect the real conversation
- Light sarcasm is allowed
- No insults
- No protected traits
- No mental health terms
- No sexual content
- Do not invent motivations or topics

Return EXACTLY:
Archetype: <short title>
Description: <one sentence>
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=80
    )

    return jsonify({
        "result": res.choices[0].message.content.strip()
    })

# -------------------------------------------------
# SILENT SPOTLIGHT
# -------------------------------------------------

@app.route("/silent", methods=["POST"])
def silent():
    persona = random.choice([
        "Dry observer.",
        "Lightly sarcastic but kind.",
        "Mock-serious and dramatic.",
        "Playfully observant."
    ])

    prompt = f"""
You are making a funny, observational remark about someone
who has been present but silent in a social space.

Tone:
{persona}

Rules:
- One sentence only
- Light humor
- No accusations
- No insults
- No protected traits
- No mental health terms
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=40
    )

    return jsonify({
        "line": res.choices[0].message.content.strip()
    })
# -------------------------------------------------
# DATA COLLECTOR (NO AI)
# -------------------------------------------------

GOOGLE_SHEET_ENDPOINT = os.environ.get("GOOGLE_SHEET_ENDPOINT")

@app.route("/collect", methods=["POST"])
def collect():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON received"}), 400

    if not GOOGLE_SHEET_ENDPOINT:
        return jsonify({"error": "Google endpoint not configured"}), 500

    try:
        r = requests.post(
            GOOGLE_SHEET_ENDPOINT,
            json=data,
            timeout=10
        )

        if r.status_code != 200:
            return jsonify({
                "error": "Google Sheet error",
                "status": r.status_code,
                "body": r.text
            }), 500

        return jsonify({"status": "ok"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------
# HEALTH CHECK
# -------------------------------------------------

@app.route("/")
def ok():
    return "OK"


