import os
from flask import Flask, request, session, jsonify, render_template, url_for, Response
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from services import db as dbsvc
from services import llm as llmsvc
from services import tts as ttssvc

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=False)

try:
    dbsvc.init_db()
except Exception:
    pass

def current_user_email():
    return session.get("email")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Valid email required"}), 400
    session["email"] = email
    try:
        if not dbsvc.get_user(email):
            dbsvc.save_user(email=email, name=None, title=None, region=None, profile=None)
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("email", None)
    return jsonify({"ok": True})

@app.route("/api/me", methods=["GET"])
def api_me():
    email = current_user_email()
    if not email:
        return jsonify({"ok": True, "logged_in": False})
    user = dbsvc.get_user(email) or {"email": email}
    profile_complete = bool(user.get("name"))
    return jsonify({"ok": True, "logged_in": True, "profile_complete": profile_complete, "user": user})

@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "GET":
        if not current_user_email():
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        user = dbsvc.get_user(current_user_email()) or {"email": current_user_email()}
        return jsonify({"ok": True, "user": user})
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    title = data.get("title")
    region = data.get("region")
    profile = data.get("profile")
    dbsvc.save_user(email=current_user_email(), name=name, title=title, region=region, profile=profile)
    user = dbsvc.get_user(current_user_email())
    return jsonify({"ok": True, "user": user})

@app.route("/api/greet", methods=["GET"])
def api_greet():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    user = dbsvc.get_user(current_user_email()) or {}
    name = user.get("name") or "there"
    text = f"Hey {name}! I'm Chip. When you're ready, ask me anything about Pure Storage or your lab setup."
    audio_rel = "chip/audio/greeting-static.mp3"
    audio_fs = os.path.join(app.static_folder, audio_rel)
    audio_url = url_for("static", filename=audio_rel) if os.path.exists(audio_fs) else None
    return jsonify({"ok": True, "text": text, "audioUrl": audio_url})

def system_prompt():
    return (
        "You are Chip, a virtual systems engineer for Pure Storage. "
        "Speak in 1–2 crisp sentences. Be practical, calm, and specific. "
        "Never exceed 30 words. Avoid marketing fluff."
    )

@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required"}), 400
    messages = [{"role":"system","content":system_prompt()},{"role":"user","content":prompt}]
    reply = llmsvc.reply(messages) or llmsvc.fallback(prompt)
    try:
        dbsvc.log_conversation(email=current_user_email(), role="user", message=prompt)
        dbsvc.log_conversation(email=current_user_email(), role="assistant", message=reply)
    except Exception:
        pass
    return jsonify({"ok": True, "reply": reply})

@app.route("/api/tts", methods=["POST"])
def api_tts():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400
    text = llmsvc.cap_30_words(text)
    audio, err = ttssvc.synthesize_tts_bytes(text)
    if not audio:
        return jsonify({"ok": False, "error": err or "TTS unavailable"}), 503
    return Response(audio, mimetype="audio/mpeg")

@app.route("/api/tts_with_visemes", methods=["POST"])
def api_tts_with_visemes():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text required"}), 400
    text = llmsvc.cap_30_words(text)
    payload, err = ttssvc.tts_with_visemes(text)
    if err and payload.get("audio") is None:
        return jsonify({"ok": True, "fallback": True, "visemes": payload.get("visemes"), "relative": True})
    return jsonify({"ok": True, **payload})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
