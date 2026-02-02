from flask import Flask, request, jsonify
from openai import OpenAI
import os
import random
import math
import time
import requests

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

GOOGLE_SHEET_ENDPOINT = os.environ.get("GOOGLE_SHEET_ENDPOINT")
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")

# =================================================
# TIME DECAY
# =================================================

def decay_weight(timestamp_utc):
    now = int(time.time())
    age_seconds = now - int(timestamp_utc)
    age_days = age_seconds / 86400

    if age_days <= 1:
        return 1.0
    elif age_days <= 7:
        return 0.6
    else:
        return 0.3

# =================================================
# HUMOR PERSONAS (GPT)
# =================================================

HUMOR_STYLES = [
    """
You are a dry, observant social commentator.
Subtle, unimpressed humor.
No insults. No protected traits. No mental health terms.
""",
    """
Internet-native sarcasm.
Playful, not cruel.
No protected traits. No mental health terms.
""",
    """
Mock-official analytical tone.
Dry and bureaucratic.
No insults or protected traits.
""",
    """
Light instigation that invites replies.
Never embarrassing.
No protected traits.
"""
]

# =================================================
# GPT ANALYSIS
# =================================================

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    stats = data.get("stats", {})
    context = data.get("context", "")

    persona = random.choice(HUMOR_STYLES)

    prompt = f"""
{persona}

Conversation:
{context}

Metrics:
Messages: {stats.get("messages")}
Caps: {stats.get("caps_ratio")}
Short: {stats.get("short_ratio")}
Questions: {stats.get("question_ratio")}

Return EXACTLY:
Archetype: <short title>
Description: <one sentence>
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=80
    )

    return jsonify({"result": res.choices[0].message.content.strip()})

# =================================================
# SILENT SPOTLIGHT
# =================================================

@app.route("/silent", methods=["POST"])
def silent():
    persona = random.choice([
        "Dry observer.",
        "Light sarcasm.",
        "Mock-serious.",
        "Playfully observant."
    ])

    prompt = f"""
One sentence.
Funny, observational.
Tone: {persona}
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=40
    )

    return jsonify({"line": res.choices[0].message.content.strip()})

# =================================================
# DATA COLLECTOR
# =================================================

@app.route("/collect", methods=["POST"])
def collect():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON"}), 400
    if not GOOGLE_SHEET_ENDPOINT:
        return jsonify({"error": "Sheet endpoint missing"}), 500

    try:
        r = requests.post(GOOGLE_SHEET_ENDPOINT, json=data, timeout=10)
        if r.status_code != 200:
            return jsonify({"error": "Sheet write failed"}), 500
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =================================================
# PROFILE ENGINE (25 TRAITS + DECAY)
# =================================================

TRAIT_DESCRIPTIONS = {
    "curious": "Frequently asks questions and explores topics",
    "declarative": "Makes statements rather than asking",
    "initiator": "Initiates interaction",
    "responsive": "Primarily responds to others",
    "brief": "Uses short messages",
    "verbose": "Uses longer messages",
    "expressive": "Uses emphasis or intensity",
    "measured": "Maintains controlled tone",

    "supportive": "Offers help or reassurance",
    "people_pleasing": "Smooths or validates interactions",
    "challenging": "Pushes back on statements",
    "non_confrontational": "Avoids conflict",
    "connector": "Bridges social interaction",
    "independent": "Speaks without social reliance",
    "agreeable": "Shows agreement over opposition",

    "humorous": "Uses humor socially",
    "serious": "Keeps a focused tone",
    "playful": "Humor + initiation",
    "reserved": "Speaks selectively",
    "energetic": "High engagement and emphasis",
    "low_key": "Calm presence",

    "engaged": "Participates consistently",
    "selective": "Rare but intentional speech",
    "observer": "Mostly silent presence",
    "dominant_presence": "Commands attention"
}

@app.route("/build_profiles", methods=["POST"])
def build_profiles():
    if not GOOGLE_PROFILES_FEED:
        return jsonify({"error": "Profiles feed missing"}), 500

    rows = requests.get(GOOGLE_PROFILES_FEED, timeout=20).json()
    profiles = {}

    # ---------- Aggregate ----------
    for r in rows:
        uuid = r["avatar_uuid"]
        weight = decay_weight(r["timestamp_utc"])
        msgs = max(int(r["messages"]), 1) * weight

        if uuid not in profiles:
            profiles[uuid] = {
                "display_name": r["display_name"],
                "t": {
                    "messages": 0,
                    "attention": 0,
                    "pleasing": 0,
                    "combative": 0,
                    "curious": 0,
                    "dominant": 0,
                    "humor": 0,
                    "supportive": 0,
                    "caps": 0,
                    "short": 0
                }
            }

        t = profiles[uuid]["t"]

        t["messages"] += msgs
        t["attention"] += int(r["kw_attention"]) * weight
        t["pleasing"] += int(r["kw_pleasing"]) * weight
        t["combative"] += int(r["kw_combative"]) * weight
        t["curious"] += (int(r["kw_curious"]) + int(r["questions"])) * weight
        t["dominant"] += int(r["kw_dominant"]) * weight
        t["humor"] += int(r["kw_humor"]) * weight
        t["supportive"] += int(r["kw_supportive"]) * weight
        t["caps"] += int(r["caps"]) * weight
        t["short"] += int(r["short_msgs"]) * weight

    # ---------- Derive Traits ----------
    results = {}

    for uuid, p in profiles.items():
        t = p["t"]
        m = max(t["messages"], 1)

        traits = {
            "curious": t["curious"] / m,
            "declarative": t["dominant"] / m,
            "initiator": t["attention"] / m,
            "responsive": 1 - (t["attention"] / m),
            "brief": t["short"] / m,
            "verbose": 1 - (t["short"] / m),
            "expressive": t["caps"] / m,
            "measured": 1 - ((t["caps"] + t["short"]) / (2 * m)),

            "supportive": t["supportive"] / m,
            "people_pleasing": t["pleasing"] / m,
            "challenging": t["combative"] / m,
            "non_confrontational": 1 - (t["combative"] / m),
            "connector": (t["supportive"] + t["pleasing"]) / m,
            "independent": 1 - ((t["supportive"] + t["pleasing"]) / (2 * m)),
            "agreeable": (t["pleasing"] + (m - t["combative"])) / (2 * m),

            "humorous": t["humor"] / m,
            "serious": 1 - (t["humor"] / m),
            "playful": (t["humor"] + t["attention"]) / (2 * m),
            "reserved": 1 - ((t["attention"] + t["caps"]) / (2 * m)),
            "energetic": (t["caps"] + t["attention"]) / (2 * m),
            "low_key": 1 - ((t["caps"] + t["attention"]) / (2 * m)),

            "engaged": math.log(m + 1) / 5,
            "selective": (m <= 5) * (1 - t["attention"] / m),
            "observer": (m <= 3) * (1 - t["attention"] / m),
            "dominant_presence": (t["dominant"] + t["caps"] + m) / (3 * m)
        }

        for k in traits:
            traits[k] = max(0.0, min(1.0, traits[k]))

        top = sorted(traits.items(), key=lambda x: x[1], reverse=True)[:5]

        results[uuid] = {
            "display_name": p["display_name"],
            "confidence": round(min(1.0, math.log(m + 1) / 5), 2),
            "top_traits": [
                {
                    "trait": name,
                    "score": round(score, 2),
                    "description": TRAIT_DESCRIPTIONS[name]
                }
                for name, score in top
            ]
        }

    return jsonify(results)

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
