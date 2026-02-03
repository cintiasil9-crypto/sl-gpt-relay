from flask import Flask, request, Response
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
# GVIZ PARSER (STABLE)
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20
    )

    match = re.search(r"setResponse\((\{.*\})\);?", r.text, re.S)
    if not match:
        raise ValueError("GVIZ payload not found")

    payload = json.loads(match.group(1))
    table = payload["table"]

    cols = [c["label"] for c in table["cols"]]
    rows = []

    for row in table["rows"]:
        record = {}
        for i, cell in enumerate(row["c"]):
            record[cols[i]] = cell["v"] if cell else 0
        rows.append(record)

    return rows

# =================================================
# TIME DECAY
# =================================================

def decay_weight(ts):
    try:
        age_days = (time.time() - int(ts)) / 86400
        if age_days <= 1:
            return 1.0
        if age_days <= 7:
            return 0.6
        return 0.3
    except:
        return 0.0

# =================================================
# PROFILE BUILDER (SL SAFE OUTPUT)
# =================================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles():
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return Response("Unauthorized", status=401)

    now = time.time()
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return Response(PROFILE_CACHE["data"], mimetype="text/plain")

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
            "name": r.get("display_name", "Unknown"),
            "messages": 0,
            "traits": {
                "concise": 0,
                "engaging": 0,
                "combative": 0,
                "humorous": 0,
                "curious": 0,
                "dominant": 0,
                "supportive": 0,
            }
        })

        p["messages"] += max(float(r.get("messages", 0)), 1) * w

        p["traits"]["concise"]    += (1 - float(r.get("short_msgs", 0))) * w
        p["traits"]["engaging"]   += (float(r.get("kw_attention", 0)) + float(r.get("kw_connector", 0))) * w
        p["traits"]["combative"]  += float(r.get("kw_combative", 0)) * w
        p["traits"]["humorous"]   += float(r.get("kw_humor", 0)) * w
        p["traits"]["curious"]    += (float(r.get("kw_curious", 0)) + float(r.get("questions", 0))) * w
        p["traits"]["dominant"]   += float(r.get("kw_dominant", 0)) * w
        p["traits"]["supportive"] += float(r.get("kw_supportive", 0)) * w

    # =================================================
    # BUILD CHAT MESSAGE (PLAIN TEXT)
    # =================================================

    lines = ["📊 Social Profiles:"]

    for p in profiles.values():
        m = p["messages"]
        if m < 5:
            continue

        confidence = round(min(1.0, math.log(m + 1) / 5), 2)
        lines.append(f"🧠 {p['name']} ({confidence})")

        top = sorted(
            p["traits"].items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]

        for trait, score in top:
            lines.append(f"• {trait} ({round(score / m, 2)})")

    if len(lines) == 1:
        lines.append("No usable profiles yet.")

    message = "\n".join(lines)

    PROFILE_CACHE["data"] = message
    PROFILE_CACHE["ts"] = now

    return Response(message, mimetype="text/plain")

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
