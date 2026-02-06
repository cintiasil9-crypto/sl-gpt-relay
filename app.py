from flask import Flask, Response, jsonify
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
# WEIGHTS / THRESHOLDS
# =================================================

QUESTION_WEIGHT = 0.15
CAPS_DOM_WEIGHT = 0.12
CAPS_COMB_WEIGHT = 0.10

MODIFIER_WEIGHT = 0.6     # modifiers are softer than traits
ARCH_MIN_MSGS = 8
TRAIT_MIN = 0.08

# =================================================
# KEYWORDS
# =================================================

ENGAGING = {"hi","hey","hello","yo","welcome"}
CURIOUS  = {"why","how","what","where","when","who"}
HUMOR    = {"lol","haha","lmao","rofl"}
SUPPORT  = {"sorry","hope","hug","hugs","better"}
DOMINANT = {"listen","look","stop","now","enough"}
COMB     = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY = {"cute","hot","handsome","beautiful","xoxo","kiss"}
SEXUAL = {"sex","fuck","horny","wet","hard","naked"}
CURSE  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# ARCHETYPES
# =================================================

ARCHETYPES = [
    ("Support Anchor","Emotionally stabilizing presence.",
     lambda t: t["supportive"] > 0.18),

    ("Entertainer","Drives humor and energy.",
     lambda t: t["humorous"] > 0.18),

    ("Social Catalyst","Pulls others into conversation.",
     lambda t: t["engaging"] > 0.18 and t["curious"] > 0.10),

    ("Debater","Thrives on disagreement.",
     lambda t: t["combative"] > 0.20),

    ("Presence Dominator","Controls conversational flow.",
     lambda t: t["dominant"] > 0.18),

    ("Quiet Thinker","Observes more than speaks.",
     lambda t: t["concise"] > 0.30 and t["engaging"] < 0.10),
]

# =================================================
# MODIFIER EXPLANATIONS
# =================================================

MODIFIER_EXPLANATIONS = {
    "flirty": "Playful, complimentary energy.",
    "sexual": "Explicit or suggestive tone.",
    "curse": "Uses profanity for emphasis."
}

# =================================================
# HELPERS
# =================================================

def decay(ts):
    try:
        days = (time.time() - float(ts)) / 86400
        return 1.0 if days <= 1 else 0.6 if days <= 7 else 0.3
    except:
        return 1.0

