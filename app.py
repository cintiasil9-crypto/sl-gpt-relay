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
# KEYWORD BUCKETS
# =================================================

FLIRTY_PHRASES = [
    "you're cute", "youre cute", "you're hot", "i like you",
    "come here", "come sit", "miss you"
]
FLIRTY_WORDS = ["cute", "hot", "sexy", "babe", "darling"]

SEXUAL_PHRASES = [
    "fuck me", "suck my", "ride you", "make you cum", "bend over"
]

CURSE_WORDS = [
    "fuck", "shit", "bullshit", "asshole", "bitch", "damn"
]

# =================================================
# PROFILE BUILD (FROM OBSERVATIONS)
# =================================================

def build_profiles(force=False):
    now = time.time()
    if CACHE["profiles"] and not force and now - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    rows = fetch_gviz_rows(GOOGLE_OBSERVATIONS_FEED)
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
            "words": 0,
            "traits": {
                "engaging": 0,
                "concise": 0,
                "combative": 0,
                "humorous": 0,
                "curious": 0,
                "dominant": 0,
                "supportive": 0
            },
            "modifiers": {
                "flirty": 0,
                "sexual": 0,
                "curse": 0
            }
        })

        msg_count = max(float(r.get("messages", 1)), 1)
        word_count = max(float(r.get("word_count", 5)), 5)

        p["messages"] += msg_count * w
        p["words"] += word_count * w

        text = (r.get("context_sample", "") or "").lower().strip()
        if not text:
            continue

        # -----------------------
        # TRAITS (DERIVED)
        # -----------------------

        if r.get("questions", 0):
            p["traits"]["curious"] += 1 * w

        if r.get("short_msgs", 0):
            p["traits"]["concise"] += 1 * w

        if r.get("caps", 0) > 5:
            p["traits"]["dominant"] += 0.5 * w

        if any(wrd in text for wrd in ["lol", "haha", "lmao"]):
            p["traits"]["humorous"] += 1 * w

        # -----------------------
        # MODIFIERS (LANGUAGE)
        # -----------------------

        for ph in FLIRTY_PHRASES:
            if ph in text and "you" in text:
                p["modifiers"]["flirty"] += 2 * w

        for wrd in FLIRTY_WORDS:
            if wrd in text and "you" in text:
                p["modifiers"]["flirty"] += 1 * w

        for ph in SEXUAL_PHRASES:
            if ph in text:
                p["modifiers"]["sexual"] += 3 * w

        for cw in CURSE_WORDS:
            if cw in text:
                p["modifiers"]["curse"] += 1 * w

    # =================================================
    # FINALIZE
    # =================================================

    for p in profiles.values():
        m = p["messages"]

        p["confidence"] = min(1.0, math.log(m + 1) / 5) if m > 0 else 0

        norm = {k: (v / m if m else 0) for k, v in p["traits"].items()}
        p["norm"] = norm
        p["top"] = sorted(norm.items(), key=lambda x: x[1], reverse=True)[:3]

        mods = []
        if p["modifiers"]["flirty"] / max(m, 1) >= 0.08:
            mods.append("High Flirt Tendency")
        if p["modifiers"]["sexual"] >= 5:
            mods.append("Explicit Sexual Language")
        if p["modifiers"]["curse"] / max(p["words"], 1) > 0.10:
            mods.append("Heavy Profanity Usage")

        p["modifiers_display"] = mods

    CACHE["profiles"] = profiles
    CACHE["ts"] = now
    return profiles

# =================================================
# FORMATTER
# =================================================

def format_profile(p):
    lines = [
        f"🧠 {p['name']}",
        f"Confidence {round(p['confidence'] * 100)}%"
    ]

    for trait, score in p["top"]:
        lines.append(f"• {trait} ({round(score * 100)}%)")

    if p["modifiers_display"]:
        lines.append("⚠ Modifiers: " + ", ".join(p["modifiers_display"]))

    return "\n".join(lines)

# =================================================
# ENDPOINTS
# =================================================

@app.route("/list_profiles", methods=["GET"])
def list_profiles():
    profiles = build_profiles()
    blocks = ["📊 Social Profiles:"]
    for p in profiles.values():
        if p["messages"] < 2:
            continue
        blocks.append("")
        blocks.append(format_profile(p))
    return Response("\n".join(blocks), mimetype="text/plain")

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    blocks = []
    for u in uuids:
        p = profiles.get(u)
        blocks.append("")
        blocks.append(format_profile(p) if p else "🧠 Unknown Avatar\nNo rating yet.")
    return Response("\n".join(blocks), mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
