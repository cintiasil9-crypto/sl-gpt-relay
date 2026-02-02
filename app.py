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
# GVIZ PARSER (CORRECT)
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()

    text = r.text.strip()

    # Google error pages / quota pages
    if not text.startswith("google.visualization"):
        raise ValueError("Invalid gviz response")

    match = re.search(r"setResponse\((.*)\);?$", text, re.S)
    if not match:
        raise ValueError("GVIZ payload not found")

    payload = json.loads(match.group(1))
    table = payload["table"]

    # IMPORTANT: use id first, label is often blank
    cols = [(c.get("id") or c.get("label")) for c in table["cols"]]

    rows = []
    for r in table["rows"]:
        row = {}
        for i, cell in enumerate(r["c"]):
            row[cols[i]] = cell["v"] if cell else None
        rows.append(row)

    return rows

# =================================================
# TIME DECAY
# =================================================

def decay_weight(timestamp_utc):
    try:
        now = int(time.time())
        age_days = (now - int(timestamp_utc)) / 86400

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
        if r.status_code == 200:
            return jsonify({"status": "ok"})
        return jsonify({"error": "Sheet write failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =================================================
# PROFILE ENGINE (FINAL)
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

    try:
        rows = fetch_gviz_rows(GOOGLE_PROFILES_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    profiles = {}

    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            timestamp = int(float(r["timestamp_utc"]))
        except Exception:
            continue

        weight = decay_weight(timestamp)
        msgs = max(float(r.get("messages", 0)), 1.0) * weight

        p = profiles.setdefault(uuid, {
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
        })

        t = p["t"]
        t["messages"] += msgs
        t["attention"] += float(r.get("kw_attention", 0)) * weight
        t["pleasing"] += float(r.get("kw_pleasing", 0)) * weight
        t["combative"] += float(r.get("kw_combative", 0)) * weight
        t["curious"] += (float(r.get("kw_curious", 0)) + float(r.get("questions", 0))) * weight
        t["dominant"] += float(r.get("kw_dominant", 0)) * weight
        t["humor"] += float(r.get("kw_humor", 0)) * weight
        t["supportive"] += float(r.get("kw_supportive", 0)) * weight
        t["caps"] += float(r.get("caps", 0)) * weight
        t["short"] += float(r.get("short_msgs", 0)) * weight

    results = {}

    for uuid, p in profiles.items():
        m = max(p["t"]["messages"], 1.0)
        if m < 5:
            continue

        traits = {
            "curious": p["t"]["curious"] / m,
            "dominant_presence": (p["t"]["dominant"] + p["t"]["caps"] + m) / (3 * m),
            "supportive": p["t"]["supportive"] / m,
            "humorous": p["t"]["humor"] / m,
            "energetic": (p["t"]["caps"] + p["t"]["attention"]) / (2 * m)
        }

        top = sorted(traits.items(), key=lambda x: x[1], reverse=True)

        results[uuid] = {
            "display_name": p["display_name"],
            "confidence": round(min(1.0, math.log(m + 1) / 5), 2),
            "top_traits": [
                {"trait": k, "score": round(v, 2)}
                for k, v in top[:5]
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
