from flask import Flask, Response
import os, time, math, requests, json, re
from collections import defaultdict

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")

CACHE = {"profiles": {}, "ts": 0}
CACHE_TTL = 300

NOW_WINDOW = 45 * 60  # 45 minutes for "Vibe Right Now"

# =================================================
# KEYWORDS – SL CULTURE TUNED
# =================================================

ENGAGING_WORDS  = {"hi","hey","hello","yo","sup","wb","welcome"}
CURIOUS_WORDS   = {"why","how","what","where","when","who"}
HUMOR_WORDS     = {"lol","lmao","haha","rofl","😂","🤣"}
SUPPORT_WORDS   = {"sorry","hope","hugs","hug","better","there","ok","its ok"}
DOMINANT_WORDS  = {"listen","look","stop","now","enough","sit","pay attention"}
COMBATIVE_WORDS = {"idiot","stupid","shut","wrong","wtf","trash","dumb"}

FLIRTY_WORDS = {"cute","hot","handsome","beautiful","kiss","kisses","xoxo","sexy"}
SEXUAL_WORDS = {"sex","fuck","fucking","horny","wet","hard","naked"}
CURSE_WORDS  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

# =================================================
# HELPERS
# =================================================

def extract_keyword_hits(text):
    hits = defaultdict(int)
    if not text:
        return hits

    words = re.findall(r"\b\w+\b|[.!?]", text.lower())

    def negated(i):
        for j in range(max(0, i-4), i):
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
        if w in FLIRTY_WORDS:    hits["flirty"] += 1
        if w in SEXUAL_WORDS:    hits["sexual"] += 1
        if w in CURSE_WORDS:     hits["curse"] += 1

    return hits

def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

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
# PROFILE BUILDER
# =================================================

def build_profiles():
    if CACHE["profiles"] and time.time() - CACHE["ts"] < CACHE_TTL:
        return CACHE["profiles"]

    rows = fetch_rows()
    profiles = {}

    now = time.time()

    for r in rows:
        uuid = r.get("avatar_uuid")
        if not uuid:
            continue

        ts = float(r.get("timestamp", now))
        recent = now - ts < NOW_WINDOW

        p = profiles.setdefault(uuid, {
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits_raw": defaultdict(float),
            "style_raw": defaultdict(float),
            "recent_raw": defaultdict(float),
            "confidence": 0,
        })

        msgs = max(int(r.get("messages",1)),1)
        p["messages"] += msgs

        hits = extract_keyword_hits(r.get("context_sample",""))

        for k,v in hits.items():
            if k in {"engaging","curious","humorous","supportive","dominant","combative"}:
                p["traits_raw"][k] += v
                if recent:
                    p["recent_raw"][k] += v
            if k in {"flirty","sexual","curse"}:
                p["style_raw"][k] += v

    # =================================================
    # NORMALIZE + DERIVED SIGNALS
    # =================================================

    for p in profiles.values():
        m = max(p["messages"],1)

        traits = {k: clamp(v/(m*2.5)) for k,v in p["traits_raw"].items()}
        styles = {k: clamp(v/(m*3.0)) for k,v in p["style_raw"].items()}
        recent = {k: clamp(v/5.0) for k,v in p["recent_raw"].items()}

        confidence = clamp(math.log(m+1)/4)

        # Reputation (soft, forgiving)
        reputation = clamp(
            0.5
            + traits.get("engaging",0)*0.2
            + traits.get("supportive",0)*0.2
            - traits.get("combative",0)*0.25
        )

        # Drama / Risk
        risk = clamp(
            traits.get("combative",0)*0.5 +
            traits.get("dominant",0)*0.3
        )

        # Safe to flirt
        safe_flirt = clamp(
            styles.get("flirty",0)*0.5 +
            traits.get("supportive",0)*0.3 -
            traits.get("combative",0)*0.4
        )

        # Comfort
        comfort = clamp(
            traits.get("supportive",0)*0.6 -
            traits.get("dominant",0)*0.2
        )

        # Club vs Hangout
        club = clamp(
            traits.get("dominant",0) +
            traits.get("humorous",0)
        )
        hangout = clamp(
            traits.get("curious",0) +
            traits.get("supportive",0)
        )

        # Vibe right now
        if confidence < 0.15:
            vibe = "Vibes loading… ⏳"
        elif recent.get("humorous",0) > 0.4:
            vibe = "Joke Mode 🤡"
        elif recent.get("flirty",0) > 0.3:
            vibe = "Flirty Energy 💋"
        elif recent.get("combative",0) > 0.3:
            vibe = "Spicy 😬"
        elif recent.get("supportive",0) > 0.3:
            vibe = "Comfort Mode 🫂"
        else:
            vibe = "Just Vibing ✨"

        # Funny summary
        if confidence < 0.2:
            summary = "Barely spoke. Vibes pending."
        elif traits.get("humorous",0) > 0.4:
            summary = "Here to joke, not to work."
        elif traits.get("supportive",0) > 0.4:
            summary = "Low drama, high emotional bandwidth."
        elif traits.get("combative",0) > 0.4:
            summary = "Will argue with a chair."
        elif traits.get("curious",0) > 0.4:
            summary = "Asks questions like an NPC with sentience."
        else:
            summary = "Just existing. Menacingly."

        p.update({
            "traits": {k:int(v*100) for k,v in traits.items()},
            "styles": {k:int(v*100) for k,v in styles.items()},
            "confidence": int(confidence*100),
            "reputation": int(reputation*100),
            "risk": int(risk*100),
            "safe_flirt": int(safe_flirt*100),
            "comfort": int(comfort*100),
            "club_energy": int(club*100),
            "hangout_energy": int(hangout*100),
            "vibe": vibe,
            "summary": summary,
        })

    CACHE["profiles"] = profiles
    CACHE["ts"] = time.time()
    return profiles

# =================================================
# API
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
            "confidence": p["confidence"],
            "reputation": p["reputation"],
            "summary": p["summary"],
            "vibe": p["vibe"],
            "traits": p["traits"],
            "styles": p["styles"],
            "risk": p["risk"],
            "safe_flirt": p["safe_flirt"],
            "comfort": p["comfort"],
            "club_energy": p["club_energy"],
            "hangout_energy": p["hangout_energy"],
        })

    return Response(
        json.dumps(out),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin":"*"}
    )

@app.route("/")
def ok():
    return "OK"
