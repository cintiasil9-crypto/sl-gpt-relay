import time, math, requests, json, re
from collections import defaultdict

GOOGLE_PROFILES_FEED = None  # injected by apps

# =========================
# CONSTANTS
# =========================

QUESTION_WEIGHT = 0.15
CAPS_DOM_WEIGHT = 0.12
CAPS_COMB_WEIGHT = 0.10
MODIFIER_WEIGHT = 0.6

TRAIT_MIN = 0.08
ARCH_MIN_MSGS = 6

ENGAGING  = {"hi","hey","hello","yo","welcome"}
CURIOUS   = {"why","how","what","where","when","who"}
HUMOR     = {"lol","haha","lmao","rofl"}
SUPPORT   = {"sorry","hope","hug","hugs","better"}
DOMINANT  = {"listen","look","stop","now","enough"}
COMBATIVE = {"idiot","stupid","shut","wrong","wtf"}

FLIRTY = {"cute","hot","handsome","beautiful","kiss","xoxo"}
SEXUAL = {"sex","fuck","horny","wet","hard","naked"}
CURSE  = {"fuck","shit","damn","bitch","asshole","wtf"}

NEGATORS = {"not","no","never","dont","don't","isnt","isn't","cant","can't"}

ARCHETYPES = [
    ("Support Anchor",     lambda t: t["supportive"] > 0.18),
    ("Entertainer",        lambda t: t["humorous"] > 0.18),
    ("Social Catalyst",    lambda t: t["engaging"] > 0.18 and t["curious"] > 0.10),
    ("Presence Dominator", lambda t: t["dominant"] > 0.18),
    ("Debater",            lambda t: t["combative"] > 0.22),
    ("Quiet Thinker",      lambda t: t["concise"] > 0.30 and t["engaging"] < 0.10),
]

# =========================
# HELPERS
# =========================

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
        if w in COMBATIVE: hits["combative"] += 1

        if w in FLIRTY: hits["flirty"] += 1
        if w in SEXUAL: hits["sexual"] += 1
        if w in CURSE:  hits["curse"]  += 1

    return hits

def fetch_rows():
    r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
    payload = json.loads(re.search(r"setResponse\((\{.*\})\)", r.text, re.S).group(1))
    cols = [c["label"] or f"col_{i}" for i,c in enumerate(payload["table"]["cols"])]

    rows = []
    for row in payload["table"]["rows"]:
        rec = {}
        for i,cell in enumerate(row["c"]):
            rec[cols[i]] = cell["v"] if cell else None
        rows.append(rec)
    return rows

# =========================
# CORE ENGINE
# =========================

def build_profiles(feed_url):
    global GOOGLE_PROFILES_FEED
    GOOGLE_PROFILES_FEED = feed_url

    profiles = {}
    rows = fetch_rows()

    for r in rows:
        uid = r.get("avatar_uuid")
        if not uid:
            continue

        w = decay(r.get("timestamp_utc", time.time()))
        msgs = max(int(r.get("messages") or 1), 1)

        p = profiles.setdefault(uid,{
            "name": r.get("display_name","Unknown"),
            "messages": 0,
            "traits": defaultdict(float),
            "modifiers": defaultdict(float),
            "reputation": 0.5,
            "gravity": 0
        })

        p["messages"] += msgs * w
        p["traits"]["concise"] += (r.get("short_msgs") or 0)/msgs * w
        p["traits"]["curious"] += (r.get("question_count") or 0)*QUESTION_WEIGHT*w

        caps = r.get("caps_msgs") or 0
        p["traits"]["dominant"]  += caps*CAPS_DOM_WEIGHT*w
        p["traits"]["combative"] += caps*CAPS_COMB_WEIGHT*w

        hits = extract_hits(r.get("context_sample",""))
        for k,v in hits.items():
            if k in ["engaging","curious","humorous","supportive","dominant","combative"]:
                p["traits"][k] += v*w
            else:
                p["modifiers"][k] += v*MODIFIER_WEIGHT*w

    # finalize
    for p in profiles.values():
        m = max(p["messages"],1)

        p["traits_norm"] = {k:min(v/m,1.0) for k,v in p["traits"].items()}
        p["modifiers_norm"] = {k:min(v/m,1.0) for k,v in p["modifiers"].items()}

        p["reputation"] = min(1.0, max(0.0,
            0.5
            + p["traits_norm"].get("supportive",0)*0.3
            + p["traits_norm"].get("engaging",0)*0.2
            - p["traits_norm"].get("combative",0)*0.4
            - p["modifiers_norm"].get("sexual",0)*0.3
            - p["modifiers_norm"].get("curse",0)*0.2
        ))

        p["role"] = "Performer" if (
            p["traits_norm"].get("engaging",0) >
            p["traits_norm"].get("concise",0)
        ) else "Audience"

        active = sum(1 for v in p["traits_norm"].values() if v>TRAIT_MIN)
        p["confidence"] = min(1.0, math.log(m+1)/4) * min(1.0, active/4)

        p["archetype"] = "Profile forming"
        if m >= ARCH_MIN_MSGS:
            for name,rule in ARCHETYPES:
                if rule(p["traits_norm"]):
                    p["archetype"] = name
                    break

        p["troll"] = (
            p["traits_norm"].get("combative",0) > 0.25
            or p["modifiers_norm"].get("curse",0) > 0.25
        )

    return profiles
