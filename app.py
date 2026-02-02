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
PROFILE_BUILD_KEY = os.environ.get("PROFILE_BUILD_KEY")

# =================================================
# SIMPLE IN-MEMORY CACHE
# =================================================

PROFILE_CACHE = {"data": None, "ts": 0}
CACHE_TTL = 300  # seconds

# =================================================
# TIME DECAY
# =================================================

def decay_weight(timestamp_utc):
    try:
        now = int(time.time())
        age_seconds = now - int(timestamp_utc)
        age_days = age_seconds / 86400

        if age_days <= 1:
            return 1.0
        elif age_days <= 7:
            return 0.6
        else:
            return 0.3
    except Exception:
        return 0.0

# =================================================
# HUMOR PERSONAS (GPT)
# =================================================

HUMOR_STYLES = [
    "Dry, observant social commentary. Subtle humor. No insults.",
    "Internet-native sarcasm. Playful, never cruel.",
    "Mock-official analytical tone. Dry and bureaucratic.",
    "Light instigation that invites replies without embarrassment."
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

    prompt = f"One sentence. Observational humor. Tone: {persona}"

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
# PROFILE ENGINE (25 TRAITS + DECAY + HARDENING)
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
    "playful": "Humor combined with initiation",
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
    # ---- auth ----
    key = request.headers.get("X-Profile-Key")
    if key != PROFILE_BUILD_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    # ---- cache ----
    now = time.time()
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return jsonify(PROFILE_CACHE["data"])

    if not GOOGLE_PROFILES_FEED:
        return jsonify({"error": "Profiles feed missing"}), 500

    rows = requests.get(GOOGLE_PROFILES_FEED, timeout=20).json()
    profiles = {}

    # ---------- Aggregate ----------
    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            timestamp = int(r["timestamp_utc"])
        except Exception:
            continue

        weight = decay_weight(timestamp)
        msgs = max(float(r.get("messages", 0)), 1.0) * weight

        if uuid not in profiles:
            profiles[uuid] = {
                "display_name": r.get("display_name", "Unknown"),
                "t": {
                    "messages": 0.0,
                    "attention": 0.0,
                    "pleasing": 0.0,
                    "combative": 0.0,
                    "curious": 0.0,
                    "dominant": 0.0,
                    "humor": 0.0,
                    "supportive": 0.0,
                    "caps": 0.0,
                    "short": 0.0
                }
            }

        t = profiles[uuid]["t"]

        t["messages"] += msgs
        t["attention"] += int(r.get("kw_attention", 0)) * weight
        t["pleasing"] += int(r.get("kw_pleasing", 0)) * weight
        t["combative"] += int(r.get("kw_combative", 0)) * weight
        t["curious"] += (int(r.get("kw_curious", 0)) + int(r.get("questions", 0))) * weight
        t["dominant"] += int(r.get("kw_dominant", 0)) * weight
        t["humor"] += int(r.get("kw_humor", 0)) * weight
        t["supportive"] += int(r.get("kw_supportive", 0)) * weight
        t["caps"] += int(r.get("caps", 0)) * weight
        t["short"] += int(r.get("short_msgs", 0)) * weight

    # ---------- Derive Traits ----------
    results = {}

    for uuid, p in profiles.items():
        t = p["t"]
        m = max(t["messages"], 1.0)

        if m < 5:
            continue  # minimum signal threshold

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
            "updated_at": int(time.time()),
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

    PROFILE_CACHE["data"] = results
    PROFILE_CACHE["ts"] = now

    return jsonify(results)

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
