from flask import Flask, request, Response
from openai import OpenAI
import os, time, math, requests, json, re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

GOOGLE_PROFILES_FEED  = os.environ["GOOGLE_PROFILES_FEED"]
PROFILE_BUILD_KEY     = os.environ["PROFILE_BUILD_KEY"]

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {"data": None, "ts": 0}
GPT_CACHE = {}  # uuid -> {summary, tier, ts}

CACHE_TTL = 300
GPT_TTL   = 1800  # 30 min

# =================================================
# GVIZ PARSER
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    match = re.search(r"setResponse\((\{.*\})\);?", r.text, re.S)
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
# PERSONALITY TIER ENGINE (DETERMINISTIC)
# =================================================

def determine_tier(t):
    if t["engaging"] > 0.6 and t["curious"] > 0.5:
        return "Social Catalyst"
    if t["engaging"] > 0.6 and t["supportive"] > 0.5:
        return "Connector"
    if t["humorous"] > 0.5 and t["engaging"] > 0.4:
        return "Entertainer"
    if t["dominant"] > 0.6 and t["engaging"] > 0.4:
        return "Leader Type"
    if t["combative"] > 0.5 and t["dominant"] > 0.4:
        return "Debater"
    if t["supportive"] > 0.6:
        return "Support Anchor"
    if t["concise"] > 0.6 and t["curious"] > 0.4:
        return "Observer"
    return "Wildcard"

# =================================================
# GPT SUMMARY
# =================================================

def gpt_summary(name, tier, traits, confidence):
    prompt = f"""
Avatar name: {name}
Personality tier: {tier}
Confidence score: {confidence}

Traits (0–1 scale):
{json.dumps(traits, indent=2)}

Write a neutral, in-world personality summary (2–3 sentences).
Avoid assumptions, intent, or real-world claims.
Use phrases like "tends to" or "often".
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.4,
        messages=[
            {"role": "system", "content": "You summarize avatar communication styles for a virtual world."},
            {"role": "user", "content": prompt}
        ]
    )

    return res.choices[0].message.content.strip()

# =================================================
# PROFILE BUILDER
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

    lines = ["📊 Social Profiles\n"]

    for uuid, p in profiles.items():
        m = p["messages"]
        if m < 5:
            continue

        traits_norm = {k: round(v / m, 2) for k, v in p["traits"].items()}
        confidence = round(min(1.0, math.log(m + 1) / 5), 2)
        tier = determine_tier(traits_norm)

        cached = GPT_CACHE.get(uuid)
        if cached and now - cached["ts"] < GPT_TTL:
            summary = cached["summary"]
        else:
            summary = gpt_summary(p["name"], tier, traits_norm, confidence)
            GPT_CACHE[uuid] = {
                "summary": summary,
                "tier": tier,
                "ts": now
            }

        lines.append(f"🧠 {p['name']}")
        lines.append(f"Tier: {tier}")
        lines.append(f"Confidence: {confidence}")
        lines.append(summary)
        lines.append("")

    if len(lines) == 1:
        lines.append("No usable profiles yet.")

    output = "\n".join(lines)
    PROFILE_CACHE["data"] = output
    PROFILE_CACHE["ts"] = now

    return Response(output, mimetype="text/plain")

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
