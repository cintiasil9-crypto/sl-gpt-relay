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
# WEIGHTS (tuned, but safe)
# =================================================

TRAIT_KEYWORD_WEIGHTS = {
    "engaging":   1.0,
    "curious":    0.9,
    "humorous":   1.0,
    "supportive": 1.0,
    "dominant":   1.1,
    "combative":  1.0,
}

STYLE_WEIGHTS = {
    "flirty": 1.0,
    "sexual": 1.0,
    "curse":  1.0
}

STRUCTURAL_WEIGHTS = {
    "question": 0.6,
    "caps": 0.8
}

STYLE_DISPLAY_MULTIPLIER = 1.0

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
# ARCHETYPES (SAFE)
# =================================================

ARCHETYPES = [
    ("Social Catalyst",    lambda t: t.get("engaging",0) > 0.25 and t.get("curious",0) > 0.15),
    ("Entertainer",        lambda t: t.get("humorous",0) > 0.30),
    ("Support Anchor",     lambda t: t.get("supportive",0) > 0.30),
    ("Presence Dominator", lambda t: t.get("dominant",0) > 0.30),
    ("Debater",            lambda t: t.get("combative",0) > 0.30),
]

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

    for i,w in enumerate(words):
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
# DECAY
# =================================================

def decay(ts):
    try:
        days = (time.time() - float(ts)) / 86400
        if days <= 1: return 1.0
        if days <= 7: return 0.7
        return 0.4
    except:
        return 1.0

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
# BUILD PROFILES
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

        p = profiles.setdefault(uuid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "styles": defaultdict(float),
            "reputation": 0.5
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k,v in hits.items():
            if k in TRAIT_KEYWORD_WEIGHTS:
                p["traits"][k] += v * TRAIT_KEYWORD_WEIGHTS[k]
            if k in STYLE_WEIGHTS:
                p["styles"][k] += v * STYLE_WEIGHTS[k]

    # FINAL NORMALIZATION (HARD CAPPED)
    for p in profiles.values():
        m = max(p["messages"], 1)

        norm = {}
        for k,v in p["traits"].items():
            norm[k] = min(v / (m * 2.5), 1.0)

        style_norm = {}
        for k,v in p["styles"].items():
            style_norm[k] = min(v / (m * 2.0), 1.0)

        signal = norm.get("engaging",0) + norm.get("supportive",0) - norm.get("combative",0)
        p["reputation"] = min(max(signal,0),1)

        archetype = "Profile forming"
        for name,rule in ARCHETYPES:
            if rule(norm):
                archetype = name
                break

        p.update({
            "norm": norm,
            "style_norm": style_norm,
            "archetype": archetype,
            "confidence": min(math.log(m+1)/4,1.0),
            "role": "Performer" if norm.get("engaging",0) >= norm.get("supportive",0) else "Audience"
        })

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    profiles = build_profiles()

    ranked = sorted(profiles.values(), key=lambda p: p["reputation"], reverse=True)

    out = []
    for i,p in enumerate(ranked,1):
        out.append({
            "rank": i,
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "reputation": int(p["reputation"]*100),
            "gravity": 0,
            "role": p["role"],
            "archetype": p["archetype"],
            "traits": {k:int(v*100) for k,v in p["norm"].items()},
            "styles": {k:int(v*100) for k,v in p["style_norm"].items()},
            "troll": p["norm"].get("combative",0) > 0.6
        })

    return Response(json.dumps(out), mimetype="application/json",
                    headers={"Access-Control-Allow-Origin":"*"})

@app.route("/")
def ok():
    return "OK"
