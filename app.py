from flask import Flask, request, Response
import os, time, math, requests, json, re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)

GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]

# =================================================
# CACHE
# =================================================

CACHE = {"profiles": {}, "ts": 0}
CACHE_TTL = 300  # seconds

# =================================================
# GVIZ PARSER
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    match = re.search(r"setResponse\((\{.*\})\)", r.text, re.S)
    payload = json.loads(match.group(1))

    cols = [c["label"] for c in payload["table"]["cols"]]
    rows = []

    for row in payload["table"]["rows"]:
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
# ARCHETYPES
# =================================================

ARCHETYPES = [
    ("Debater",            lambda t: t["combative"] >= 0.35),
    ("Entertainer",        lambda t: t["humorous"] >= 0.35),
    ("Presence Dominator", lambda t: t["dominant"] >= 0.30),
    ("Support Anchor",     lambda t: t["supportive"] >= 0.30),
    ("Social Catalyst",    lambda t: t["engaging"] >= 0.30 and t["curious"] >= 0.20),
    ("Quiet Thinker",      lambda t: t["concise"] >= 0.60 and t["curious"] >= 0.15),
]

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles(force=False):
    now = time.time()
    if CACHE["profiles"] and not force and now - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

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
                "engaging": 0,
                "curious": 0,
                "humorous": 0,
                "combative": 0,
                "dominant": 0,
                "supportive": 0,
                "concise": 0   # stores short_msgs COUNT
            }
        })

        msg_count = max(int(r.get("messages", 1)), 1)
        p["messages"] += msg_count * w

        # keyword / behavior counts
        p["traits"]["engaging"]   += (r.get("kw_attention", 0) + r.get("kw_connector", 0)) * w
        p["traits"]["curious"]    += (r.get("questions", 0) + r.get("kw_curious", 0)) * w
        p["traits"]["humorous"]   += r.get("kw_humor", 0) * w
        p["traits"]["combative"]  += r.get("kw_combative", 0) * w
        p["traits"]["dominant"]   += r.get("kw_dominant", 0) * w
        p["traits"]["supportive"] += r.get("kw_supportive", 0) * w
        p["traits"]["concise"]    += r.get("short_msgs", 0) * w

    # =================================================
    # FINAL CALCULATIONS
    # =================================================

    for p in profiles.values():
        m = max(int(p["messages"]), 1)

        # --------------------------------
        # CONFIDENCE (DATA RELIABILITY)
        # --------------------------------
        # 5 msgs  ≈ 25%
        # 20 msgs ≈ 55%
        # 50 msgs ≈ 80%
        p["confidence"] = round(
            min(1.0, 1 - math.exp(-m / 20)),
            2
        )

        # --------------------------------
        # NORMALIZED TRAITS (0–1)
        # --------------------------------
        norm = {}

        norm["engaging"]   = min(1.0, p["traits"]["engaging"]   / m)
        norm["curious"]    = min(1.0, p["traits"]["curious"]    / m)
        norm["humorous"]   = min(1.0, p["traits"]["humorous"]   / m)
        norm["combative"]  = min(1.0, p["traits"]["combative"]  / m)
        norm["dominant"]   = min(1.0, p["traits"]["dominant"]   / m)
        norm["supportive"] = min(1.0, p["traits"]["supportive"] / m)

        # Concise = low short-message ratio
        short_ratio = min(1.0, p["traits"]["concise"] / m)
        norm["concise"] = round(1.0 - short_ratio, 2)

        p["norm"] = norm

        # --------------------------------
        # TOP TRAITS
        # --------------------------------
        p["top"] = sorted(
            norm.items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]

        # --------------------------------
        # ARCHETYPE
        # --------------------------------
        p["archetype"] = "Profile forming"
        for name, rule in ARCHETYPES:
            if rule(norm):
                p["archetype"] = name
                break

    CACHE["profiles"] = profiles
    CACHE["ts"] = now
    return profiles

# =================================================
# FORMAT PROFILE (SL CHAT SAFE)
# =================================================

def format_profile(p):
    lines = []
    lines.append(f"🧠 {p['name']}")
    lines.append(f"Confidence {int(p['confidence'] * 100)}%")

    for trait, score in p["top"]:
        lines.append(f"• {trait} ({int(score * 100)}%)")

    lines.append(f"🧩 {p['archetype']}")
    return "\n".join(lines)

# =================================================
# ENDPOINTS
# =================================================

@app.route("/list_profiles", methods=["GET"])
def list_profiles():
    profiles = build_profiles()

    ranked = sorted(
        profiles.values(),
        key=lambda p: p["messages"],
        reverse=True
    )

    blocks = ["📊 Social Profiles:"]
    count = 0

    for p in ranked:
        if p["messages"] < 2:
            continue

        blocks.append("")
        blocks.append(format_profile(p))

        count += 1
        if count >= 5:
            break

    return Response("\n".join(blocks), mimetype="text/plain")

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    blocks = []

    for u in uuids:
        p = profiles.get(u)
        blocks.append("")
        if p:
            blocks.append(format_profile(p))
        else:
            blocks.append("🧠 Unknown Avatar\nNo rating yet.")

    return Response("\n".join(blocks), mimetype="text/plain")

@app.route("/scan_now", methods=["POST"])
def scan_now():
    return Response("Scan triggered.", mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
