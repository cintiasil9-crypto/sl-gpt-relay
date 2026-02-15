from flask import Flask, Response, jsonify, request
import os
import time
import math
import requests
import json
import re
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

# ======================================
# SOCIAL TRAIT KEYWORDS — SLANG EXPANDED
# ======================================

ENGAGING = {
    "hi","hey","heya","hiya","yo","sup","wb","welcome",
    "hello","ello","hai","haiii","hii","hiii",
    "o/","\\o","wave","waves","*waves*","*wave*",
    "heyhey","yo yo","sup all","hiya all"
}

CURIOUS = {
    "why","how","what","where","when","who",
    "anyone","anybody","any1","any1?",
    "curious","wonder","wondering",
    "?","??","???","????",
    "huh","eh","hm","hmm","hmmm"
}

HUMOR = {
    "lol","lmao","lmfao","rofl","roflmao",
    "haha","hehe","heh","bahaha",
    "😂","🤣","😆","😜","😹","💀","😭",
    "lawl","lul","lel","ded","im dead","dead 💀"
}

SUPPORT = {
    "sorry","sry","srry","soz",
    "hope","ok","okay","k","kk","mk",
    "there","here","np","nps","no worries",
    "hug","hugs","hugz","*hug*","*hugs*",
    "<3","❤️","💜","💙","💖",
    "u ok","you ok","all good","its ok","it's ok"
}

DOMINANT = {
    "listen","look","stop","wait","now",
    "do it","dont","don't","come here","stay",
    "pay attention","focus","enough",
    "move","sit","stand","follow","watch","hold up"
}

COMBATIVE = {
    "idiot","stupid","dumb","moron","retard",
    "shut","shut up","stfu","gtfo","wtf","tf",
    "screw you","fuck off",
    "trash","garbage","bs","bullshit","smh",
}

# ======================================
# STYLE / TONE — SLANG EXPANDED
# ======================================

FLIRTY = {
    "cute","cutie","qt","hot","handsome","beautiful","pretty",
    "sexy","kiss","kisses","xoxo","mwah","😘","😍","😉","😏",
    "flirt","tease","teasing",
    "hey you","hey sexy","hey cutie","damn u cute","babe","baby","sweety"
}

SEXUAL = {
    "sex","fuck","fucking","horny","wet","hard","naked",
    "dick","cock","pussy","boobs","tits","ass","booty",
    "cum","cumming","breed","breedable",
    "thrust","ride","mount","spread","bed","moan","mm","mmm"
}

CURSE = {
    "fuck","fucking","shit","damn","bitch","asshole",
    "crap","hell","pissed","wtf","ffs","af","asf",
    "omfg","holy shit"
}

# ======================================
# NEGATORS / REVERSALS — SL STYLE
# ======================================

NEGATORS = {
    "not","no","never","dont","don't","cant","can't",
    "isnt","isn't","wasnt","wasn't",
    "aint","ain't","nah","nope","naw",
    "idk","idc","dont care","doesnt matter"
}

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
    age_hrs = (time.time() - ts) / 3600
    if age_hrs <= 1:
        return 1.0
    if age_hrs <= 24:
        return 0.7
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

    now = time.time()

    total_registered_set = set()
    spoke_24h_set = set()
    live_now_set = set()
    power_users_set = set()
    silent_set = set()

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid:
            continue

        total_registered_set.add(uid)

        try:
            ts = float(r.get("timestamp", now))
            msgs = int(r.get("messages", 0))
        except:
            continue

        age = now - ts

        # Spoke in last 24 hours
        if age <= 86400 and msgs > 0:
            spoke_24h_set.add(uid)

        # Live right now (real chat activity)
        if age <= 120 and msgs > 0:
            live_now_set.add(uid)

        # Silent observer (HUD ping but no speech)
        if age <= 300 and msgs == 0:
            silent_set.add(uid)

        # Power users (20+ msgs in last hour)
        if age <= 3600 and msgs >= 20:
            power_users_set.add(uid)

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

        if age < 3600:
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

 # CACHE EVERYTHING INSIDE THE FUNCTION
    CACHE["profiles"] = out
    CACHE["ts"] = time.time()
    CACHE["platform_metrics"] = {
    "total_registered": len(total_registered_set),
    "spoke_24h": len(spoke_24h_set),
    "live_now": len(live_now_set),
    "power_users": len(power_users_set),
    "silent_observers": len(silent_set)
}

    return out
