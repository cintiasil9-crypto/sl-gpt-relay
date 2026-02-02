from flask import Flask, request, jsonify
from openai import OpenAI
import os
import time
import math
import random
import requests
import json

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

GOOGLE_SHEET_ENDPOINT = os.environ.get("GOOGLE_SHEET_ENDPOINT")
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")
PROFILE_BUILD_KEY = os.environ.get("PROFILE_BUILD_KEY")

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {
    "data": None,
    "ts": 0
}

CACHE_TTL = 300  # seconds

# =================================================
# TIME DECAY
# =================================================

def decay_weight(timestamp_utc):
    try:
        age_seconds = int(time.time()) - int(timestamp_utc)
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
    "Dry, observant social commentary. Subtle humor.",
    "Internet-native sarcasm. Playful.",
    "Mock-official analytical tone.",
    "Light instigation that invites replies."
]

# =================================================
# GPT ANALYSIS
# =================================================

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
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

    return jsonify({
        "result": res.choices[0].message.content.strip()
    })

# =================================================
# SILENT SPOTLIGHT
# =================================================

@app.route("/silent", methods=["POST"])
def silent():
    persona = random.choice([
        "Dry observer",
        "Light sarcasm",
        "Mock-serious",
        "Playfully observant"
    ])

    prompt = f"One sentence. Observational humor. Tone: {persona}"

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=40
    )

    return jsonify({
        "line": res.choices[0].message.content.strip()
    })

# =================================================
# DATA COLLECTOR
# =================================================

@app.route("/collect", methods=["POST"])
def collect():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON body"}), 400

    if not GOOGLE_SHEET_ENDPOINT:
        return jsonify({"error": "GOOGLE_SHEET_ENDPOINT missing"}), 500

    try:
        r = requests.post(GOOGLE_SHEET_ENDPOINT, json=data, timeout=10)
        if r.status_code != 200:
            return jsonify({"error": "Sheet write failed"}), 500
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =================================================
# GVIZ PARSER
# =================================================

def fetch_gviz_rows(url):
    resp = requests.get(url, timeout=20)

    if not resp.text.startswith("google.visualization.Query.setResponse"):
        raise ValueError("Invalid gviz response")

    json_str = resp.text[
        resp.text.find("(") + 1 : resp.text.rfind(")")
    ]

    data = json.loads(json_str)

    cols = [c["label"] for c in data["table"]["cols"]]
    rows = []

    for r in data["table"]["rows"]:
        row = {}
        for i, cell in enumerate(r["c"]):
            row[cols[i]] = cell["v"] if cell else None
        rows.append(row)

    return rows

# =================================================
# PROFILE ENGINE
# =================================================

TRAIT_DESCRIPTIONS = {
    "curious": "Frequently asks questions",
    "declarative": "Makes statements rather than asking",
    "initiator": "Initiates interaction",
    "responsive": "Primarily responds to others",
    "brief": "Uses short messages",
    "verbose": "Uses longer messages",
    "expressive": "Uses emphasis",
    "measured": "Controlled tone",
    "supportive": "Offers reassurance",
    "people_pleasing": "Validates others",
    "challenging": "Pushes back",
    "non_confrontational": "Avoids conflict",
    "connector": "Bridges interactions",
    "independent": "Speaks autonomously",
    "agreeable": "Shows agreement",
    "humorous": "Uses humor",
    "serious": "Keeps a focused tone",
    "playful": "Humor + initiation",
    "reserved": "Speaks selectively",
    "energetic": "High engagement",
    "low_key": "Calm presence",
    "engaged": "Consistent participation",
    "selective": "Rare but intentional",
    "observer": "Mostly silent",
    "dominant_presence": "Commands attention"
}

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
        return jsonify({"error": "GOOGLE_PROFILES_FEED missing"}), 500

    try:
        rows = fetch_gviz_rows(GOOGLE_PROFILES_FEED)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    profiles = {}

    # ---- aggregate ----
    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            ts = int(r["timestamp_utc"])
        except Exception:
            continue

        w = decay_weight(ts)
        msgs = max(float(r.get("messages", 0)), 1.0) * w

        p = profiles.setdefault(uuid, {
            "display_name": r.get("display_name", "Unknown"),
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
        })

        t = p["t"]
        t["messages"] += msgs
        t["attention"] += (r.get("kw_attention", 0) or 0) * w
        t["pleasing"] += (r.get("kw_pleasing", 0) or 0) * w
        t["combative"] += (r.get("kw_combative", 0) or 0) * w
        t["curious"] += ((r.get("kw_curious", 0) or 0) + (r.get("questions", 0) or 0)) * w
        t["dominant"] += (r.get("kw_dominant", 0) or 0) * w
        t["humor"] += (r.get("kw_humor", 0) or 0) * w
        t["supportive"] += (r.get("kw_supportive", 0) or 0) * w
        t["caps"] += (r.get("caps", 0) or 0) * w
        t["short"] += (r.get("short_msgs", 0) or 0) * w

    # ---- derive ----
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
            "people_pleasing": t["pleasing"] / m,
            "challenging": t["combative"] / m,
            "humorous": t["humor"] / m,
            "energetic": (t["caps"] + t["attention"]) / (2 * m),
            "dominant_presence": (t["dominant"] + t["caps"] + m) / (3 * m),
        }

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
def health():
    return "OK"
