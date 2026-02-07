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
HUMOR = {"lol","lmao","haha","rofl","😂","🤣"}
SUPPORT = {"sorry","hope","ok","there","np","hug","hugs","here"}
DOMINANT = {"listen","look","stop","wait","now"}
COMBATIVE = {"idiot","stupid","shut","wtf","dumb"}

FLIRTY = {"cute","hot","handsome","beautiful","kiss","xoxo","flirt"}
SEXUAL = {"sex","fuck","horny","wet","hard","naked"}
CURSE = {"fuck","shit","damn","bitch","asshole"}

NEGATORS = {"not","no","never","dont","can't","isn't"}

# =================================================
# SUMMARY PHRASES (UNCHANGED)
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
    ("supportive","sexual"): "with emotional intimacy and adult undertones",
    ("dominant","sexual"): "with bold, adult energy",
    ("humorous","sexual"): "using shock humor",
    ("humorous","curse"): "with crude humor",
    ("dominant","curse"): "in a forceful, unfiltered way",
    ("supportive","curse"): "in a familiar, casual tone",
}

# =================================================
# VISUAL HELPERS (SL CHAT SAFE)
# =================================================

def bar(v, width=5):
    if v <= 0:
        return "▒" * width
    filled = max(1, int(round((v / 100) * width)))
    return "█" * filled + "▒" * (width - filled)

def row(icon, label, value):
    return f"{icon} {label:<11} {bar(value)} {value:>3}%"

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
# SUMMARY ENGINE (UNCHANGED)
# =================================================

def build_summary(conf, traits, styles):
    if conf < 0.25:
        return "Barely spoke. Vibes pending."

    ranked = sorted(traits.items(), key=lambda x: x[1], reverse=True)
    top = [k for k,v in ranked if v > 0][:3]

    if not top:
        return "Present, but patterns are still forming."

    if len(top) == 1:
        return PRIMARY_PHRASE[top[0]] + ". This trait stands out strongly, but there isn’t enough data yet to assess other aspects."

    base = ", ".join([
        PRIMARY_PHRASE[top[0]],
        SECONDARY_PHRASE[top[1]],
        TERTIARY_PHRASE[top[2]] if len(top) > 2 else ""
    ]).strip(", ") + "."

    for m in ["sexual","flirty","curse"]:
        if styles.get(m,0) >= 0.2 and conf >= 0.35:
            phrase = MODIFIER_PHRASE.get((top[0], m))
            if phrase:
                return base + " " + phrase + "."

    return base

# =================================================
# BUILD PROFILES (FULL, RESTORED, LEADERBOARD-SAFE)
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

        p = profiles.setdefault(uid, {
            "avatar_uuid": uid,
            "name": r.get("display_name", "Unknown"),
            "messages": 0,
            "raw_traits": defaultdict(float),
            "raw_styles": defaultdict(float),
            "recent": 0
        })

        msgs = max(int(r.get("messages", 1)), 1)
        p["messages"] += msgs * w

        if NOW - ts < 3600:
            p["recent"] += msgs

        hits = extract_hits(r.get("context_sample", ""))

        for k, v in hits.items():
            if k in TRAIT_WEIGHTS:
                p["raw_traits"][k] += v * TRAIT_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["raw_styles"][k] += v * STYLE_WEIGHTS[k] * w

    out = []

    for p in profiles.values():
        m = max(p["messages"], 1)

        confidence = min(1.0, math.log(m + 1) / 4)
        damp = max(0.05, confidence ** 1.5)

        traits = {
            k: min((p["raw_traits"][k] / m) * damp, 1.0)
            for k in TRAIT_WEIGHTS
        }

        styles = {
            k: min((p["raw_styles"][k] / (m * 0.3)) * damp, 1.0)
            for k in STYLE_WEIGHTS
        }

        # ---------------- ENERGY METRICS (RESTORED) ----------------
        risk = min((traits["combative"] + styles["curse"]) * 0.8, 1.0)
        club = min((traits["dominant"] + styles["sexual"] + styles["curse"]) * 0.6, 1.0)
        hangout = min((traits["supportive"] + traits["curious"]) * 0.6, 1.0)

        vibe = "Active 🔥" if p["recent"] > 3 else "Just Vibing ✨"

        # ---------------- PRETTY PROFILE TEXT ----------------
        pretty_text = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🧠 SOCIAL PROFILE\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Avatar: {p['name']}\n"
            f"🔥 Vibe: {vibe}\n"
            f"📊 Confidence: {bar(int(confidence * 100))} {int(confidence * 100)}%\n\n"
            "🧩 PERSONALITY\n"
            + row("💬", "Engaging", int(traits["engaging"] * 100)) + "\n"
            + row("🧠", "Curious", int(traits["curious"] * 100)) + "\n"
            + row("😂", "Humorous", int(traits["humorous"] * 100)) + "\n"
            + row("🤍", "Supportive", int(traits["supportive"] * 100)) + "\n"
            + row("👑", "Dominant", int(traits["dominant"] * 100)) + "\n"
            + row("⚔", "Combative", int(traits["combative"] * 100)) + "\n\n"
            "💋 STYLE\n"
            + row("💕", "Flirty", int(styles["flirty"] * 100)) + "\n"
            + row("🔞", "Sexual", int(styles["sexual"] * 100)) + "\n"
            + row("🤬", "Curse", int(styles["curse"] * 100)) + "\n\n"
            "🌙 ENERGY\n"
            + row("🎧", "Hangout", int(hangout * 100)) + "\n"
            + row("🎉", "Club", int(club * 100)) + "\n"
            + row("🔥", "Risk", int(risk * 100)) + "\n\n"
            "📝 Summary\n"
            + build_summary(confidence, traits, styles) + "\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        out.append({
            "avatar_uuid": p["avatar_uuid"],
            "name": p["name"],

            "confidence": int(confidence * 100),
            "vibe": vibe,
            "recent": p["recent"],

            "traits": {k: int(v * 100) for k, v in traits.items()},
            "styles": {k: int(v * 100) for k, v in styles.items()},

            "risk": int(risk * 100),
            "club_energy": int(club * 100),
            "hangout_energy": int(hangout * 100),

            "summary": build_summary(confidence, traits, styles),
            "pretty_text": pretty_text
        })

    CACHE["profiles"] = out
    CACHE["ts"] = time.time()
    return out


