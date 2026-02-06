from flask import Flask, Response
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ["GOOGLE_PROFILES_FEED"]

CACHE = {"profiles": None, "ts": 0}
CACHE_TTL = 300

NOW = time.time()

# =================================================
# WEIGHTS (REALISTIC + SL-TUNED)
# =================================================

TRAIT_WEIGHTS = {
    "engaging":   1.0,
    "curious":    0.9,
    "humorous":   1.3,
    "supportive": 1.2,
    "dominant":   1.1,
    "combative":  1.6,
}

STYLE_WEIGHTS = {
    "flirty": 1.1,
    "sexual": 1.3,
    "curse":  1.0
}

# =================================================
# KEYWORDS (SL CULTURE)
# =================================================

ENGAGING = {"hi","hey","yo","sup","wb","welcome"}
CURIOUS = {"why","how","what","where","when","who"}
HUMOR = {"lol","lmao","haha","rofl","😂","🤣"}
SUPPORT = {"sorry","hope","ok","there","np","hug","hugs"}
DOMINANT = {"listen","look","stop","wait","now"}
COMBATIVE = {"idiot","stupid","shut","wtf","dumb"}

FLIRTY = {"cute","hot","handsome","beautiful","kiss","xoxo"}
SEXUAL = {"sex","fuck","horny","wet","hard","naked"}
CURSE = {"fuck","shit","damn","bitch","asshole"}

NEGATORS = {"not","no","never","dont","can't","isn't"}

# =================================================
# HELPERS
# =================================================

def decay(ts):
    age = (NOW - ts) / 3600
    if age <= 1: return 1.0
    if age <= 24: return 0.7
    return 0.4

def extract_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b", text.lower())

    def neg(i):
        return any(w in NEGATORS for w in words[max(0,i-3):i])

    for i,w in enumerate(words):
        if neg(i): continue
        if w in ENGAGING: hits["engaging"] += 1
        if w in CURIOUS: hits["curious"] += 1
        if w in HUMOR: hits["humorous"] += 1
        if w in SUPPORT: hits["supportive"] += 1
        if w in DOMINANT: hits["dominant"] += 1
        if w in COMBATIVE: hits["combative"] += 1
        if w in FLIRTY: hits["flirty"] += 1
        if w in SEXUAL: hits["sexual"] += 1
        if w in CURSE: hits["curse"] += 1
    return hits

# =================================================
# FETCH DATA
# =================================================

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    m = re.search(r"setResponse\((\{.*\})\)", r.text, re.S)
    payload = json.loads(m.group(1))
    cols = [c["label"] for c in payload["table"]["cols"]]
    rows=[]
    for row in payload["table"]["rows"]:
        rec={}
        for i,cell in enumerate(row["c"]):
            rec[cols[i]] = cell["v"] if cell else 0
        rows.append(rec)
    return rows

# =================================================
# PERSONALITY TEXT
# =================================================

def summary(conf, t):
    if conf < 0.25:
        return "Barely spoke. Vibes pending."
    if t["humorous"] > 0.5:
        return "Here to joke, not to work."
    if t["supportive"] > 0.45:
        return "Comfort avatar energy."
    if t["dominant"] > 0.45:
        return "Low-key runs the room."
    if t["engaging"] > 0.45:
        return "Talks to literally everyone."
    return "Just existing. Menacingly."

def vibe(conf, t):
    if conf < 0.25:
        return "Just Vibing ✨"
    if t["humorous"] > 0.5:
        return "Comedy Mode 🎭"
    if t["supportive"] > 0.45:
        return "Comfort Mode 🫂"
    if t["dominant"] > 0.5:
        return "Main Character Energy 👑"
    return "Ambient Presence 🌫️"

# =================================================
# BUILD PROFILES
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    profiles={}
    rows = fetch_rows()

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid: continue

        ts = float(r.get("timestamp",NOW))
        w = decay(ts)

        p = profiles.setdefault(uid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "raw_traits": defaultdict(float),
            "raw_styles": defaultdict(float),
            "recent": 0
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs * w
        if NOW - ts < 3600:
            p["recent"] += msgs

        hits = extract_hits(r.get("context_sample",""))
        for k,v in hits.items():
            if k in TRAIT_WEIGHTS:
                p["raw_traits"][k] += v * TRAIT_WEIGHTS[k] * w
            if k in STYLE_WEIGHTS:
                p["raw_styles"][k] += v * STYLE_WEIGHTS[k] * w

    out=[]
    for p in profiles.values():
        m = max(p["messages"],1)
        conf = min(1.0, math.log(m+1)/4)
        conf_w = max(0.05, conf**1.5)

        traits={}
        for k in ["engaging","curious","humorous","supportive","dominant","combative"]:
            traits[k] = min((p["raw_traits"][k]/m)*conf_w,1.0)

        styles={}
        for k in ["flirty","sexual","curse"]:
            styles[k] = min((p["raw_styles"][k]/(m*0.3))*conf_w,1.0)

        # 🔥 NEW DERIVED SIGNALS
        drama = min((traits["combative"]+styles["curse"])*0.8,1.0)
        safe_flirt = max(0, styles["flirty"] - traits["combative"])
        comfort = traits["supportive"] * (1-traits["dominant"])
        club = min((styles["curse"]+styles["sexual"]+traits["dominant"])*0.6,1.0)
        hangout = min((traits["supportive"]+traits["curious"])*0.6,1.0)

        # 🏅 WEEKLY BADGES
        badges=[]
        if traits["humorous"]>0.55: badges.append("🎭 Comedy MVP")
        if traits["supportive"]>0.5: badges.append("🫂 Comfort Avatar")
        if drama>0.6: badges.append("🔥 Drama Magnet")
        if safe_flirt>0.4: badges.append("💖 Safe to Flirt")
        if conf>0.8: badges.append("👑 Social Regular")

        out.append({
            "name": p["name"],
            "confidence": int(conf*100),
            "vibe": vibe(conf,traits),
            "summary": summary(conf,traits),
            "traits": {k:int(v*100) for k,v in traits.items()},
            "styles": {k:int(v*100) for k,v in styles.items()},
            "risk": int(drama*100),
            "club": int(club*100),
            "hangout": int(hangout*100),
            "live": "Active 🔥" if p["recent"]>3 else "Chilling 💤",
            "badges": badges
        })

    CACHE["profiles"] = out
    CACHE["ts"] = time.time()
    return out

# =================================================
# ENDPOINT
# =================================================

@app.route("/leaderboard")
def leaderboard():
    data = build_profiles()
    return Response(json.dumps(data), mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"})

@app.route("/")
def ok():
    return "OK"
