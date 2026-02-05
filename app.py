from flask import Flask, Response, request
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

TRAIT_KEYWORD_WEIGHTS = {
    "engaging":   1.6,
    "curious":    1.4,
    "humorous":   1.5,
    "supportive": 1.5,
    "dominant":   1.7,
    "combative":  1.6,
}

STRUCTURAL_WEIGHTS = {
    "question":  0.15,
    "caps_dom":  0.15,
    "caps_comb": 0.12,
    "concise":   1.0
}

STYLE_WEIGHTS = {
    "flirty": 1.2,
    "sexual": 1.3,
    "curse":  1.1
}

STYLE_DISPLAY_MULTIPLIER = 1.8

# =================================================
# KEYWORDS
# =================================================

ENGAGING_WORDS  = {"hi","hey","hello","hiya","yo","welcome"}
CURIOUS_WORDS   = {"why","how","what","where","when","who"}
HUMOR_WORDS     = {"lol","lmao","haha","rofl"}
SUPPORT_WORDS   = {"sorry","hope","hugs","hug","better"}
DOMINANT_WORDS  = {"listen","look","stop","enough","now"}
COMBATIVE_WORDS = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY_WORDS = {"cute","hot","handsome","beautiful","kiss","kisses","hugsss","xoxo","flirt"}
SEXUAL_WORDS = {"sex","fuck","fucking","horny","wet","hard","naked"}
CURSE_WORDS  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# FUNNY EXPLANATIONS
# =================================================

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
# KEYWORD PARSER
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

    def negated(i):
        return any(words[j] in NEGATORS for j in range(max(0, i-2), i))

    for i, w in enumerate(words):
        if negated(i):
            continue

        if w in ENGAGING_WORDS:   hits["engaging"]   = 1
        if w in CURIOUS_WORDS:    hits["curious"]    = 1
        if w in HUMOR_WORDS:      hits["humorous"]   = 1
        if w in SUPPORT_WORDS:    hits["supportive"] = 1
        if w in DOMINANT_WORDS:   hits["dominant"]   = 1
        if w in COMBATIVE_WORDS:  hits["combative"]  = 1

        if w in FLIRTY_WORDS:     hits["flirty"]     = 1
        if w in SEXUAL_WORDS:     hits["sexual"]     = 1
        if w in CURSE_WORDS:      hits["curse"]      = 1

    return hits

# =================================================
# DATA FETCH
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    payload = json.loads(
        re.search(r"setResponse\((\{.*\})\)", r.text, re.S).group(1)
    )
    cols = [c["label"] for c in payload["table"]["cols"]]

    rows = []
    for row in payload["table"]["rows"]:
        rec = {}
        for i, cell in enumerate(row["c"]):
            rec[cols[i] if cols[i] else f"col_{i}"] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

# =================================================
# DECAY
# =================================================

def decay_weight(ts):
    try:
        ts = int(float(ts))
        days = (time.time() - ts) / 86400
        if days <= 1: return 1.0
        if days <= 7: return 0.6
        return 0.3
    except:
        return 1.0

# =================================================
# ARCHETYPES
# =================================================

ARCHETYPES = [
    ("Social Catalyst",     lambda t: t["engaging"] > t["concise"] and t["curious"] > 0.05),
    ("Entertainer",         lambda t: t["humorous"] >= max(t["engaging"], t["curious"])),
    ("Debater",             lambda t: t["combative"] >= max(t["supportive"], t["humorous"])),
    ("Presence Dominator",  lambda t: t["dominant"] >= max(t["engaging"], t["curious"])),
    ("Support Anchor",      lambda t: t["supportive"] >= max(t["combative"], t["dominant"])),
    ("Quiet Thinker",       lambda t: t["concise"] >= max(t["engaging"], t["humorous"])),
]

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles = {}

    for r in fetch_rows():
        uuid = r.get("avatar_uuid")
        if not uuid:
            continue

        ts = r.get("timestamp") or next(iter(r.values()), time.time())
        w = decay_weight(ts)

        p = profiles.setdefault(uuid, {
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": {k:0 for k in [
                "engaging","curious","humorous",
                "supportive","dominant","combative","concise"
            ]},
            "style": {"flirty":0,"sexual":0,"curse":0}
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w

        p["traits"]["concise"] += (1 - r.get("short_msgs",0)/msgs) * w
        p["traits"]["curious"] += r.get("question_count",0) * STRUCTURAL_WEIGHTS["question"] * w
        p["traits"]["dominant"] += r.get("caps_msgs",0) * STRUCTURAL_WEIGHTS["caps_dom"] * w
        p["traits"]["combative"] += r.get("caps_msgs",0) * STRUCTURAL_WEIGHTS["caps_comb"] * w

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k in TRAIT_KEYWORD_WEIGHTS:
            p["traits"][k] += hits[k] * TRAIT_KEYWORD_WEIGHTS[k] * w
        for k in STYLE_WEIGHTS:
            p["style"][k] += hits[k] * STYLE_WEIGHTS[k] * w

    for p in profiles.values():
        m = max(p["messages"],1)
        norm = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["norm"] = norm

        active = sum(1 for v in norm.values() if v > 0.03)
        p["confidence"] = min(1.0, (math.log(m+1)/4) * (0.5 + active/6))

        p["top"] = sorted(norm.items(), key=lambda x:x[1], reverse=True)[:3]

        p["archetype"] = "Profile forming"
        for name, rule in ARCHETYPES:
            if rule(norm):
                p["archetype"] = name
                break

        p["style_norm"] = {
            k:min((v/m)*STYLE_DISPLAY_MULTIPLIER,1.0)
            for k,v in p["style"].items()
        }

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

    arch = p["archetype"]
    lines.append(f"🧩 {arch}")
    lines.append(f"   {ARCHETYPE_EXPLANATIONS.get(arch,'')}")

    styles = [k for k,v in p["style_norm"].items() if v > 0.05]
    if styles:
        lines.append("")
        lines.append("Style:")
        for k in styles:
            lines.append(f"• {k} — {STYLE_EXPLANATIONS[k]}")

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

@app.route("/lookup_avatars", methods=["POST"])
def lookup_avatars():
    profiles = build_profiles()
    uuids = request.get_json(force=True)

    out = []
    for u in uuids:
        p = profiles.get(u)
        if p:
            out.extend(["", format_profile(p)])
        else:
            out.extend(["", "🧠 Unknown Avatar\nNo rating yet."])

    return Response("\n".join(out) if out else "⚠️ No data returned.", mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