# =================================================
# PLATFORM METRICS
# =================================================

def build_platform_metrics():

    if not CACHE.get("profiles"):
        build_profiles()

    return CACHE.get("platform_metrics", {
        "total_profiles": 0,
        "active_24h": 0,
        "huds_online": 0
    })


# =================================================
# ROOM VIBE HELPERS (REQUIRED)
# =================================================

def presence_summary(profiles):
    if not profiles:
        return "None"

    trait_counts = {
        "Dominant": 0,
        "Humorous": 0,
        "Supportive": 0,
        "Combative": 0
    }

    for p in profiles:
        t = p.get("traits", {})
        if t.get("dominant", 0) >= 40:
            trait_counts["Dominant"] += 1
        if t.get("humorous", 0) >= 40:
            trait_counts["Humorous"] += 1
        if t.get("supportive", 0) >= 40:
            trait_counts["Supportive"] += 1
        if t.get("combative", 0) >= 40:
            trait_counts["Combative"] += 1

    ranked = sorted(trait_counts.items(), key=lambda x: x[1], reverse=True)
    top = [name for name, count in ranked if count > 0][:2]

    return " • ".join(top) if top else "Mixed personalities"


def live_chat_summary(profiles):
    total_recent = sum(p.get("recent", 0) for p in profiles)
    high_conf = sum(1 for p in profiles if p.get("confidence", 0) >= 50)

    if total_recent >= 15:
        return "Buzzing"
    if total_recent >= 6:
        return "Active"
    if total_recent > 0:
        return "Warming Up"

    if high_conf >= 3:
        return "Active"

    return "Quiet"


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
# MATCHING ADD-ONS (NEW, NON-DESTRUCTIVE)
# =================================================

MATCH_ICONS = {
    "similar": "🧬",
    "complement": "🔀",
    "hybrid": "⚖️"
}

def similarity_score(a, b):
    """How alike two avatars are (distance-based)."""
    dist = 0.0
    for k in a["traits"]:
        dist += (a["traits"][k] - b["traits"][k]) ** 2
    return max(0, 100 - math.sqrt(dist))


def complement_score(a, b):
    """How well two avatars balance each other."""
    score = 0.0

    score += min(a["traits"]["dominant"],   b["traits"]["supportive"])
    score += min(b["traits"]["dominant"],   a["traits"]["supportive"])

    score += min(a["traits"]["curious"],    b["traits"]["engaging"])
    score += min(b["traits"]["curious"],    a["traits"]["engaging"])

    score += min(a["styles"]["flirty"],     b["styles"]["flirty"])
    score += min(a["styles"]["sexual"],     b["styles"]["sexual"])

    score -= abs(a["traits"]["combative"] - b["traits"]["combative"])

    return max(0, min(score, 100))


def hybrid_score(similar, complement):
    """Balanced mix of similarity and complement."""
    return (0.6 * similar) + (0.4 * complement)


def find_best_matches(source, profiles):
    """
    Returns one avatar per category:
    similar, complementary, hybrid
    """
    best_similar = (None, -1)
    best_complement = (None, -1)
    best_hybrid = (None, -1)

    for p in profiles:
        if p["avatar_uuid"] == source["avatar_uuid"]:
            continue

        sim = similarity_score(source, p)
        comp = complement_score(source, p)
        hyb = hybrid_score(sim, comp)

        if sim > best_similar[1]:
            best_similar = (p, sim)

        if comp > best_complement[1]:
            best_complement = (p, comp)

        if hyb > best_hybrid[1]:
            best_hybrid = (p, hyb)

    return best_similar[0], best_complement[0], best_hybrid[0]


