from flask import Flask, jsonify
import os
from core import build_profiles

app = Flask(__name__)

FEED = os.environ.get("GOOGLE_PROFILES_FEED")

@app.route("/leaderboard")
def leaderboard():
    if not FEED:
        return jsonify({"error": "GOOGLE_PROFILES_FEED not set"}), 500

    profiles = build_profiles(FEED)

    ranked = sorted(
        profiles.values(),
        key=lambda p: (p["reputation"], p["confidence"]),
        reverse=True
    )

    out = []
    for i, p in enumerate(ranked, start=1):
        out.append({
            "rank": i,
            "name": p["name"],

            "confidence": int(p["confidence"] * 100),
            "reputation": int(p["reputation"] * 100),
            "gravity": int(p.get("gravity", 0) * 100),

            "role": p["role"],
            "archetype": p["archetype"],

            "traits": {
                k: int(v * 100)
                for k, v in p["traits_norm"].items()
            },

            "modifiers": {
                k: int(v * 100)
                for k, v in p["modifiers_norm"].items()
            },

            "troll": bool(p["troll"])
        })

    response = jsonify(out)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

@app.route("/")
def ok():
    return "OK"
