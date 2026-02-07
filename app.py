from flask import Flask, Response, jsonify, request
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]

CACHE = {"profiles": None, "ts": 0}
CACHE_TTL = 300
NOW = time.time()

# =================================================
# WEIGHTS
# =================================================

TRAIT_WEIGHTS = {
    "engaging":   1.0,
    "curious":    0.9,
    "humorous":   1.2,
    "supportive": 1.1,
    "dominant":   1.0,
    "combative":  1.4,
}

STYLE_WEIGHTS = {
    "flirty": 1.0,
    "sexual": 1.2,
    "curse":  0.9
}

# =================================================
# KEYWORDS
# =================================================

ENGAGING = {"hi","hey","yo","sup","wb","welcome"}
CURIOUS = {"why","how","what","where","when","who"}
HUMOR = {"lol","lmao","haha","rofl"}
SUPPORT = {"sorry","hope","ok","there","np","hug","hugs","here"}
DOMINANT = {"listen","look","stop","wait","now"}
COMBATIVE = {"idiot","stupid","shut","wtf","dumb"}

FLIRTY = {"cute","hot","handsome","beautiful","kiss","xoxo","flirt"}
SEXUAL = {"sex","fuck","horny","wet","hard","naked"}
CURSE = {"fuck","shit","damn","bitch","asshole"}

NEGATORS = {"not","no","never","dont","can't","isn't"}

# =================================================
# SUMMARY PHRASES
# =================================================

PRIMARY_PHRASE = {
    "engaging": "Naturally pulls people into conversation",
    "curious": "Actively curious about who’s around",
    "humorous": "Shows up to entertain",
    "supportive": "Creates emotional safety",
    "dominant": "Carries main-character energy",
    "combative": "Thrives on strong opinions",
}

SECONDARY_PHRASE = {
    "engaging": "keeps interactions flowing",
    "curious": "asks thoughtful questions",
    "humorous": "keeps things playful",
    "supportive": "softens heavy moments",
    "dominant": "steers conversations",
    "combative": "pushes back when challenged",
}

TERTIARY_PHRASE = {
    "engaging": "without forcing attention",
    "curious": "while quietly observing",
    "humorous": "often with a playful edge",
    "supportive": "in a grounding way",
    "dominant": "with subtle authority",
    "combative": "with occasional friction",
}

MODIFIER_PHRASE = {
    ("curious","flirty"): "with light romantic curiosity",
    ("humorous","flirty"): "through playful flirtation",
    ("supportive","flirty"): "with warm, gentle flirtation",
    ("dominant","flirty"): "with confident flirtation",
    ("curious","sexual"): "with adult curiosity",
    ("supportive","sexual"): "with emotional intimacy",
    ("dominant","sexual"): "with bold adult energy",
    ("humorous","sexual"): "using shock humor",
    ("humorous","curse"): "with crude humor",
    ("dominant","curse"): "in a forceful, unfiltered way",
    ("supportive","curse"): "in a familiar, casual tone",
}

# =================================================
# SL CHAT SAFE VISUAL HELPERS
# =================================================

SEP = "===================="

def bar(v, width=5):
    filled = int(round((v / 100) * width))
    return "#" * filled + "-" * (width - filled)

def row(label, value):
    return f"{label:<11} {bar(value)} {value:>3}%"

# =================================================
# HELPERS
# =================================================

def decay(ts):
    age_hrs = (NOW - ts) / 3600
    if age_hrs <= 1: return 1.0
    if age_hrs <= 24: return 0.7
    return 0.4

def extract_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b", text.lower())

    def neg(i):
        return any(w in NEGATORS for w in words[max(0,i-3):i])

    for i,w in enumerate(words):
        if neg(i): continue
        if w in ENGAGING: hits["engaging"] += 1
        if w in CURIOUS: hits["curious"] += 1
        if w in HUMOR: hits["humorous"] += 1
        if w in SUPPORT: hits["supportive"] += 1
        if w in DOMINANT: hits["dominant"] += 1
        if w in COMBATIVE: hits["combative"] += 1
        if w in FLIRTY: hits["flirty"] += 1
        if w in SEXUAL: hits["sexual"] += 1
        if w in CURSE: hits["curse"] += 1

    return hits

# =================================================
# DATA FETCH
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    m = re.search(r"setResponse\((\{.*\})\)", r.text, re.S)
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
# SUMMARY ENGINE
# =================================================

