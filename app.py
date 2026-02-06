from flask import Flask, Response, request
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]

CACHE = {"profiles": {}, "ts": 0}
CACHE_TTL = 300

# =================================================
# WEIGHTS
# =================================================

TRAIT_KEYWORD_WEIGHTS = {
    "engaging":   1.6,
    "curious":    1.4,
    "humorous":   1.5,
    "supportive": 1.5,
    "dominant":   1.7,
    "combative":  1.6,
}

STYLE_WEIGHTS = {
    "flirty": 1.2,
    "sexual": 1.3,
    "curse":  1.1
}

STYLE_EXPLANATIONS = {
    "flirty": "Leans playful. Probably typing with a wink.",
    "sexual": "Not subtle. HR would like a word.",
    "curse":  "Expressive vocabulary. Swears for emphasis."
}

ARCHETYPE_EXPLANATIONS = {
    "Social Catalyst":    "Keeps conversations alive like caffeine for humans.",
    "Entertainer":        "Here for the laughs. Would absolutely bring snacks.",
    "Debater":            "Thrives on disagreement. Argues for sport.",
    "Presence Dominator": "Fills the room without trying.",
    "Support Anchor":     "Emotional duct tape. Holds chats together.",
    "Quiet Thinker":      "Observes first. Speaks with purpose.",
    "Profile forming":    "Still warming up. Data in progress."
}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

ENGAGING_WORDS  = {"hi","hey","hello","yo","welcome"}
CURIOUS_WORDS   = {"why","how","what","where","when","who"}
HUMOR_WORDS     = {"lol","lmao","haha","rofl"}
SUPPORT_WORDS   = {"sorry","hope","hugs","hug","better"}
DOMINANT_WORDS  = {"listen","look","stop","now","enough"}
COMBATIVE_WORDS = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY_WORDS = {"cute","hot","handsome","beautiful","kiss","kisses","xoxo"}
SEXUAL_WORDS = {"sex","fuck","fucking","horny","wet","hard","naked"}
CURSE_WORDS  = {"fuck","shit","damn","bitch","asshole","wtf"}

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
            if words[j] in ".!?": break
            if words[j] in NEGATORS:
                return True
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
            rec[cols[i]] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

# =================================================
# BUILD PROFILES (SINGLE SOURCE OF TRUTH)
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles = {}

    for r in fetch_rows():
        uuid = r.get("avatar_uuid")
        if not uuid:
            continue

        ts = r.get("timestamp", time.time())
        w  = decay(ts)

        p = profiles.setdefault(uuid,{
            "uuid": uuid,
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "styles": defaultdict(float)
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k,v in hits.items():
            if k in TRAIT_KEYWORD_WEIGHTS:
                p["traits"][k] += v * TRAIT_KEYWORD_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["styles"][k] += v * STYLE_WEIGHTS[k] * w

    # FINALIZE
    for p in profiles.values():
        m = max(p["messages"],1)

        p["confidence"] = min(1.0, math.log(m+1)/4)

        p["trait_pct"] = {k:int(min(v/m,1)*100) for k,v in p["traits"].items()}
        p["style_pct"] = {k:int(min(v/(m*0.25),1)*100) for k,v in p["styles"].items()}

        # Archetype logic
        p["archetype"] = "Profile forming"
        if p["trait_pct"].get("humorous",0) > 12:
            p["archetype"] = "Entertainer"
        elif p["trait_pct"].get("engaging",0) > 12 and p["trait_pct"].get("curious",0) > 6:
            p["archetype"] = "Social Catalyst"
        elif p["trait_pct"].get("combative",0) > 12:
            p["archetype"] = "Debater"

        p["archetype_explanation"] = ARCHETYPE_EXPLANATIONS[p["archetype"]]

        p["style_explanations"] = {
            k: STYLE_EXPLANATIONS[k]
            for k,v in p["style_pct"].items()
            if v > 0
        }

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# SECOND LIFE ENDPOINTS (DO NOT BREAK)
# =================================================

@app.route("/list_profiles")
def list_profiles():
    profiles = build_profiles()
    ranked = sorted(profiles.values(), key=lambda p:p["messages"], reverse=True)[:5]

    out = []
    for p in ranked:
        out.append({
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "traits": p["trait_pct"],
            "styles": p["style_pct"],
            "style_explanations": p["style_explanations"],
            "archetype": p["archetype"],
            "archetype_explanation": p["archetype_explanation"]
        })

    return Response(json.dumps(out), mimetype="application/json")

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    ids = set(request.json.get("ids",[]))
    profiles = build_profiles()

    out = []
    for p in profiles.values():
        if p["uuid"] in ids:
            out.append({
                "name": p["name"],
                "confidence": int(p["confidence"]*100),
                "traits": p["trait_pct"],
                "styles": p["style_pct"],
                "style_explanations": p["style_explanations"],
                "archetype": p["archetype"],
                "archetype_explanation": p["archetype_explanation"]
            })

    return Response(json.dumps(out), mimetype="application/json")

# =================================================
# WEBSITE ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    profiles = build_profiles()
    ranked = sorted(profiles.values(), key=lambda p:p["messages"], reverse=True)

    return Response(
        json.dumps(ranked),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"}
    )

@app.route("/")
def ok():
    return "OK"