# =================================================
# ROOM VIBE ADD-ONS (NEW, NON-DESTRUCTIVE)
# =================================================

VIBE_ADJECTIVES = {
    "playful": ["Loose","Lively","Light"],
    "warm": ["Warm","Welcoming","Low-pressure"],
    "flirty": ["Charged","Intimate","Playful"],
    "focused": ["Grounded","Intentional","Purposeful"],
    "tense": ["Sharp","Edgy","Pressurized"],
    "chaotic": ["Unfiltered","Volatile","High-friction"],
    "quiet": ["Calm","Still","Reserved"]
}

_LAST_ADJ = None

def rotate_adjective(vibe):
    global _LAST_ADJ
    for a in VIBE_ADJECTIVES.get(vibe, ["Neutral"]):
        if a != _LAST_ADJ:
            _LAST_ADJ = a
            return a
    return VIBE_ADJECTIVES[vibe][0]

def score_room_vibe(profiles):
    scores = defaultdict(float)
    for p in profiles:
        if p["recent"] <= 0: continue
        t, s = p["traits"], p["styles"]
        scores["playful"] += t["humorous"]
        scores["warm"] += t["supportive"]
        scores["flirty"] += s["flirty"] + s["sexual"]
        scores["focused"] += t["curious"]
        scores["tense"] += t["combative"] + s["curse"]
        if t["combative"] > 50 and s["curse"] > 40:
            scores["chaotic"] += 2
    return scores

def resolve_room_vibe(scores):
    if not scores:
        return "quiet", "Clear"
    ranked = sorted(scores.items(), key=lambda x:x[1], reverse=True)
    top, second = ranked[0], ranked[1] if len(ranked)>1 else ("quiet",0)
    total = sum(scores.values())
    if total == 0:
        return "quiet", "Clear"
    share = top[1] / total
    ratio = top[1] / max(second[1],1)
    if share >= 0.35 and ratio >= 1.3:
        return top[0], "Clear" if share >= 0.5 else "Forming"
    return "quiet", "Shifting"

def build_room_vibe_enhanced(profiles):
    scores = score_room_vibe(profiles)
    vibe, clarity = resolve_room_vibe(scores)
    adjective = rotate_adjective(vibe)

    live = live_chat_summary(profiles)
    presence = presence_summary(profiles)

    pretty = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 ROOM VIBE\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Presence: {presence}\n"
        f"💬 Live Chat: {live}\n"
        f"🎭 Vibe: {vibe.capitalize()}\n"
        f"🧭 Vibe clarity: {clarity}\n\n"
        "🌙 First impression:\n"
        f"{adjective} room. Easy to enter without overcommitting.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    html = {
        "presence": presence,
        "live": live,
        "vibe": vibe,
        "clarity": clarity,
        "impression": f"{adjective} room. Easy to enter without overcommitting."
    }

    return pretty, html

# =================================================
# ROOM VIBE ENDPOINT (BACKWARD-COMPATIBLE)
# =================================================

@app.route("/room/vibe", methods=["POST"])
def room_vibe():
    data = request.get_json(silent=True) or {}
    uuids = set(data.get("uuids", []))

    profiles = [p for p in build_profiles() if p["avatar_uuid"] in uuids]

    legacy = build_hybrid_room_vibe(profiles)
    pretty, html = build_room_vibe_enhanced(profiles)

    return Response(
    json.dumps({
        "text": pretty,          # REQUIRED for current HUD (Script C)
        "legacy_text": legacy,   # backward logic
        "pretty_text": pretty,   # future UI
        "html": html             # web / dashboard
    }, ensure_ascii=False),
    mimetype="application/json; charset=utf-8"
)

# =================================================
# REMAINING ENDPOINTS (UNCHANGED)
# =================================================

@app.route("/profile/self", methods=["POST"])
def profile_self():
    data = request.get_json(silent=True) or {}
    uuid = data.get("uuid")
    for p in build_profiles():
        if p["avatar_uuid"] == uuid:
            return Response(json.dumps(p, ensure_ascii=False), mimetype="application/json; charset=utf-8")
    return jsonify({"error": "profile not found"}), 404

@app.route("/profile/<uuid>", methods=["GET"])
def profile_by_uuid(uuid):
    for p in build_profiles():
        if p["avatar_uuid"] == uuid:
            return Response(json.dumps(p, ensure_ascii=False), mimetype="application/json; charset=utf-8")
    return jsonify({"error": "profile not found"}), 404

@app.route("/profiles/available", methods=["POST"])
def profiles_available():
    data = request.get_json(silent=True) or {}
    uuids = set(data.get("uuids", []))
    return Response(
        json.dumps([{"name":p["name"],"uuid":p["avatar_uuid"]} for p in build_profiles() if p["avatar_uuid"] in uuids], ensure_ascii=False),
        mimetype="application/json; charset=utf-8"
    )

@app.route("/leaderboard")
def leaderboard():
    return Response(json.dumps(build_profiles()), mimetype="application/json", headers={"Access-Control-Allow-Origin":"*"})

@app.route("/")
def ok():
    return "OK", 200
