from flask import Flask, request, jsonify
import os, time, math, random, requests, json, re

# =====================================
# APP SETUP
# =====================================

app = Flask(__name__)

GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]
PROFILE_BUILD_KEY   = os.environ["PROFILE_BUILD_KEY"]

# =====================================
# CACHE
# =====================================

PROFILE_CACHE = {"data": None, "ts": 0}
CACHE_TTL = 300

# =====================================
# GVIZ PARSER (STABLE)
# =====================================

def fetch_gviz_rows(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    match = re.search(r"setResponse\((\{.*\})\);", r.text, re.S)
    if not match:
        raise ValueError("GVIZ payload not found")

    payload = json.loads(match.group(1))
    table = payload["table"]

    cols = [c["label"] for c in table["cols"]]
    rows = []

    for row in table["rows"]:
        obj = {}
        for i, cell in enumerate(row["c"]):
            obj[cols[i]] = cell["v"] if cell else 0
        rows.append(obj)

    return rows

# =====================================
# DECAY
# =====================================

def decay_weight(ts):
    try:
        age_days = (time.time() - int(ts)) / 86400
        if age_days <= 1: return 1.0
        if age_days <= 7: return 0.6
        return 0.3
    except:
        return 0.0

# =====================================
# PROFILE BUILDER
# =====================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles():
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    now = time.time()
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return jsonify(PROFILE_CACHE["data"])

    rows = fetch_gviz_rows(GOOGLE_PROFILES_FEED)

    raw = {}

    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            ts   = int(float(r["timestamp_utc"]))
        except:
            continue

        w = decay_weight(ts)

        p = raw.setdefault(uuid, {
            "display_name": r.get("display_name", "Unknown"),
            "messages": 0,
            "questions": 0,
            "caps": 0,
            "short": 0,
            "humor": 0,
            "curious": 0,
            "dominant": 0,
            "supportive": 0,
            "engaging": 0,
            "combative": 0,
        })

        p["messages"]   += max(float(r.get("messages", 0)), 1) * w
        p["questions"]  += float(r.get("questions", 0)) * w
        p["caps"]       += float(r.get("caps", 0)) * w
        p["short"]      += float(r.get("short_msgs", 0)) * w
        p["humor"]      += float(r.get("kw_humor", 0)) * w
        p["curious"]    += float(r.get("kw_curious", 0)) * w
        p["dominant"]   += float(r.get("kw_dominant", 0)) * w
        p["supportive"] += float(r.get("kw_supportive", 0)) * w
        p["engaging"]   += float(r.get("kw_attention", 0)) * w
        p["combative"]  += float(r.get("kw_combative", 0)) * w

    profiles = []
    chat_lines = ["📊 Social Profiles:"]

    for uuid, p in raw.items():
        m = max(p["messages"], 1)
        if m < 5:
            continue

        traits = {
            "concise":    1 - (p["short"] / m),
            "engaging":   p["engaging"] / m,
            "combative":  p["combative"] / m,
            "humorous":   p["humor"] / m,
            "curious":    (p["curious"] + p["questions"]) / m,
            "dominant":   (p["dominant"] + p["caps"]) / m,
            "supportive": p["supportive"] / m,
        }

        top = sorted(
            [{"trait": k, "score": round(v, 2)} for k, v in traits.items()],
            key=lambda x: x["score"],
            reverse=True
        )[:3]

        confidence = round(min(1.0, math.log(m + 1) / 5), 2)

        chat = f"🧠 {p['display_name']} ({confidence})"
        for t in top:
            chat += f"\n• {t['trait']} ({t['score']})"

        profiles.append({
            "uuid": uuid,
            "display_name": p["display_name"],
            "confidence": confidence,
            "top_traits": top,
            "chat": chat
        })

        chat_lines.append(chat)

    result = {
        "profiles": profiles,
        "chat_message": "\n".join(chat_lines)
    }

    PROFILE_CACHE.update({"data": result, "ts": now})
    return jsonify(result)

# =====================================
# HEALTH
# =====================================

@app.route("/")
def ok():
    return "OK"
