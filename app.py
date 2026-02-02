from flask import Flask, request, jsonify
from openai import OpenAI
import os
import time
import math
import random
import requests
import json
import re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")
PROFILE_BUILD_KEY = os.environ.get("PROFILE_BUILD_KEY")

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {"data": {}, "ts": 0}
CACHE_TTL = 300  # seconds

# =================================================
# GVIZ PARSER (THIS IS THE IMPORTANT PART)
# =================================================

def fetch_gviz_rows(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()

    text = r.text.strip()

    # Must be Google Visualization JS, not JSON
    if not text.startswith("google.visualization"):
        raise ValueError("Invalid gviz response")

    # Strip JS wrapper
    match = re.search(r"setResponse\((.*)\);?$", text, re.S)
    if not match:
        raise ValueError("GVIZ payload not found")

    payload = json.loads(match.group(1))
    table = payload.get("table", {})

    # Use column IDs (labels are often empty)
    cols = [(c.get("id") or c.get("label")) for c in table.get("cols", [])]

    rows = []
    for r in table.get("rows", []):
        row = {}
        for i, cell in enumerate(r.get("c", [])):
            row[cols[i]] = cell.get("v") if cell else None
        rows.append(row)

    return rows

# =================================================
# TIME DECAY
# =================================================

def decay_weight(ts):
    try:
        age_days = (time.time() - int(ts)) / 86400
        if age_days <= 1:
            return 1.0
        elif age_days <= 7:
            return 0.6
        return 0.3
    except:
        return 0.0

# =================================================
# PROFILE ENGINE (NO CRASH VERSION)
# =================================================

@app.route("/build_profiles", methods=["POST"])
def build_profiles():

    # ---- AUTH ----
    if request.headers.get("X-Profile-Key") != PROFILE_BUILD_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    now = time.time()

    # ---- CACHE ----
    if PROFILE_CACHE["data"] and now - PROFILE_CACHE["ts"] < CACHE_TTL:
        return jsonify(PROFILE_CACHE["data"])

    if not GOOGLE_PROFILES_FEED:
        return jsonify({"error": "Profiles feed missing"}), 500

    try:
        rows = fetch_gviz_rows(GOOGLE_PROFILES_FEED)
    except Exception as e:
        # IMPORTANT: return JSON, not crash
        return jsonify({"error": str(e)}), 500

    profiles = {}

    # ---------- Aggregate ----------
    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            ts = int(float(r["timestamp_utc"]))
        except:
            continue

        weight = decay_weight(ts)
        msgs = max(float(r.get("messages", 0)), 1.0) * weight

        p = profiles.setdefault(uuid, {
            "display_name": r.get("display_name", "Unknown"),
            "t": {
                "messages": 0.0,
                "curious": 0.0,
                "dominant": 0.0,
                "supportive": 0.0,
                "humor": 0.0,
                "caps": 0.0
            }
        })

        t = p["t"]
        t["messages"] += msgs
        t["curious"] += float(r.get("kw_curious", 0)) * weight
        t["dominant"] += float(r.get("kw_dominant", 0)) * weight
        t["supportive"] += float(r.get("kw_supportive", 0)) * weight
        t["humor"] += float(r.get("kw_humor", 0)) * weight
        t["caps"] += float(r.get("caps", 0)) * weight

    # ---------- Build Results ----------
    results = {}

    for uuid, p in profiles.items():
        m = p["t"]["messages"]
        if m < 5:
            continue

        traits = {
            "curious": p["t"]["curious"] / m,
            "dominant": (p["t"]["dominant"] + p["t"]["caps"]) / (2 * m),
            "supportive": p["t"]["supportive"] / m,
            "humorous": p["t"]["humor"] / m
        }

        results[uuid] = {
            "display_name": p["display_name"],
            "confidence": round(min(1.0, math.log(m + 1) / 5), 2),
            "top_traits": sorted(
                [{"trait": k, "score": round(v, 2)} for k, v in traits.items()],
                key=lambda x: x["score"],
                reverse=True
            )
        }

    PROFILE_CACHE["data"] = results
    PROFILE_CACHE["ts"] = now

    return jsonify(results)

# =================================================
# HEALTH
# =================================================

@app.route("/")
def ok():
    return "OK"
