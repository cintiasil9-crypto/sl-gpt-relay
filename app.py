from flask import Flask, request, jsonify
from openai import OpenAI
import os
import random
import math
import time
import requests
import json
import re

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
# HUMOR PERSONAS
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
# TRAIT DEFINITIONS
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

# =================================================
# PROFILE ENGINE (FIXED GVIZ PARSING)
# =================================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles():
    # ---- auth ----
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    # ---- cache ----
    now = time.time()
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return jsonify(PROFILE_CACHE["data"])

    if not GOOGLE_PROFILES_FEED:
        return jsonify({"error": "Profiles feed missing"}), 500

    # ---- fetch gviz ----
    try:
        resp = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
        text = resp.text

        match = re.search(r"setResponse\((.*)\);?$", text, re.S)
        if not match:
            return jsonify({"error": "Invalid gviz response"}), 500

        data = json.loads(match.group(1))
        rows = data["table"]["rows"]
        cols = [c["label"] for c in data["table"]["cols"]]

    except Exception as e:
        return jsonify({"error": f"Feed parse failed: {str(e)}"}), 500

    profiles = {}

    # ---------- Aggregate ----------
    for row in rows:
        try:
            r = {cols[i]: row["c"][i]["v"] if row["c"][i] else None for i in range(len(cols))}
            uuid = r["avatar_uuid"]
            timestamp = int(r["timestamp_utc"])
        except Exception:
            continue

        weight = decay_weight(timestamp)
        msgs = max(float(r.get("messages", 0)), 1.0) * weight

        if uuid not in profiles:
            profiles[uuid] = {
                "display_name": r.get("display_name", "Unknown"),
                "t": {k: 0.0 for k in [
                    "messages","attention","pleasing","combative","curious",
                    "dominant","humor","supportive","caps","short"
                ]}
            }

        t = profiles[uuid]["t"]

        t["messages"] += msgs
        t["attention"] += (r.get("kw_attention") or 0) * weight
        t["pleasing"] += (r.get("kw_pleasing") or 0) * weight
        t["combative"] += (r.get("kw_combative") or 0) * weight
        t["curious"] += ((r.get("kw_curious") or 0) + (r.get("questions") or 0)) * weight
        t["dominant"] += (r.get("kw_dominant") or 0) * weight
        t["humor"] += (r.get("kw_humor") or 0) * weight
        t["supportive"] += (r.get("kw_supportive") or 0) * weight
        t["caps"] += (r.get("caps") or 0) * weight
        t["short"] += (r.get("short_msgs") or 0) * weight

    # ---------- Derive Traits ----------
    results = {}

    for uuid, p in profiles.items():
        t = p["t"]
        m = max(t["messages"], 1.0)
        if m < 5:
            continue

        traits = {
            "curious": t["curious"] / m,
            "initiator": t["attention"] / m,
            "brief": t["short"] / m,
            "expressive": t["caps"] / m,
            "supportive": t["supportive"] / m,
            "challenging": t["combative"] / m,
            "humorous": t["humor"] / m,
            "dominant_presence": (t["dominant"] + t["caps"]) / (2 * m),
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
                    "description": TRAIT_DESCRIPTIONS.get(name, "")
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
