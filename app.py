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

STRUCTURAL_WEIGHTS = {
    "question": 0.15,
    "caps": 0.4
}

STYLE_DISPLAY_MULTIPLIER = 1.8
STYLE_DISPLAY_THRESHOLD = 0.02

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
    ("Social Catalyst",    lambda t: t["engaging"] > 0.12 and t["curious"] > 0.06),
    ("Entertainer",        lambda t: t["humorous"] > 0.12),
    ("Debater",            lambda t: t["combative"] > 0.12),
    ("Presence Dominator", lambda t: t["dominant"] > 0.12),
    ("Support Anchor",     lambda t: t["supportive"] > 0.12),
    ("Quiet Thinker",      lambda t: t["concise"] > 0.18),
]

ARCHETYPE_EXPLANATIONS = {
    "Social Catalyst": "Keeps conversations alive like caffeine for humans.",
    "Entertainer": "Here for the laughs. Would absolutely bring snacks.",
    "Debater": "Argues for sport. Facts optional. Passion mandatory.",
    "Presence Dominator": "Enters rooms like a patch note nobody asked for.",
    "Support Anchor": "Emotional first aid kit. Free hugs included.",
    "Quiet Thinker": "Observes everything. Speaks when it matters.",
    "Profile forming": "Still loading personality… please stand by."
}

STYLE_EXPLANATIONS = {
    "flirty": "Harmless chaos. Compliments deployed strategically.",
    "sexual": "Zero chill detected. Viewer discretion advised.",
    "curse": "Uses swear words like punctuation."
}

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

def decay(ts, scale=86400):
    try:
        age = (time.time() - float(ts)) / scale
        if age <= 1: return 1.0
        if age <= 7: return 0.6
        return 0.3
    except:
        return 1.0

def style_decay(ts):
    try:
        hrs = (time.time() - float(ts)) / 3600
        if hrs <= 1: return 1.0
        if hrs <= 6: return 0.4
        return 0.1
    except:
        return 0.3

# =================================================
# DATA FETCH
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    payload = json.loads(re.search(r"setResponse\((\{.*\})\)", r.text, re.S).group(1))
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
    name_mentions = defaultdict(int)

    rows = fetch_rows()

    # pass 1: detect name mentions
    for r in rows:
        txt = r.get("context_sample","").lower()
        for other in rows:
            name = other.get("display_name","").lower()
            if name and name in txt:
                name_mentions[other.get("avatar_uuid")] += 1

    for r in rows:
        uuid = r.get("avatar_uuid")
        if not uuid: continue

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

        short = r.get("short_msgs",0)
        p["traits"]["concise"] += (short / msgs) * 1.2 * w

        density = min(msgs / 10, 1.0)
        p["traits"]["engaging"] += density * 0.4 * w
        p["traits"]["dominant"] += density * 0.6 * w

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

        # gravity inference
        p["gravity"] += name_mentions[uuid] * 0.6 * w
        if " you " in f" {r.get('context_sample','').lower()} ":
            p["gravity"] += 0.4 * w

    # finalize
    for p in profiles.values():
        m = max(p["messages"],1)

        p["norm"] = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["gravity_norm"] = min(p["gravity"]/m,1.0)

        # reputation drift
        signal = (
            p["norm"].get("engaging",0)
          + p["norm"].get("supportive",0)
          - p["norm"].get("combative",0)*0.7
        )
        p["reputation"]["score"] = p["reputation"]["score"]*0.92 + max(0,signal)*0.08

        # troll detection
        p["troll_score"] = min(
            p["norm"].get("combative",0)*0.4 +
            (1-p["gravity_norm"])*0.4 +
            p["norm"].get("dominant",0)*0.2,
            1.0
        )
        p["troll_flag"] = p["troll_score"] > 0.65 and m > 12

        # manipulation resistance
        vals = sorted(p["norm"].values(), reverse=True)
        imbalance = vals[0]-vals[2] if len(vals)>=3 else 0
        damp = 1 - min(imbalance*0.6,0.5)
        for k in p["norm"]:
            p["norm"][k] *= damp

        # role
        performer = m*0.4 + p["gravity_norm"]*2 - p["norm"].get("concise",0)
        audience  = p["norm"].get("concise",0)*1.5 - p["gravity_norm"]
        p["role"] = "Performer" if performer > audience else "Audience"

        p["confidence"] = min(1.0, math.log(m+1)/4)

        p["archetype"] = "Profile forming"
        if p["confidence"] > 0.35 and m >= 10:
            for name,rule in ARCHETYPES:
                if rule(p["norm"]):
                    p["archetype"] = name
                    break

        p["style_norm"] = {
            k:min((v/(m*0.25))*STYLE_DISPLAY_MULTIPLIER,1.0)
            for k,v in p["style"].items()
        }

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# OUTPUT
# =================================================

def format_profile(p):
    lines = [
        f"🧠 {p['name']}",
        f"Confidence {int(p['confidence']*100)}%",
    ]

    for k,v in sorted(p["norm"].items(), key=lambda x:x[1], reverse=True)[:3]:
        lines.append(f"• {k} ({int(v*100)}%)")

    lines.append(f"🧩 {p['archetype']}")
    lines.append(f"   {ARCHETYPE_EXPLANATIONS.get(p['archetype'],'')}")

    lines.append("")
    lines.append(f"🎭 Role: {p['role']}")
    lines.append(f"🧲 Social Gravity: {int(p['gravity_norm']*100)}%")
    lines.append(f"📈 Reputation: {int(p['reputation']['score']*100)}%")

    if p["troll_flag"]:
        lines.append("⚠️ Pattern detected: Disruptive influence")

    styles = [(k,v) for k,v in p["style_norm"].items() if v>STYLE_DISPLAY_THRESHOLD]
    if styles:
        lines.append("")
        lines.append("Style:")
        for k,v in styles:
            lines.append(f"• {k} ({int(v*100)}%) — {STYLE_EXPLANATIONS[k]}")

    return "\n".join(lines)

# =================================================
# ENDPOINTS
# =================================================

@app.route("/lookup_avatars", methods=["POST"])
def lookup():
    profiles = build_profiles()
    uuids = request.get_json(force=True)
    out=[]
    for u in uuids:
        out.append(format_profile(profiles.get(u,{"name":"Unknown","confidence":0,"norm":{}})))
    return Response("\n\n".join(out), mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
