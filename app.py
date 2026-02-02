from flask import Flask, request, jsonify
from openai import OpenAI
import os, time, math, random, requests, json, re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

GOOGLE_SHEET_ENDPOINT = os.environ.get("GOOGLE_SHEET_ENDPOINT")
GOOGLE_PROFILES_FEED  = os.environ["GOOGLE_PROFILES_FEED"]
PROFILE_BUILD_KEY     = os.environ["PROFILE_BUILD_KEY"]

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {"data": None, "ts": 0}
CACHE_TTL = 300  # seconds

# =================================================
# GVIZ PARSER (REAL FIX)
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20
    )

    text = r.text

    # Google ALWAYS wraps GVIZ like this:
    # /*O_o*/ google.visualization.Query.setResponse({...});
    match = re.search(r"setResponse\((\{.*\})\);?", text, re.S)
    if not match:
        raise ValueError("GVIZ payload not found")

    payload = json.loads(match.group(1))
    table = payload["table"]

    cols = [c["label"] for c in table["cols"]]
    rows = []

    for r in table["rows"]:
        row = {}
        for i, cell in enumerate(r["c"]):
            row[cols[i]] = cell["v"] if cell else 0
        rows.append(row)

    return rows

# =================================================
# TIME DECAY
# =================================================

def decay_weight(ts):
    try:
        age_days = (time.time() - int(ts)) / 86400
        if age_days <= 1: return 1.0
        if age_days <= 7: return 0.6
        return 0.3
    except:
        return 0.0

# =================================================
# HUMOR PERSONAS (GPT)
# =================================================

HUMOR_STYLES = [
    "Dry analytical social commentary. Observational.",
    "Light sarcasm. Internet fluent. No cruelty.",
    "Mock-bureaucratic tone. Amused detachment.",
    "Warm instigation that invites replies."
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
Caps: {stats.get("caps")}
Short: {stats.get("short")}
Questions: {stats.get("questions")}

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

    r = requests.post(GOOGLE_SHEET_ENDPOINT, json=data, timeout=10)
    if r.status_code != 200:
        return jsonify({"error": "Sheet write failed"}), 500

    return jsonify({"status": "ok"})

# =================================================
# PROFILE ENGINE (FULL STATS)
# =================================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles():
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    now = time.time()
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return jsonify(PROFILE_CACHE["data"])

    rows = fetch_gviz_rows(GOOGLE_PROFILES_FEED)

    profiles = {}

    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            ts   = int(float(r["timestamp_utc"]))
        except:
            continue

        w = decay_weight(ts)

        p = profiles.setdefault(uuid, {
            "display_name": r.get("display_name", "Unknown"),
            "t": {
                "messages": 0,
                "questions": 0,
                "caps": 0,
                "short": 0,
                "attention": 0,
                "connector": 0,
                "pleasing": 0,
                "combative": 0,
                "curious": 0,
                "dominant": 0,
                "humor": 0,
                "supportive": 0,
            }
        })

        t = p["t"]
        t["messages"]   += max(float(r.get("messages", 0)), 1) * w
        t["questions"]  += float(r.get("questions", 0)) * w
        t["caps"]       += float(r.get("caps", 0)) * w
        t["short"]      += float(r.get("short_msgs", 0)) * w
        t["attention"]  += float(r.get("kw_attention", 0)) * w
        t["connector"]  += float(r.get("kw_connector", 0)) * w
        t["pleasing"]   += float(r.get("kw_pleasing", 0)) * w
        t["combative"]  += float(r.get("kw_combative", 0)) * w
        t["curious"]    += float(r.get("kw_curious", 0)) * w
        t["dominant"]   += float(r.get("kw_dominant", 0)) * w
        t["humor"]      += float(r.get("kw_humor", 0)) * w
        t["supportive"] += float(r.get("kw_supportive", 0)) * w

    results = {}

    for uuid, p in profiles.items():
        m = max(p["t"]["messages"], 1)
        if m < 5:
            continue

        traits = {
            "curious":      (p["t"]["curious"] + p["t"]["questions"]) / m,
            "dominant":     (p["t"]["dominant"] + p["t"]["caps"]) / m,
            "supportive":   p["t"]["supportive"] / m,
            "humorous":     p["t"]["humor"] / m,
            "engaging":     (p["t"]["attention"] + p["t"]["connector"]) / m,
            "combative":    p["t"]["combative"] / m,
            "concise":      1 - (p["t"]["short"] / m),
        }

        results[uuid] = {
            "display_name": p["display_name"],
            "confidence": round(min(1.0, math.log(m + 1) / 5), 2),
            "top_traits": sorted(
                [{"trait": k, "score": round(v, 2)} for k, v in traits.items()],
                key=lambda x: x["score"],
                reverse=True
            )
        }

    PROFILE_CACHE.update({"data": results, "ts": now})
    return jsonify(results)

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