def extract_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b", text.lower())

    def neg(i):
        return any(words[j] in NEGATORS for j in range(max(0,i-2), i))

    for i,w in enumerate(words):
        if neg(i): continue

        if w in ENGAGING:  hits["engaging"]  += 1
        if w in CURIOUS:   hits["curious"]   += 1
        if w in HUMOR:     hits["humorous"]  += 1
        if w in SUPPORT:   hits["supportive"]+= 1
        if w in DOMINANT:  hits["dominant"]  += 1
        if w in COMB:      hits["combative"] += 1

        if w in FLIRTY: hits["flirty"] += 1
        if w in SEXUAL: hits["sexual"] += 1
        if w in CURSE:  hits["curse"]  += 1

    return hits

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

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time()-CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles = {}
    name_mentions = defaultdict(int)
    rows = fetch_rows()

    # gravity pass
    for r in rows:
        txt = r.get("context_sample","").lower()
        for o in rows:
            name = o.get("display_name","").lower()
            if name and name in txt:
                name_mentions[o.get("avatar_uuid")] += 1

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid: continue

        w = decay(r.get("timestamp_utc",time.time()))
        msgs = max(int(r.get("messages",1)),1)

        p = profiles.setdefault(uid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "modifiers": defaultdict(float),
            "gravity": 0,
            "reputation": 0.5
        })

        p["messages"] += msgs * w

        p["traits"]["concise"] += (r.get("short_msgs",0)/msgs) * w
        p["traits"]["curious"] += r.get("question_count",0) * QUESTION_WEIGHT * w

        caps = r.get("caps_msgs",0)
        p["traits"]["dominant"]  += caps * CAPS_DOM_WEIGHT * w
        p["traits"]["combative"] += caps * CAPS_COMB_WEIGHT * w

        hits = extract_hits(r.get("context_sample",""))
        for k,v in hits.items():
            if k in ["engaging","curious","humorous","supportive","dominant","combative"]:
                p["traits"][k] += v * w
            else:
                p["modifiers"][k] += v * w * MODIFIER_WEIGHT

        p["gravity"] += name_mentions[uid] * 0.6 * w

    # finalize
    for p in profiles.values():
        m = max(p["messages"],1)

        p["norm"] = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["modifier_norm"] = {k:min(v/m,1.0) for k,v in p["modifiers"].items()}
        p["gravity_norm"] = min(p["gravity"]/m,1.0)

        # reputation (modifiers influence negatively)
        signal = (
            p["norm"].get("supportive",0)
          + p["norm"].get("engaging",0)
          - p["norm"].get("combative",0)*0.7
          - p["modifier_norm"].get("sexual",0)*0.4
          - p["modifier_norm"].get("curse",0)*0.3
        )
        p["reputation"] = min(1.0, max(0.0, p["reputation"]*0.9 + signal*0.1))

        # role
        performer = p["norm"].get("engaging",0) + p["gravity_norm"]
        audience  = p["norm"].get("concise",0)
        p["role"] = "Performer" if performer > audience else "Audience"

        # troll
        p["troll"] = (
            p["norm"].get("combative",0) > 0.25
            or p["modifier_norm"].get("curse",0) > 0.25
        )

        # confidence
        active = sum(1 for v in p["norm"].values() if v > TRAIT_MIN)
        p["confidence"] = min(1.0, math.log(m+1)/4) * min(1.0, active/4)

        # archetype
        p["archetype"] = "Profile forming"
        if m >= ARCH_MIN_MSGS:
            for name,desc,rule in ARCHETYPES:
                if rule(p["norm"]):
                    p["archetype"] = name
                    break

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# SECOND LIFE ENDPOINT
# =================================================

def format_profile(p):
    lines = [
        f"🧠 {p['name']}",
        f"Confidence {int(p['confidence']*100)}%"
    ]

    top = sorted(p["norm"].items(), key=lambda x:x[1], reverse=True)[:3]
    for t,v in top:
        lines.append(f"• {t} ({int(v*100)}%)")

    lines.append(f"🧩 {p['archetype']}")

    mods = [
        f"• {k} ({int(v*100)}%)"
        for k,v in p["modifier_norm"].items()
        if v > 0.08
    ]
    if mods:
        lines.append("")
        lines.append("Style:")
        lines.extend(mods)

    return "\n".join(lines)

@app.route("/list_profiles")
def list_profiles():
    ranked = sorted(build_profiles().values(), key=lambda p:p["messages"], reverse=True)
    out = ["📊 Social Profiles:"]
    for p in ranked[:5]:
        out.extend(["", format_profile(p)])
    return Response("\n".join(out), mimetype="text/plain")

# =================================================
# WEBSITE ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    ranked = sorted(
        build_profiles().values(),
        key=lambda p:(p["reputation"],p["gravity_norm"]),
        reverse=True
    )

    out = []
    for i,p in enumerate(ranked,1):
        out.append({
            "rank": i,
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "reputation": int(p["reputation"]*100),
            "gravity": int(p["gravity_norm"]*100),
            "role": p["role"],
            "archetype": p["archetype"],
            "traits": {k:int(v*100) for k,v in p["norm"].items()},
            "modifiers": {
                k:{
                    "score": int(v*100),
                    "explanation": MODIFIER_EXPLANATIONS[k]
                }
                for k,v in p["modifier_norm"].items()
            },
            "troll": p["troll"]
        })

    return jsonify(out)

@app.route("/")
def ok():
    return "OK"
