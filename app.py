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
# TRAITS / STYLES DEFINITIONS (ALWAYS PRESENT)
# =================================================

TRAITS = ["engaging","curious","humorous","supportive","dominant","combative"]
STYLES = ["flirty","sexual","curse"]

# =================================================
# SL-CULTURE KEYWORDS
# =================================================

KEYWORDS = {
    "engaging":  {"hi","hey","hello","yo","sup","wb","welcome"},
    "curious":   {"why","how","what","where","when","who"},
    "humorous":  {"lol","lmao","haha","rofl","😂","🤣"},
    "supportive":{"sorry","hope","hugs","hug","❤","💖","care"},
    "dominant":  {"listen","look","stop","now","enough"},
    "combative": {"idiot","stupid","shut","wtf","trash","drama"},

    "flirty":    {"cute","hot","handsome","beautiful","kiss","xoxo","😘"},
    "sexual":    {"sex","fuck","fucking","horny","wet","hard","naked"},
    "curse":     {"fuck","shit","damn","bitch","asshole"}
}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# HELPERS
# =================================================

def clamp(v): 
    return max(0.0, min(v, 1.0))

def extract_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b", text.lower())
    for i,w in enumerate(words):
        if i > 0 and words[i-1] in NEGATORS:
            continue
        for k,ws in KEYWORDS.items():
            if w in ws:
                hits[k] += 1
    return hits

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    match = re.search(r"setResponse\((\{.*\})\)", r.text, re.S)
    payload = json.loads(match.group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]

    rows = []
    for row in payload["table"]["rows"]:
        rec = {}
        for i,cell in enumerate(row["c"]):
            rec[cols[i] if cols[i] else f"col_{i}"] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

# =================================================
# FUNNY SUMMARY + BADGES
# =================================================

def build_summary(name, conf, traits, styles, risk):
    if conf < 20:
        return "👻 Barely spoke. Vibes pending. Observed mostly breathing."

    top = max(traits, key=traits.get)
    tone = {
        "engaging":"Talks to literally everyone.",
        "curious":"Asks questions like an NPC with sentience.",
        "humorous":"Here to joke, not to work.",
        "supportive":"Therapist energy. Probably hugs strangers.",
        "dominant":"Main character syndrome detected.",
        "combative":"Thrives on chaos and local chat drama."
    }[top]

    spice = " ⚠️ spicy" if risk > 60 else ""
    return f"{tone}{spice}"

def build_badges(conf, traits, styles, risk):
    badges = []
    if conf < 20: badges.append("👀 Lurker")
    if traits["humorous"] > 60: badges.append("🎤 Comedy HUD")
    if traits["dominant"] > 60: badges.append("🗣️ Voice of the Sim")
    if styles["flirty"] > 60: badges.append("💋 Flirt Warning")
    if risk > 70: badges.append("🚨 Drama Magnet")
    if traits["supportive"] > 60: badges.append("🫂 Comfort Avatar")
    return badges

# =================================================
# CORE LOGIC
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    rows = fetch_rows()
    profiles = {}

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid:
            continue

        p = profiles.setdefault(uid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "raw": defaultdict(float)
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs

        hits = extract_hits(r.get("context_sample",""))
        for k,v in hits.items():
            p["raw"][k] += v

        # caps yelling = dominance + combativeness
        caps = r.get("caps_msgs",0)
        p["raw"]["dominant"] += caps * 0.4
        p["raw"]["combative"] += caps * 0.25

    output = []

    for p in profiles.values():
        m = max(p["messages"],1)

        # Bayesian smoothing baseline
        traits = {}
        for t in TRAITS:
            traits[t] = clamp((p["raw"].get(t,0) + 1) / (m + 6))

        styles = {}
        for s in STYLES:
            styles[s] = clamp((p["raw"].get(s,0) + 0.5) / (m + 8))

        confidence = clamp(math.log(m+1)/4)
        reputation = clamp(
            traits["engaging"] +
            traits["supportive"] -
            traits["combative"]*0.6
        )

        risk = clamp(
            traits["combative"]*0.6 +
            traits["dominant"]*0.4 +
            styles["curse"]*0.5
        )

        summary = build_summary(p["name"], confidence*100, traits, styles, risk*100)
        badges = build_badges(confidence*100, traits, styles, risk*100)

        output.append({
            "name": p["name"],
            "confidence": int(confidence*100),
            "reputation": int(reputation*100),
            "gravity": 0,

            "role": "Performer" if traits["engaging"] > traits["curious"] else "Audience",
            "archetype": "Profile forming" if confidence < 40 else max(traits, key=traits.get).title(),

            "summary": summary,
            "badges": badges,

            "traits": {k:int(v*100) for k,v in traits.items()},
            "styles": {k:int(v*100) for k,v in styles.items()},
            "risk": int(risk*100)
        })

    CACHE["profiles"] = output
    CACHE["ts"] = time.time()
    return output

# =================================================
# ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    data = build_profiles()
    return Response(
        json.dumps(data),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"}
    )

@app.route("/")
def ok():
    return "OK"