def build_match_pretty(source, similar, complement, hybrid):
    """
    SL-safe pretty display, mirrors ROOM VIBE + PROFILE style
    """
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💞 BEST MATCHES\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 You: {source['name']}\n\n"

        f"{MATCH_ICONS['similar']} Similar Energy\n"
        f"   {similar['name'] if similar else 'No strong match yet'}\n\n"

        f"{MATCH_ICONS['complement']} Complementary Energy\n"
        f"   {complement['name'] if complement else 'No strong match yet'}\n\n"

        f"{MATCH_ICONS['hybrid']} Hybrid Balance\n"
        f"   {hybrid['name'] if hybrid else 'No strong match yet'}\n\n"

        "🌙 Tip\n"
        "🧬 Similar feels natural\n"
        "🔀 Complement sparks growth\n"
        "⚖️ Hybrid builds long-term flow\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    
build_best_match_pretty = build_match_pretty

# =================================================
# LEADERBOARD ENGINE (NEW, NON-DESTRUCTIVE)
# =================================================

def leaderboard_effective_score(p, raw_value):
    """
    Confidence-weighted + activity-weighted ranking.
    Prevents low-confidence spam from ranking high.
    """
    confidence_weight = p["confidence"] / 100
    activity_bonus = p["recent"] * 2
    return (raw_value * confidence_weight) + activity_bonus


def lb_bar(v, width=8):
    if v <= 0:
        return "▒" * width
    filled = max(1, int(round((v / 100) * width)))
    return "█" * filled + "▒" * (width - filled)


def rank_top3(profiles, key_fn):
    ranked = sorted(
        profiles,
        key=lambda p: leaderboard_effective_score(p, key_fn(p)),
        reverse=True
    )
    return ranked[:3]


def lb_block(title, icon, ranked, key_fn):
    text = f"{icon} {title}\n"
    for i, p in enumerate(ranked):
        raw = key_fn(p)
        text += (
            f"{i+1}-{p['name']} "
            f"{lb_bar(raw)} "
            f"{raw}%\n"
        )
    return text + "\n"


def build_leaderboard_pretty(profiles):

    if not profiles:
        return "No leaderboard data available."

    pretty = "━━━━━━━━━━━━━━━━━━━━\n"
    pretty += "🏆 SL SOCIAL EXPERIMENT\n"
    pretty += "COMPETITIVE LEADERBOARD\n"
    pretty += "━━━━━━━━━━━━━━━━━━━━\n\n"

    medals = ["🥇", "🥈", "🥉"]

    def top3(title, key_fn):
        ranked = sorted(profiles, key=key_fn, reverse=True)[:3]

        if not ranked or key_fn(ranked[0]) <= 0:
            return ""  # skip empty categories

        block = f"{title}\n"

        for i, p in enumerate(ranked):
            score = key_fn(p)
            medal = medals[i] if i < 3 else ""
            champ = " 🔥 CURRENT CHAMPION" if i == 0 else ""
            block += f"{medal} {p['name']} — {score}%{champ}\n"

        return block + "\n"

    # PERSONALITY
    pretty += top3("📊 Confidence", lambda p: p["confidence"])
    pretty += top3("💬 Engaging", lambda p: p["traits"]["engaging"])
    pretty += top3("🧠 Curious", lambda p: p["traits"]["curious"])
    pretty += top3("😂 Humorous", lambda p: p["traits"]["humorous"])
    pretty += top3("🤍 Supportive", lambda p: p["traits"]["supportive"])
    pretty += top3("👑 Dominant", lambda p: p["traits"]["dominant"])
    pretty += top3("⚔ Combative", lambda p: p["traits"]["combative"])

    # STYLE
    pretty += top3("💕 Flirty", lambda p: p["styles"]["flirty"])
    pretty += top3("🔞 Sexual", lambda p: p["styles"]["sexual"])
    pretty += top3("🤬 Curse", lambda p: p["styles"]["curse"])

    # ENERGY
    pretty += top3("🎧 Hangout Energy", lambda p: p["hangout_energy"])
    pretty += top3("🎉 Club Energy", lambda p: p["club_energy"])
    pretty += top3("🔥 Risk Energy", lambda p: p["risk"])

    pretty += "━━━━━━━━━━━━━━━━━━━━"

    return pretty

# =================================================
# ROOM VIBE ENDPOINT (SL-SAFE, PROFILE-STYLE)
# =================================================

