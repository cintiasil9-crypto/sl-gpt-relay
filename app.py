from flask import Flask, Response
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")

CACHE = {"profiles": None, "ts": 0}
CACHE_TTL = 300

# =================================================
# KEYWORDS (TRAITS)
# =================================================

KEYWORDS = {
    "engaging": {"hi","hey","hello","yo","welcome","anyone","sup"},
    "curious": {"why","how","what","where","when","who"},
    "humorous": {"lol","lmao","haha","rofl"},
    "supportive": {"sorry","hope","hugs","hug","better","care","ok"},
    "dominant": {"listen","look","stop","now","enough"},
    "combative": {"idiot","stupid","shut","wrong","wtf"},
    "flirty": {"cute","hot","handsome","beautiful","kiss","xoxo"},
    "sexual": {"sex","fuck","horny","naked","hard","wet"},
    "curse": {"fuck","shit","damn","bitch","asshole"}
}

NEGATORS = {"not","no","never","dont","can't","isn't"}

# =================================================
# CULTURE KEYWORDS
# =================================================

EMOJI_RE = re.compile(r"[😂🤣😍😘🔥💃🕺❤️💋]")

CLUB_WORDS = {
    "dance","dj","beat","club","song","music","spin","grind","party"
}

RP_WORDS = {
    "kneel","obey","command","protect","draw","attack","enemy","sir","ma'am"
}

BUILDER_WORDS = {
    "script","error","fix","help","how","why","build","mesh","lsl","texture"
}

# =================================================
# CULTURE BADGES
# =================================================

CULTURE_BADGES = {
    "club": "🪩 Club Energy",
    "rp": "🎭 RP Mode",
    "builder": "🛠️ Builder Brain"
}

# =================================================
# SUMMARY PHRASES
# =================================================

TRAIT_PHRASES = {
    "engaging": "naturally pulls people into conversation",
    "curious": "asks questions others don’t think to ask",
    "humorous": "keeps things light with humor",
    "supportive": "brings steady, comforting energy",
    "dominant": "likes steering the direction of the room",
    "combative": "pushes back instead of letting things slide",
    "flirty": "adds playful tension to interactions"
}

# =================================================
# UTIL
# =================================================

def extract_hits(text):
    hits = defaultdict(int)
    words = re.findall(r"\b\w+\b", (text or "").lower())
    for i, w in enumerate(words):
        if any(n in words[max(0,i-3):i] for n in NEGATORS):
            continue
        for trait, vocab in KEYWORDS.items():
            if w in vocab:
                hits[trait] += 1
    return hits

def decay(ts):
    try:
        age = (time.time() - float(ts)) / 86400
        return 1.0 if age < 1 else 0.6 if age < 7 else 0.3
    except:
        return 1.0

def apply_culture_modifiers(traits, culture):
    t = traits.copy()

    if culture["club"] > 0.5:
        t["flirty"] *= 1.1
        t["sexual"] *= 0.7
        t["combative"] *= 0.5

    if culture["rp"] > 0.5:
        t["dominant"] *= 1.1
        t["supportive"] *= 1.1
        t["combative"] *= 0.8

    if culture["builder"] > 0.5:
        t["curious"] *= 1.2
        t["flirty"] *= 0.3
        t["sexual"] *= 0.2

    return t

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    payload = json.loads(re.search(r"setResponse\((\{.*\})\)", r.text, re.S).group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]

    profiles = {}

    for row in payload["table"]["rows"]:
        rec = {cols[i]: (cell["v"] if cell else "") for i,cell in enumerate(row["c"])}
        uid = rec.get("avatar_uuid")
        if not uid:
            continue

        p = profiles.setdefault(uid,{
            "name": rec.get("display_name","Unknown"),
            "messages": 0,
            "raw": defaultdict(float),
            "signals": defaultdict(float)
        })

        w = decay(rec.get("timestamp",time.time()))
        p["messages"] += 1 * w

        text = (rec.get("context_sample") or "").lower()
        words = re.findall(r"\b\w+\b", text)

        # culture signals
        p["signals"]["msgs"] += 1
        p["signals"]["short"] += 1 if len(words) <= 3 else 0
        p["signals"]["emotes"] += 1 if re.search(r"\*.+?\*|/me\s", text) else 0
        p["signals"]["emojis"] += len(EMOJI_RE.findall(text))
        p["signals"]["club"] += sum(w in CLUB_WORDS for w in words)
        p["signals"]["rp"] += sum(w in RP_WORDS for w in words)
        p["signals"]["builder"] += sum(w in BUILDER_WORDS for w in words)

        # trait hits
        hits = extract_hits(text)
        for k,v in hits.items():
            p["raw"][k] += v * w

    results = []

    for p in profiles.values():
        m = max(p["messages"],1)
        confidence = min(math.log(m+1)/4,1.0)

        # culture probabilities
        msgs = max(p["signals"]["msgs"],1)
        club = (
            (p["signals"]["short"]/msgs)*0.3 +
            (p["signals"]["emotes"]/msgs)*0.25 +
            (p["signals"]["emojis"]/msgs)*0.15 +
            (p["signals"]["club"]/msgs)*0.3
        )
        rp = (
            (p["signals"]["rp"]/msgs)*0.6 +
            (1 - p["signals"]["short"]/msgs)*0.4
        )
        builder = (
            (p["signals"]["builder"]/msgs)*0.6 +
            (1 - p["signals"]["emotes"]/msgs)*0.4
        )

        total_c = club + rp + builder or 1
        culture = {
            "club": club/total_c,
            "rp": rp/total_c,
            "builder": builder/total_c
        }

        # normalize traits relatively
        raw = apply_culture_modifiers(p["raw"], culture)
        total = sum(raw.values()) or 1

        traits = {
            k: min(int((v/total) * (0.4 + confidence) * 100), 85)
            for k,v in raw.items()
        }

        styles = {
            k: min(int(traits.get(k,0) * 0.8), 85)
            for k in ["flirty","sexual","curse"]
        }

        # disruption (troll)
        disruption = min(
            traits.get("combative",0)*0.6 +
            traits.get("dominant",0)*0.4,
            100
        )

        # summary
        main = max(traits, key=lambda k: traits[k])
        summary = f"Often {TRAIT_PHRASES.get(main,'has a distinct presence')}. "
        summary += (
            "Early impressions only."
            if confidence < 0.3 else
            "Pattern is becoming clearer."
            if confidence < 0.6 else
            "Consistent behavior detected."
        )

        culture_badges = [
            CULTURE_BADGES[k]
            for k,v in culture.items()
            if v > 0.45
        ][:2]

        results.append({
            "name": p["name"],
            "confidence": int(confidence*100),
            "traits": traits,
            "styles": styles,
            "troll": disruption > 65,

            # new fields (safe to ignore in UI)
            "summary": summary,
            "culture": culture,
            "culture_badges": culture_badges
        })

    CACHE["profiles"] = results
    CACHE["ts"] = time.time()
    return results

# =================================================
# ENDPOINTS
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
    return "OK"
