from flask import Flask, Response
import os, time, math, requests, json, re

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

QUESTION_WEIGHT       = 0.15
CAPS_DOMINANT_WEIGHT  = 0.10
CAPS_COMBATIVE_WEIGHT = 0.08

# =================================================
# KEYWORDS (EXACT MATCH)
# =================================================

ENGAGING_WORDS  = {"hi","hey","hello","hiya","yo","welcome"}
CURIOUS_WORDS   = {"why","how","what","where","when","who"}
HUMOR_WORDS     = {"lol","lmao","haha","rofl"}
SUPPORT_WORDS   = {"sorry","hope","hugs","hug","feel","better"}
DOMINANT_WORDS  = {"listen","look","stop","enough","now"}
COMBATIVE_WORDS = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY_WORDS = {"cute","hot","handsome","beautiful","kiss","kisses","hugsss","xoxo","flirt"}
SEXUAL_WORDS = {"sex","fuck","fucking","horny","wet","hard","naked"}
CURSE_WORDS  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# KEYWORD PARSER (NEGATION AWARE)
# =================================================

def extract_keyword_hits(text):
    hits = {k:0 for k in [
        "engaging","curious","humorous",
        "supportive","dominant","combative",
        "flirty","sexual","curse"
    ]}
    if not text:
        return hits

    words = re.findall(r"\b\w+\b", text.lower())

    def neg(i):
        return any(words[j] in NEGATORS for j in range(max(0,i-2), i))

    for i,w in enumerate(words):
        if neg(i): 
            continue

        if w in ENGAGING_WORDS:  hits["engaging"]   = 1
        if w in CURIOUS_WORDS:   hits["curious"]    = 1
        if w in HUMOR_WORDS:     hits["humorous"]   = 1
        if w in SUPPORT_WORDS:   hits["supportive"] = 1
        if w in DOMINANT_WORDS:  hits["dominant"]   = 1
        if w in COMBATIVE_WORDS: hits["combative"]  = 1

        if w in FLIRTY_WORDS:    hits["flirty"]     = 1
        if w in SEXUAL_WORDS:    hits["sexual"]     = 1
        if w in CURSE_WORDS:     hits["curse"]      = 1

    return hits

# =================================================
# DATA + DECAY
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    payload = json.loads(re.search(r"setResponse\((\{.*\})\)", r.text, re.S).group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]

    rows = []
    for row in payload["table"]["rows"]:
        rec = {}
        for i,cell in enumerate(row["c"]):
            rec[cols[i]] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

def decay(ts):
    try:
        days = (time.time() - int(ts)) / 86400
        return 1.0 if days <= 1 else 0.6 if days <= 7 else 0.3
    except:
        return 0.0

# =================================================
# ARCHETYPES
# =================================================

ARCHETYPES = [
    ("Social Catalyst",    lambda t: t["engaging"]>=0.25 and t["curious"]>=0.15),
    ("Entertainer",        lambda t: t["humorous"]>=0.20),
    ("Debater",            lambda t: t["combative"]>=0.20),
    ("Presence Dominator", lambda t: t["dominant"]>=0.20),
    ("Support Anchor",     lambda t: t["supportive"]>=0.20),
    ("Quiet Thinker",      lambda t: t["concise"]>=0.30 and t["engaging"]<0.10),
]

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time()-CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles = {}

    for r in fetch_rows():
        uuid = r["avatar_uuid"]
        w = decay(r["timestamp_utc"])

        p = profiles.setdefault(uuid,{
            "name":r.get("display_name","Unknown"),
            "messages":0,
            "traits":{k:0 for k in [
                "engaging","curious","humorous",
                "supportive","dominant","combative","concise"
            ]},
            "style":{"flirty":0,"sexual":0,"curse":0}
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w

        # concise (UNCHANGED)
        p["traits"]["concise"] += (1 - r.get("short_msgs",0)/msgs) * w

        # structural boosts
        p["traits"]["curious"]   += r.get("question_count",0) * QUESTION_WEIGHT * w
        p["traits"]["dominant"]  += r.get("caps_msgs",0) * CAPS_DOMINANT_WEIGHT * w
        p["traits"]["combative"] += r.get("caps_msgs",0) * CAPS_COMBATIVE_WEIGHT * w

        # semantic keywords
        hits = extract_keyword_hits(r.get("context_sample",""))
        for k in ["engaging","curious","humorous","supportive","dominant","combative"]:
            p["traits"][k] += hits[k] * w
        for k in ["flirty","sexual","curse"]:
            p["style"][k] += hits[k] * w

    # finalize
    for p in profiles.values():
        m = max(p["messages"],1)
        norm = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["norm"] = norm

        active = sum(1 for v in norm.values() if v>0.1)
        p["confidence"] = min(1.0, math.log(m+1)/5) * (active/3 if active else 0)

        p["top"] = sorted(norm.items(), key=lambda x:x[1], reverse=True)[:3]

        p["archetype"] = "Profile forming"
        for name,rule in ARCHETYPES:
            if rule(norm):
                p["archetype"] = name
                break

        p["style_norm"] = {k:min(v/m,1.0) for k,v in p["style"].items()}

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# FORMAT FOR SL
# =================================================

def format_profile(p):
    lines = [
        f"🧠 {p['name']}",
        f"Confidence {int(p['confidence']*100)}%"
    ]
    for t,v in p["top"]:
        lines.append(f"• {t} ({int(v*100)}%)")
    lines.append(f"🧩 {p['archetype']}")

    styles = [f"• {k} ({int(v*100)}%)" for k,v in p["style_norm"].items() if v>0.05]
    if styles:
        lines.append("")
        lines.append("Style:")
        lines.extend(styles)

    return "\n".join(lines)

# =================================================
# ENDPOINTS
# =================================================

@app.route("/list_profiles")
def list_profiles():
    ranked = sorted(build_profiles().values(), key=lambda x:x["messages"], reverse=True)
    out = ["📊 Social Profiles:"]
    for p in ranked[:5]:
        out.extend(["", format_profile(p)])
    return Response("\n".join(out), mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
