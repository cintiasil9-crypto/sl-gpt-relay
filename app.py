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

PROFILE_CACHE = {"data": None, "profiles": {}, "ts": 0}
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

def confidence_label(v):
    return f"Confidence {round(v * 100)}%"

# =================================================
# ARCHETYPES (ORDER MATTERS)
# =================================================

ARCHETYPES = [
    {
        "name": "Debater",
        "rule": lambda t: t["combative"] >= 0.15,
        "summary": "Engages through challenge and assertive dialogue."
    },
    {
        "name": "Entertainer",
        "rule": lambda t: t["humorous"] >= 0.20,
        "summary": "Uses humor as a primary social tool."
    },
    {
        "name": "Presence Dominator",
        "rule": lambda t: t["dominant"] >= 0.15,
        "summary": "Maintains conversational control and strong presence."
    },
    {
        "name": "Support Anchor",
        "rule": lambda t: t["supportive"] >= 0.12,
        "summary": "Provides reassurance and emotional stability."
    },
    {
        "name": "Social Catalyst",
        "rule": lambda t: t["engaging"] >= 0.20 and t["curious"] >= 0.08,
        "summary": "Actively pulls others into conversation."
    },
    {
        "name": "Quiet Thinker",
        "rule": lambda t: t["concise"] >= 0.20 and t["curious"] >= 0.05,
        "summary": "Speaks selectively and thoughtfully."
    },
]

def resolve_archetype(norm, top):
    for a in ARCHETYPES:
        if a["rule"](norm):
            return a["name"], a["summary"]
    return "Unclassified", f"Primarily {top[0][0]} with secondary {top[1][0]}."

# =================================================
# CORE PROFILE BUILD (USED BY ALL ENDPOINTS)
# =================================================

def build_profiles(force=False):
    now = time.time()
    if PROFILE_CACHE["profiles"] and not force and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return PROFILE_CACHE["profiles"]

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
            "uuid": uuid,
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

    # Finalize
    for p in profiles.values():
        m = p["messages"]
        p["confidence"] = min(1.0, math.log(m + 1) / 5) if m > 0 else 0
        p["norm"] = normalize_traits(p["traits"], m)

        top = sorted(p["norm"].items(), key=lambda x: x[1], reverse=True)
        archetype, summary = resolve_archetype(p["norm"], top)

        p["archetype"] = archetype
        p["summary"] = summary
        p["top"] = top[:3]

    PROFILE_CACHE["profiles"] = profiles
    PROFILE_CACHE["ts"] = now
    return profiles

# =================================================
# ENDPOINTS
# =================================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles_endpoint():
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return Response("Unauthorized", status=401)

    profiles = build_profiles(force=True)
    lines = ["📊 Social Profiles:"]

    for p in profiles.values():
        if p["messages"] < 2:
            continue

        lines.append(f"\n🧠 {p['name']}")
        lines.append(confidence_label(p["confidence"]))

        for trait, score in p["top"]:
            lines.append(f"• {trait} ({pct(score)})")

        lines.append(f"🧩 {p['archetype']}: {p['summary']}")

    return Response("\n".join(lines), mimetype="text/plain")

# -------------------------------------------------

@app.route("/list_profiles", methods=["GET"])
def list_profiles():
    profiles = build_profiles()
    lines = []

    for p in profiles.values():
        if p["messages"] < 2:
            continue
        lines.append(
            f"{p['name']} — {pct(p['confidence'])} — {p['archetype']}"
        )

    return Response("\n".join(lines) or "No profiles yet.", mimetype="text/plain")

# -------------------------------------------------

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    out = []
    for u in uuids:
        p = profiles.get(u)
        if not p:
            out.append("No rating yet")
        else:
            out.append(
                f"{p['name']} — {pct(p['confidence'])} — {p['archetype']}"
            )

    return Response("\n".join(out), mimetype="text/plain")

# -------------------------------------------------

@app.route("/scan_now", methods=["POST"])
def scan_now():
    # Marker endpoint for HUD-triggered scans
    return Response("Scan triggered.", mimetype="text/plain")

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