def build_summary(conf, traits, styles):
    if conf < 0.25:
        return "Barely spoke. Vibes pending."

    ranked = sorted(traits.items(), key=lambda x: x[1], reverse=True)
    top = [k for k,v in ranked if v > 0][:3]

    if not top:
        return "Present, but patterns are still forming."

    parts = [
        PRIMARY_PHRASE.get(top[0]),
        SECONDARY_PHRASE.get(top[1]) if len(top) > 1 else None,
        TERTIARY_PHRASE.get(top[2]) if len(top) > 2 else None
    ]

    base = ", ".join(p for p in parts if p) + "."

    for m in ["sexual","flirty","curse"]:
        if styles.get(m,0) >= 0.2 and conf >= 0.35:
            phrase = MODIFIER_PHRASE.get((top[0], m))
            if phrase:
                return base + " " + phrase + "."

    return base

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    rows = fetch_rows()
    profiles = {}

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid:
            continue

        ts = float(r.get("timestamp", NOW))
        w = decay(ts)

        p = profiles.setdefault(uid,{
            "avatar_uuid": uid,
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "raw_traits": defaultdict(float),
            "raw_styles": defaultdict(float),
            "recent": 0
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w
        if NOW - ts < 3600:
            p["recent"] += msgs

        hits = extract_hits(r.get("context_sample",""))
        for k,v in hits.items():
            if k in TRAIT_WEIGHTS:
                p["raw_traits"][k] += v * TRAIT_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["raw_styles"][k] += v * STYLE_WEIGHTS[k] * w

    out = []
    for p in profiles.values():
        m = max(p["messages"],1)
        confidence = min(1.0, math.log(m + 1) / 4)

        traits = {k:int(min((p["raw_traits"][k]/m),1.0)*100) for k in TRAIT_WEIGHTS}
        styles = {k:int(min((p["raw_styles"][k]/m),1.0)*100) for k in STYLE_WEIGHTS}

        risk = int(min((traits["combative"] + styles["curse"]) * 0.5, 100))
        club = int(min((traits["dominant"] + styles["sexual"]) * 0.5, 100))
        hangout = int(min((traits["supportive"] + traits["curious"]) * 0.5, 100))

        badges = []
        if traits["humorous"] > 55: badges.append("Comedy MVP")
        if traits["supportive"] > 50: badges.append("Comfort Avatar")
        if confidence > 0.75: badges.append("High Signal")

        pretty_text = (
            f"{SEP}\n"
            "SOCIAL PROFILE\n"
            f"{SEP}\n"
            f"Avatar: {p['name']}\n"
            f"Vibe: {'Active' if p['recent'] > 3 else 'Just Vibing'}\n"
            f"Confidence: {bar(int(confidence*100))} {int(confidence*100)}%\n\n"

            "PERSONALITY\n"
            + row("Engaging", traits["engaging"]) + "\n"
            + row("Curious", traits["curious"]) + "\n"
            + row("Humorous", traits["humorous"]) + "\n"
            + row("Supportive", traits["supportive"]) + "\n"
            + row("Dominant", traits["dominant"]) + "\n"
            + row("Combative", traits["combative"]) + "\n\n"

            "STYLE\n"
            + row("Flirty", styles["flirty"]) + "\n"
            + row("Sexual", styles["sexual"]) + "\n"
            + row("Curse", styles["curse"]) + "\n\n"

            "ENERGY\n"
            + row("Hangout", hangout) + "\n"
            + row("Club", club) + "\n"
            + row("Risk", risk) + "\n\n"

            "BADGES\n"
            + (", ".join(badges) if badges else "None") + "\n\n"

            "Summary\n"
            + build_summary(confidence, traits, styles) + "\n"
            f"{SEP}"
        )

        out.append({
            "avatar_uuid": p["avatar_uuid"],
            "name": p["name"],
            "pretty_text": pretty_text
        })

    CACHE["profiles"] = out
    CACHE["ts"] = time.time()
    return out

# =================================================
# HUD ENDPOINTS
# =================================================

@app.route("/profile/self", methods=["POST"])
def profile_self():
    data = request.get_json(silent=True) or {}
    uuid = data.get("uuid")
    for p in build_profiles():
        if p["avatar_uuid"] == uuid:
            return jsonify(p)
    return jsonify({"error": "profile not found"}), 404

@app.route("/profile/<uuid>", methods=["GET"])
def profile_by_uuid(uuid):
    for p in build_profiles():
        if p["avatar_uuid"] == uuid:
            return jsonify(p)
    return jsonify({"error": "profile not found"}), 404


# =================================================
# WEBSITE ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    return Response(
        json.dumps(build_profiles()),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"}
    )

@app.route("/")
def ok():
    return "OK", 200
