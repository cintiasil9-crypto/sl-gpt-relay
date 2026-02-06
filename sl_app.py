from flask import Flask, Response
import os
from core import build_profiles

app = Flask(__name__)
FEED = os.environ["GOOGLE_PROFILES_FEED"]

@app.route("/list_profiles")
def list_profiles():
    profiles = build_profiles(FEED)
    ranked = sorted(profiles.values(), key=lambda p:p["messages"], reverse=True)

    out = ["📊 Social Profiles:"]
    for p in ranked[:5]:
        out.extend([
            "",
            f"🧠 {p['name']}",
            f"Confidence {int(p['confidence']*100)}%",
            f"🧩 {p['archetype']}"
        ])

        for t,v in sorted(p["traits_norm"].items(), key=lambda x:x[1], reverse=True)[:3]:
            out.append(f"• {t} ({int(v*100)}%)")

        mods = [k for k,v in p["modifiers_norm"].items() if v>0.1]
        if mods:
            out.append("Style: " + ", ".join(mods))

    return Response("\n".join(out), mimetype="text/plain")

@app.route("/")
def ok():
    return "OK"
