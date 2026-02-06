from flask import Flask, Response
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")

CACHE = {"profiles": {}, "ts": 0}
CACHE_TTL = 300  # 5 minutes

# =================================================
# WEIGHTS (BEHAVIORAL, NOT DISPLAY)
# =================================================

TRAIT_KEYWORD_WEIGHTS = {
    "engaging":   1.4,
    "curious":    1.3,
    "humorous":   1.4,
    "supportive": 1.4,
    "dominant":   1.5,
    "combative":  1.4,
}

STYLE_WEIGHTS = {
    "flirty": 1.1,
    "sexual": 1.2,
    "curse":  1.0
}

STRUCTURAL_WEIGHTS = {
    "question": 0.15,
    "caps": 0.35
}

# =================================================
# KEYWORDS
# =================================================

ENGAGING_WORDS  = {"hi","hey","hello","yo","welcome"}
CURIOUS_WORDS   = {"why","how","what","where","when","who"}
HUMOR_WORDS     = {"lol","lmao","haha","rofl"}
SUPPORT_WORDS   = {"sorry","hope","hugs","hug","better"}
DOMINANT_WORDS  = {"listen","look","stop","now","enough"}
COMBATIVE_WORDS = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY_WORDS = {"cute","hot","handsome","beautiful","kiss","kisses","xoxo"}
SEXUAL_WORDS = {"sex","fuck","fucking","horny","wet","hard","naked"}
CURSE_WORDS  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# ARCHETYPES (RATE-BASED)
# =================================================

ARCHETYPES = [
    ("Social Catalyst",    lambda t: t["engaging"] > 0.35 and t["curious"] > 0.18),
    ("Entertainer",        lambda t: t["humorous"] > 0.35),
    ("Debater",            lambda t: t["combative"] > 0.30),
    ("Presence Dominator", lambda t: t["dominant"] > 0.35),
    ("Support Anchor",     lambda t: t["supportive"] > 0.35),
]

# =================================================
# DECAY
# =================================================

def decay(ts):
    try:
        days = (time.time() - float(ts)) / 86400
        if days <= 1: return 1.0
        if days <= 7: return 0.6
        return 0.3
    except:
        return 1.0

def style_decay(ts):
    try:
        hours = (time.time() - float(ts)) / 3600
        if hours <= 1: return 1.0
        if hours <= 6: return 0.4
        return 0.15
    except:
        return 0.3

# =================================================
# TEXT PARSING
# =================================================

def extract_keyword_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b|[.!?]", text.lower())

    def negated(i):
        for j in range(i-4, i):
            if j < 0: continue
            if words[j] in {".","!","?"}: break
            if words[j] in NEGATORS: return True
        return False

    for i, w in enumerate(words):
        if negated(i): continue

        if w in ENGAGING_WORDS:  hits["engaging"] += 1
        if w in CURIOUS_WORDS:   hits["curious"] += 1
        if w in HUMOR_WORDS:     hits["humorous"] += 1
        if w in SUPPORT_WORDS:   hits["supportive"] += 1
        if w in DOMINANT_WORDS:  hits["dominant"] += 1
        if w in COMBATIVE_WORDS: hits["combative"] += 1

        if w in FLIRTY_WORDS: hits["flirty"] += 1
        if w in SEXUAL_WORDS: hits["sexual"] += 1
        if w in CURSE_WORDS:  hits["curse"] += 1

    return hits

# =================================================
# DATA FETCH
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    match = re.search(r"setResponse\((\{.*\})\)", r.text, re.S)
    payload = json.loads(match.group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]

    rows = []
    for row in payload["table"]["rows"]:
        rec = {}
        for i, cell in enumerate(row["c"]):
            rec[cols[i] if cols[i] else f"col_{i}"] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

# =================================================
# BUILD PROFILES (STABLE + BOUNDED)
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles = {}
    rows = fetch_rows()

    for r in rows:
        uuid = r.get("avatar_uuid")
        if not uuid:
            continue

        ts = r.get("timestamp", time.time())
        w  = decay(ts)
        sw = style_decay(ts)

        p = profiles.setdefault(uuid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "style": defaultdict(float),
            "reputation": 0.5
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w

        # STRUCTURAL (CAPPED)
        p["traits"]["curious"] += min(r.get("question_count",0), 5) * STRUCTURAL_WEIGHTS["question"] * w

        caps = min(r.get("caps_msgs",0), 5)
        p["traits"]["dominant"]  += caps * STRUCTURAL_WEIGHTS["caps"] * w
        p["traits"]["combative"] += caps * STRUCTURAL_WEIGHTS["caps"] * 0.6 * w

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k,v in hits.items():
            if k in TRAIT_KEYWORD_WEIGHTS:
                p["traits"][k] += v * TRAIT_KEYWORD_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["style"][k] += v * STYLE_WEIGHTS[k] * sw

    # FINAL NORMALIZATION
    for p in profiles.values():
        m = max(p["messages"],1)

        p["norm"] = {
            k: max(0.0, min(v / m, 1.0))
            for k,v in p["traits"].items()
        }

        signal = (
            p["norm"].get("engaging",0)
          + p["norm"].get("supportive",0)
          - p["norm"].get("combative",0)*0.6
        )

        p["reputation"] = p["reputation"]*0.92 + max(0,signal)*0.08

        p["confidence"] = min(1.0, math.log(m+1)/4)

        p["archetype"] = "Profile forming"
        if p["confidence"] > 0.35 and m >= 10:
            for name,rule in ARCHETYPES:
                if rule(p["norm"]):
                    p["archetype"] = name
                    break

        p["role"] = "Performer" if p["norm"].get("engaging",0) > p["norm"].get("curious",0) else "Audience"

        p["style_norm"] = {
            k: max(0.0, min(v / m, 1.0))
            for k,v in p["style"].items()
        }

        p["troll_flag"] = (
            p["norm"].get("combative",0) > 0.35 and
            p["norm"].get("dominant",0) > 0.35 and
            m > 15
        )

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# API
# =================================================

@app.route("/leaderboard")
def leaderboard():
    profiles = build_profiles()

    ranked = sorted(
        profiles.values(),
        key=lambda p: p["reputation"],
        reverse=True
    )

    out = []
    for i,p in enumerate(ranked,1):
        out.append({
            "rank": i,
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "reputation": int(p["reputation"]*100),
            "role": p["role"],
            "archetype": p["archetype"],
            "traits": {k:int(v*100) for k,v in p["norm"].items()},
            "styles": {k:int(v*100) for k,v in p["style_norm"].items()},
            "troll": bool(p["troll_flag"])
        })

    return Response(
        json.dumps(out),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"}
    )

@app.route("/")
def ok():
    return "OK"
