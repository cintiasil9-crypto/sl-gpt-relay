from flask import Flask, request, Response
import os, time, math, requests, json, re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)

GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]
PROFILE_BUILD_KEY   = os.environ.get("PROFILE_BUILD_KEY", "")

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
    ("Debater",            lambda t: t["combative"] >= 0.15),
    ("Entertainer",        lambda t: t["humorous"] >= 0.20),
    ("Presence Dominator", lambda t: t["dominant"] >= 0.15),
    ("Support Anchor",     lambda t: t["supportive"] >= 0.12),
    ("Social Catalyst",    lambda t: t["engaging"] >= 0.20 and t["curious"] >= 0.08),
    ("Quiet Thinker",      lambda t: t["concise"] >= 0.20 and t["curious"] >= 0.05),
]

# =================================================
# PROFILE BUILD
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
                "concise": 0,
                "combative": 0,
                "humorous": 0,
                "curious": 0,
                "dominant": 0,
                "supportive": 0
            }
        })

        msg = max(float(r.get("messages", 1)), 1)
        p["messages"] += msg * w

        p["traits"]["engaging"]   += (float(r.get("kw_attention", 0)) + float(r.get("kw_connector", 0))) * w
        p["traits"]["concise"]    += (1 - float(r.get("short_msgs", 0))) * w
        p["traits"]["combative"]  += float(r.get("kw_combative", 0)) * w
        p["traits"]["humorous"]   += float(r.get("kw_humor", 0)) * w
        p["traits"]["curious"]    += (float(r.get("kw_curious", 0)) + float(r.get("questions", 0))) * w
        p["traits"]["dominant"]   += float(r.get("kw_dominant", 0)) * w
        p["traits"]["supportive"] += float(r.get("kw_supportive", 0)) * w

    # Finalize
    for p in profiles.values():
        m = p["messages"]
        p["confidence"] = min(1.0, math.log(m + 1) / 5) if m > 0 else 0

        trait_sum = sum(p["traits"].values()) or 1
        norm = {k: v / trait_sum for k, v in p["traits"].items()}
        p["norm"] = norm

        p["top"] = sorted(norm.items(), key=lambda x: x[1], reverse=True)[:3]

        p["archetype"] = "Unclassified"
        for name, rule in ARCHETYPES:
            if rule(norm):
                p["archetype"] = name
                break

    CACHE["profiles"] = profiles
    CACHE["ts"] = now
    return profiles

# =================================================
# PROFILE FORMATTER
# =================================================

def format_profile(p):
    lines = []
    lines.append(f"🧠 {p['name']}")
    lines.append(f"Confidence {round(p['confidence'] * 100)}%")

    for trait, score in p["top"]:
        lines.append(f"• {trait} ({score * 100:.1f}%)")

    if p["archetype"] != "Unclassified":
        lines.append(f"🧩 {p['archetype']}")
    else:
        lines.append("🧩 Profile forming")

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

# -------------------------------------------------

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    blocks = []

    for u in uuids:
        blocks.append("")
        p = profiles.get(u)
        if p:
            blocks.append(format_profile(p))
        else:
            blocks.append("🧠 Unknown Avatar\nNo rating yet.")

    return Response("\n".join(blocks), mimetype="text/plain")

# -------------------------------------------------

@app.route("/scan_now", methods=["POST"])
def scan_now():
    return Response("Scan triggered.", mimetype="text/plain")

# -------------------------------------------------

@app.route("/")
def ok():
    return "OK"
