from flask import Flask, request, Response
import os, time, math, requests, json, re

# =================================================
# APP
# =================================================

app = Flask(__name__)

# =================================================
# ENV VARS (ONLY ONE REQUIRED)
# =================================================

GOOGLE_OBSERVATIONS_FEED = os.environ["GOOGLE_OBSERVATIONS_FEED"]

# =================================================
# CACHE
# =================================================

CACHE = {"profiles": {}, "ts": 0}
CACHE_TTL = 300  # 5 minutes

# =================================================
# GVIZ FETCH
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    m = re.search(r"setResponse\((.*)\);?", r.text, re.S)
    if not m:
        raise RuntimeError("Invalid GViz response")

    payload = json.loads(m.group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]
    rows = []

    for row in payload["table"]["rows"]:
        rec = {}
        for i, cell in enumerate(row["c"]):
            rec[cols[i]] = cell["v"] if cell else 0
        rows.append(rec)

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
# LANGUAGE BUCKETS
# =================================================

FLIRTY_PHRASES = [
    "you're cute", "youre cute", "you're hot", "i like you",
    "come here", "come sit", "miss you"
]
FLIRTY_WORDS = ["cute", "hot", "sexy", "babe", "darling"]

SEXUAL_PHRASES = [
    "fuck me", "suck my", "ride you", "make you cum", "bend over"
]

CURSE_WORDS = ["fuck", "shit", "bullshit", "asshole", "bitch", "damn"]

# =================================================
# ARCHETYPE RESOLUTION (RESTORED)
# =================================================

def resolve_archetype(p):
    m = p["messages"]
    t = p["traits"]
    mods = p["modifiers"]

    if m < 5:
        return "Wallflower"

    if mods["sexual"] >= 5:
        return "Explicit Flirt"

    if mods["flirty"] / max(m, 1) >= 0.1:
        return "Flirt"

    if t["dominant"] > 0.4 and mods["curse"] > 3:
        return "Instigator"

    if t["humorous"] > 0.3 and m > 10:
        return "Entertainer"

    if m > 25:
        return "Social Constant"

    if t["curious"] > 0.3:
        return "Icebreaker"

    return "Observer"

# =================================================
# PROFILE BUILDER
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
            ts = int(float(r["timestamp_utc"]))
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

        msg = max(float(r.get("messages", 1)), 1)
        words = max(float(r.get("word_count", 5)), 5)

        p["messages"] += msg * w
        p["words"] += words * w

        text = (r.get("context_sample", "") or "").lower()

        # TRAITS
        if r.get("questions", 0):
            p["traits"]["curious"] += w

        if r.get("short_msgs", 0):
            p["traits"]["concise"] += w

        if r.get("caps", 0) > 5:
            p["traits"]["dominant"] += 0.5 * w

        if any(x in text for x in ["lol", "haha", "lmao"]):
            p["traits"]["humorous"] += w

        # MODIFIERS
        for ph in FLIRTY_PHRASES:
            if ph in text:
                p["modifiers"]["flirty"] += 2 * w

        for wrd in FLIRTY_WORDS:
            if wrd in text:
                p["modifiers"]["flirty"] += w

        for ph in SEXUAL_PHRASES:
            if ph in text:
                p["modifiers"]["sexual"] += 3 * w

        for cw in CURSE_WORDS:
            if cw in text:
                p["modifiers"]["curse"] += w

    # FINALIZE
    for p in profiles.values():
        m = p["messages"]
        p["confidence"] = min(1.0, math.log(m + 1) / 5) if m > 0 else 0

        norm = {k: (v / m if m else 0) for k, v in p["traits"].items()}
        p["norm"] = norm
        p["top"] = sorted(norm.items(), key=lambda x: x[1], reverse=True)[:3]

        p["archetype"] = resolve_archetype(p)

        flags = []
        if p["modifiers"]["flirty"] / max(m, 1) >= 0.1:
            flags.append("Flirty")
        if p["modifiers"]["sexual"] >= 5:
            flags.append("Explicit")
        if p["modifiers"]["curse"] / max(p["words"], 1) > 0.1:
            flags.append("Profanity")

        p["flags"] = flags

    CACHE["profiles"] = profiles
    CACHE["ts"] = now
    return profiles

# =================================================
# FORMATTER (SL FRIENDLY)
# =================================================

def format_profile(p):
    lines = [
        f"🧠 {p['name']}",
        f"Archetype: {p['archetype']}",
        f"Confidence: {round(p['confidence'] * 100)}%"
    ]

    for trait, score in p["top"]:
        lines.append(f"• {trait} ({round(score * 100)}%)")

    if p["flags"]:
        lines.append("⚠ " + ", ".join(p["flags"]))

    return "\n".join(lines)

# =================================================
# ENDPOINTS
# =================================================

@app.route("/list_profiles", methods=["GET"])
def list_profiles():
    profiles = build_profiles()
    out = ["📊 Social Profiles:"]
    for p in profiles.values():
        if p["messages"] < 2:
            continue
        out.append("")
        out.append(format_profile(p))
    return Response("\n".join(out), mimetype="text/plain")

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    out = []
    for u in uuids:
        p = profiles.get(u)
        out.append("")
        out.append(format_profile(p) if p else "🧠 Unknown Avatar\nNo rating yet.")
    return Response("\n".join(out), mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
