from flask import Flask, Response, request
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")

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

STRUCTURAL_WEIGHTS = {
    "question": 0.15,
    "caps": 0.4
}

STYLE_DISPLAY_MULTIPLIER = 1.8

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
# ARCHETYPES
# =================================================

ARCHETYPES = [
    ("Social Catalyst",    lambda t: t.get("engaging",0) > 0.12 and t.get("curious",0) > 0.06),
    ("Entertainer",        lambda t: t.get("humorous",0) > 0.12),
    ("Debater",            lambda t: t.get("combative",0) > 0.12),
    ("Presence Dominator", lambda t: t.get("dominant",0) > 0.12),
    ("Support Anchor",     lambda t: t.get("supportive",0) > 0.12),
    ("Quiet Thinker",      lambda t: t.get("concise",0) > 0.18),
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
            if j < 0:
                continue
            if words[j] in {".","!","?"}:
                break
            if words[j] in NEGATORS:
                return True
        return False

    for i, w in enumerate(words):
        if negated(i):
            continue

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

def style_decay(ts):
    try:
        hours = (time.time() - float(ts)) / 3600
        if hours <= 1: return 1.0
        if hours <= 6: return 0.4
        return 0.1
    except:
        return 0.3

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

        ts = r.get("timestamp", time.time())
        w  = decay(ts)
        sw = style_decay(ts)

        p = profiles.setdefault(uuid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "style": defaultdict(float),
            "gravity": 0,
            "reputation": {"score":0.5}
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w

        p["traits"]["concise"] += (r.get("short_msgs",0)/msgs) * 1.2 * w
        p["traits"]["curious"] += r.get("question_count",0) * STRUCTURAL_WEIGHTS["question"] * w

        caps = r.get("caps_msgs",0)
        p["traits"]["dominant"] += caps * STRUCTURAL_WEIGHTS["caps"] * w
        p["traits"]["combative"] += caps * STRUCTURAL_WEIGHTS["caps"] * 0.6 * w

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k,v in hits.items():
            if k in TRAIT_KEYWORD_WEIGHTS:
                p["traits"][k] += v * TRAIT_KEYWORD_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["style"][k] += v * STYLE_WEIGHTS[k] * sw

    # FINALIZE
    for p in profiles.values():
        m = max(p["messages"],1)

        p["norm"] = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["gravity_norm"] = min(p["gravity"]/m,1.0)

        signal = (
            p["norm"].get("engaging",0)
          + p["norm"].get("supportive",0)
          - p["norm"].get("combative",0)*0.7
        )
        p["reputation"]["score"] = p["reputation"]["score"]*0.92 + max(0,signal)*0.08

        p["troll_flag"] = (
            p["norm"].get("combative",0) > 0.25 and
            p["norm"].get("dominant",0) > 0.25 and
            m > 12
        )

        p["confidence"] = min(1.0, math.log(m+1)/4)

        p["archetype"] = "Profile forming"
        if p["confidence"] > 0.35 and m >= 10:
            for name,rule in ARCHETYPES:
                if rule(p["norm"]):
                    p["archetype"] = name
                    break

        p["role"] = "Performer" if p["norm"].get("engaging",0) > p["norm"].get("concise",0) else "Audience"

        p["style_norm"] = {
            k:min((v/(m*0.25))*STYLE_DISPLAY_MULTIPLIER,1.0)
            for k,v in p["style"].items()
        }

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# ENDPOINTS
# =================================================

@app.route("/leaderboard")
def leaderboard():
    profiles = build_profiles()

    ranked = sorted(
        profiles.values(),
        key=lambda p: p.get("reputation",{}).get("score",0),
        reverse=True
    )

    out = []
    for i,p in enumerate(ranked,1):
        out.append({
            "rank": i,
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "reputation": int(p["reputation"]["score"]*100),
            "gravity": int(p["gravity_norm"]*100),
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
