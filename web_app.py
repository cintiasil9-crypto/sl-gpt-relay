from flask import Flask, jsonify
import os
from core import build_profiles

app = Flask(__name__)
FEED = os.environ["GOOGLE_PROFILES_FEED"]

@app.route("/leaderboard")
def leaderboard():
    profiles = build_profiles(FEED)

    ranked = sorted(
        profiles.values(),
        key=lambda p:(p["reputation"],p["confidence"]),
        reverse=True
    )

    out=[]
    for i,p in enumerate(ranked,1):
        out.append({
            "rank": i,
            "name": p["name"],
            "confidence": int(p["confidence"]*100),
            "reputation": int(p["reputation"]*100),
            "role": p["role"],
            "archetype": p["archetype"],
            "traits": {k:int(v*100) for k,v in p["traits_norm"].items()},
            "modifiers": {k:int(v*100) for k,v in p["modifiers_norm"].items()},
            "troll": p["troll"]
        })

    return jsonify(out)

@app.route("/")
def ok():
    return "OK"
