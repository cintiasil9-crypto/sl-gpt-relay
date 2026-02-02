from flask import Flask, request, jsonify
import os
import time
import math
import requests
import json
import re

# =================================================
# APP SETUP
# =================================================

app = Flask(__name__)

GOOGLE_PROFILES_FEED = os.environ.get("GOOGLE_PROFILES_FEED")
PROFILE_BUILD_KEY = os.environ.get("PROFILE_BUILD_KEY")

# =================================================
# CACHE
# =================================================

PROFILE_CACHE = {"data": {}, "ts": 0}
CACHE_TTL = 300

# =================================================
# GVIZ FETCH (NO CRASH VERSION)
# =================================================

def fetch_gviz_debug(url):
    """
    Fetches Google GVIZ and returns diagnostics instead of crashing.
    """
    try:
        r = requests.get(url, timeout=20)
        text = r.text.strip()

        return {
            "http_status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "starts_with": text[:60],
            "raw_preview": text[:300],
            "is_gviz": text.startswith("google.visualization"),
        }
    except Exception as e:
        return {
            "error": str(e)
        }

def parse_gviz(text):
    """
    Strict parser. Raises ValueError on failure.
    """
    match = re.search(r"setResponse\((.*)\)\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError("GVIZ wrapper not found")

    payload = json.loads(match.group(1))
    table = payload.get("table", {})
    cols = [(c.get("id") or c.get("label")) for c in table.get("cols", [])]

    rows = []
    for r in table.get("rows", []):
        row = {}
        for i, cell in enumerate(r.get("c", [])):
            row[cols[i]] = cell.get("v") if cell else None
        rows.append(row)

    return rows

# =================================================
# TEST ENDPOINT (THIS IS THE KEY)
# =================================================

@app.route("/test_gviz", methods=["GET"])
def test_gviz():
    if not GOOGLE_PROFILES_FEED:
        return jsonify({"error": "GOOGLE_PROFILES_FEED missing"}), 500

    info = fetch_gviz_debug(GOOGLE_PROFILES_FEED)
    return jsonify(info)

# =================================================
# PROFILE ENGINE (SAFE MODE)
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

    # ---- FETCH ----
    try:
        r = requests.get(GOOGLE_PROFILES_FEED, timeout=20)
        text = r.text.strip()
    except Exception as e:
        return jsonify({"error": "Fetch failed", "detail": str(e)}), 500

    # ---- VERIFY GVIZ ----
    if not text.startswith("google.visualization"):
        return jsonify({
            "error": "Not GVIZ",
            "preview": text[:200]
        }), 500

    # ---- PARSE ----
    try:
        rows = parse_gviz(text)
    except Exception as e:
        return jsonify({
            "error": "GVIZ parse failed",
            "detail": str(e)
        }), 500

    profiles = {}

    for r in rows:
        try:
            uuid = r["avatar_uuid"]
            ts = int(float(r["timestamp_utc"]))
        except Exception:
            continue

        msgs = max(float(r.get("messages", 0)), 1.0)

        p = profiles.setdefault(uuid, {
            "display_name": r.get("display_name", "Unknown"),
            "messages": 0.0
        })

        p["messages"] += msgs

    results = {}

    for uuid, p in profiles.items():
        if p["messages"] < 5:
            continue

        results[uuid] = {
            "display_name": p["display_name"],
            "confidence": round(min(1.0, math.log(p["messages"] + 1) / 5), 2),
            "top_traits": []
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