@app.route("/room/vibe", methods=["POST"])
def room_vibe():
    data = request.get_json(silent=True) or {}

    if "uuids" in data:
        uuids = set(data.get("uuids", []))
    elif "uuid" in data:
        uuids = {data.get("uuid")}
    else:
        uuids = set()

    profiles = [
        p for p in build_profiles()
        if p["avatar_uuid"] in uuids
    ]

    pretty, html = build_room_vibe_enhanced(profiles)

    return Response(
        json.dumps({
            "pretty_text": pretty   # ← ONLY thing SL needs
        }, ensure_ascii=False),
        mimetype="application/json; charset=utf-8"
    )

@app.route("/match/best", methods=["POST"])
def best_match():
    data = request.get_json(silent=True) or {}
    uuid = data.get("uuid")

    profiles = build_profiles()
    source = next((p for p in profiles if p["avatar_uuid"] == uuid), None)

    if not source:
        return jsonify({"error": "profile not found"}), 404

    similar, complement, hybrid = find_best_matches(source, profiles)

    pretty = build_best_match_pretty(
        source,
        similar,
        complement,
        hybrid
    )

    # 🔑 SL-SAFE RESPONSE (THIS IS WHY IT WORKS)
    return Response(
        json.dumps({
            "text": pretty,        # Script C reads this
            "pretty_text": pretty  # kept for consistency
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

@app.route("/leaderboard/sl")
def leaderboard_sl():

    profiles = build_profiles()
    pretty = build_leaderboard_pretty(profiles)

    return Response(
        json.dumps({
            "pretty_text": pretty
        }, ensure_ascii=False),
        mimetype="application/json; charset=utf-8",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.route("/leaderboard/panels")
def leaderboard_panels():

    profiles = build_profiles()
    ranked = sorted(profiles, key=lambda p: p["confidence"], reverse=True)[:3]

    def card(pos, p, color):
        medal = ["🥇","🥈","🥉"][pos]
        return f"""
        <div class="card">
            <div class="medal">{medal}</div>
            <div class="name">{p['name']}</div>
            <div class="score">{p['confidence']}%</div>
            <div class="bar">
                <div class="fill" style="width:{p['confidence']}%; background:{color};"></div>
            </div>
        </div>
        """

    html = f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="60">
    <style>

        html, body {{
            margin:0;
            padding:0;
            height:100%;
            width:100%;
            overflow:hidden;
            font-family: 'Segoe UI', sans-serif;
            background: radial-gradient(circle at center,
                #12002b 0%,
                #0a001a 40%,
                #000010 100%);
            color:white;
        }}

        .container {{
            width:100%;
            height:100%;
            display:flex;
            flex-direction:column;
            align-items:center;
            justify-content:center;
            padding:40px;
            box-sizing:border-box;
        }}

        .title {{
            font-size:48px;
            letter-spacing:4px;
            margin-bottom:40px;
            text-align:center;
            background: linear-gradient(90deg, #00f0ff, #ff00ff);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            text-shadow:0 0 20px rgba(0,255,255,0.4);
        }}

        .board {{
            width:90%;
            max-width:1000px;
            display:flex;
            flex-direction:column;
            gap:30px;
        }}

        .card {{
            background: rgba(20,20,40,0.6);
            backdrop-filter: blur(10px);
            border-radius:20px;
            padding:30px;
            box-shadow:
                0 0 20px rgba(0,255,255,0.2),
                0 0 40px rgba(255,0,255,0.15);
            position:relative;
        }}

        .medal {{
            position:absolute;
            right:30px;
            top:25px;
            font-size:32px;
        }}

        .name {{
            font-size:28px;
            font-weight:600;
            margin-bottom:10px;
        }}

        .score {{
            font-size:18px;
            opacity:0.7;
            margin-bottom:15px;
        }}

        .bar {{
            height:16px;
            background:#111;
            border-radius:10px;
            overflow:hidden;
        }}

        .fill {{
            height:100%;
            border-radius:10px;
            box-shadow:0 0 12px currentColor;
            animation: grow 1.2s ease-out;
        }}

        @keyframes grow {{
            from {{ width:0%; }}
            to {{ width:100%; }}
        }}

    </style>
    </head>

    <body>
        <div class="container">
            <div class="title">🏆 CONFIDENCE LEADERBOARD</div>
            <div class="board">
                {card(0, ranked[0], "#FFD700")}
                {card(1, ranked[1], "#C0C0C0")}
                {card(2, ranked[2], "#CD7F32")}
            </div>
        </div>
    </body>
    </html>
    """

    return html


@app.route("/leaderboard/live", methods=["GET"])
def leaderboard_live():

    profiles = build_profiles()

    if not profiles:
        return jsonify({"trait":"None","top":[]})

    # Rotate trait here if you want later
    trait_key = "confidence"
    trait_label = "Confidence"

    # Rank top 3
    ranked = sorted(
        profiles,
        key=lambda p: p["confidence"],
        reverse=True
    )[:3]

    top = [
        {
            "name": p["name"],
            "score": p["confidence"]
        }
        for p in ranked
    ]

    return Response(
        json.dumps({
            "trait": trait_label,
            "top": top
        }, ensure_ascii=False),
        mimetype="application/json; charset=utf-8",
        headers={"Access-Control-Allow-Origin": "*"}
    )
    
@app.route("/metrics/platform", methods=["GET"])
def platform_metrics():

    metrics = build_platform_metrics()

    return Response(
        json.dumps(metrics),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.route("/metrics/panels")
def metrics_panels():

    metrics = build_platform_metrics()

    html = f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="60">
    <style>

        html, body {{
            margin:0;
            padding:0;
            height:100%;
            width:100%;
            overflow:hidden;
            font-family: 'Segoe UI', sans-serif;
            background: radial-gradient(circle at center,
                #140030 0%,
                #0b001f 40%,
                #000010 100%);
            color:white;
        }}

        .container {{
            width:100%;
            height:100%;
            display:flex;
            flex-direction:column;
            align-items:center;
            justify-content:center;
            padding:40px;
            box-sizing:border-box;
        }}

        .title {{
            font-size:48px;
            letter-spacing:4px;
            margin-bottom:60px;
            text-align:center;
            background: linear-gradient(90deg, #00f0ff, #ff00ff);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            text-shadow:0 0 25px rgba(0,255,255,0.5);
        }}

        .board {{
            width:90%;
            max-width:900px;
            display:flex;
            flex-direction:column;
            gap:40px;
        }}

        .card {{
            background: rgba(25,25,60,0.6);
            backdrop-filter: blur(12px);
            border-radius:24px;
            padding:40px;
            box-shadow:
                0 0 25px rgba(0,255,255,0.25),
                0 0 50px rgba(255,0,255,0.2);
            text-align:center;
        }}

        .label {{
            font-size:22px;
            letter-spacing:2px;
            opacity:0.7;
            margin-bottom:15px;
        }}

        .value {{
            font-size:64px;
            font-weight:700;
            background: linear-gradient(90deg, #00f0ff, #ff00ff);
            -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            text-shadow:0 0 20px rgba(0,255,255,0.4);
        }}

    </style>
    </head>

    <body>
        <div class="container">
            <div class="title">📊 PLATFORM METRICS</div>

<div class="board">

    <div class="card">
        <div class="label">TOTAL REGISTERED</div>
        <div class="value">{metrics["total_registered"]}</div>
    </div>

    <div class="card">
        <div class="label">SPOKE LAST 24 HOURS</div>
        <div class="value">{metrics["spoke_24h"]}</div>
    </div>

    <div class="card">
        <div class="label">LIVE RIGHT NOW</div>
        <div class="value">{metrics["live_now"]}</div>
    </div>

    <div class="card">
        <div class="label">POWER USERS (1H)</div>
        <div class="value">{metrics["power_users"]}</div>
    </div>

    <div class="card">
        <div class="label">SILENT OBSERVERS</div>
        <div class="value">{metrics["silent_observers"]}</div>
    </div>

</div>

            </div>
        </div>
    </body>
    </html>
    """

    return html


@app.route("/")
def ok():
    return "OK", 200

# ==========================================
# REQUIRED FOR RENDER
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
