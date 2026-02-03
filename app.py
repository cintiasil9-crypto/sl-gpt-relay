from flask import Flask, request, Response
import os, time, math, requests, json, re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)

GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]
PROFILE_BUILD_KEY   = os.environ["PROFILE_BUILD_KEY"]

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {"data": None, "ts": 0}
CACHE_TTL = 300  # seconds

# =================================================
# GVIZ PARSER
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
# NORMALIZATION
# =================================================

def normalize_traits(traits, m):
    if m <= 0:
        return {k: 0 for k in traits}
    return {k: v / m for k, v in traits.items()}

# =================================================
# DISPLAY HELPERS
# =================================================

def pct(v):
    return f"{round(v * 100)}%"

def confidence_pct(v):
    return f"Confidence {round(v * 100)}%"

# =================================================
# PERSONALITY ARCHETYPES (REALISTIC THRESHOLDS)
# =================================================

ARCHETYPES = [
    {
        "name": "Debater",
        "rule": lambda t: t["combative"] >= 0.15,
        "summary": "Engages through challenge and assertive dialogue, often steering discussions."
    },
    {
        "name": "Entertainer",
        "rule": lambda t: t["humorous"] >= 0.20,
        "summary": "Uses humor as a primary social tool and keeps interactions lively."
    },
    {
        "name": "Presence Dominator",
        "rule": lambda t: t["dominant"] >= 0.15,
        "summary": "Maintains conversational control and a strong social presence."
    },
    {
        "name": "Support Anchor",
        "rule": lambda t: t["supportive"] >= 0.12,
        "summary": "Provides reassurance and emotional stability within group interactions."
    },
    {
        "name": "Social Catalyst",
        "rule": lambda t: t["engaging"] >= 0.20 and t["curious"] >= 0.08,
        "summary": "Highly social and interaction-driven, actively pulling others into conversation."
    },
    {
        "name": "Quiet Thinker",
        "rule": lambda t: t["concise"] >= 0.20 and t["curious"] >= 0.05,
        "summary": "Speaks selectively but thoughtfully, favoring questions over dominance."
    },
]

def build_personality_summary(norm_traits, top_traits):
    for a in ARCHETYPES:
        if a["rule"](norm_traits):
            return f"{a['name']}: {a['summary']}"

    return f"Primarily {top_traits[0][0]} with secondary tendencies toward {top_traits[1][0]}."

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

        msg_count = max(float(r.get("messages", 0)), 1)
        p["messages"] += msg_count * w

        p["traits"]["concise"]    += (1 - float(r.get("short_msgs", 0))) * w
        p["traits"]["engaging"]   += (float(r.get("kw_attention", 0)) + float(r.get("kw_connector", 0))) * w
        p["traits"]["combative"]  += float(r.get("kw_combative", 0)) * w
        p["traits"]["humorous"]   += float(r.get("kw_humor", 0)) * w
        p["traits"]["curious"]    += (float(r.get("kw_curious", 0)) + float(r.get("questions", 0))) * w
        p["traits"]["dominant"]   += float(r.get("kw_dominant", 0)) * w
        p["traits"]["supportive"] += float(r.get("kw_supportive", 0)) * w

    # =================================================
    # OUTPUT
    # =================================================

    lines = ["📊 Social Profiles:"]

    for p in profiles.values():
        m = p["messages"]
        if m < 5:
            continue

        confidence = min(1.0, math.log(m + 1) / 5)

        lines.append(f"\n🧠 {p['name']}")
        lines.append(f"{confidence_pct(confidence)}")

        norm = normalize_traits(p["traits"], m)
        top = sorted(norm.items(), key=lambda x: x[1], reverse=True)[:3]

        for trait, score in top:
            lines.append(f"• {trait} ({pct(score)})")

        summary = build_personality_summary(norm, top)
        lines.append(f"🧩 {summary}")

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
