import os
import json
from datetime import datetime
from flask import Flask, request, session, jsonify, render_template, url_for, Response
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import memory
from services.llm_service import generate_reply
from services.tts_service import tts_bytes, tts_with_visemes
from services.email_service import send_email
from services.accounts_service import search_accounts
from services.accounts_service import find_by_account, team_for_account, headers as accounts_headers, reload as reload_accounts

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)

try:
    memory.init_db()
except Exception:
    pass

def current_user_email():
    return session.get("email")

@app.route("/")
def index():
    return render_template("index.html", build=os.getenv("APP_BUILD","1"))

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Valid email required"}), 400
    session["email"] = email
    try:
        user = memory.get_user(email) or {}
        if not user:
            memory.save_user(email=email, name=None, title=None, region=None, profile=None)
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
    user = memory.get_user(email) or {"email": email}
    profile_complete = bool(user.get("name"))
    return jsonify({"ok": True, "logged_in": True, "profile_complete": profile_complete, "user": user})

@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "GET":
        if not current_user_email():
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        user = memory.get_user(current_user_email()) or {"email": current_user_email()}
        return jsonify({"ok": True, "user": user})
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    title = data.get("title")
    region = data.get("region")
    profile = data.get("profile")
    memory.save_user(email=current_user_email(), name=name, title=title, region=region, profile=profile)
    user = memory.get_user(current_user_email())
    return jsonify({"ok": True, "user": user})

@app.route("/api/greet", methods=["GET"])
def api_greet():
    email = current_user_email()
    if not email:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    user = memory.get_user(email) or {}
    name = user.get("name") or "there"
    text = f"Hey {name}! I'm Chip. When you're ready, ask me anything about Pure Storage or your lab setup."
    audio_rel = "chip/audio/greeting-static.mp3"
    audio_fs = os.path.join(app.static_folder, "chip", "audio", "greeting-static.mp3")
    audio_url = url_for("static", filename=audio_rel) if os.path.exists(audio_fs) else None
    return jsonify({"ok": True, "text": text, "audioUrl": audio_url})

def cap_30_words(s: str) -> str:
    words = (s or "").split()
    return " ".join(words[:30])

@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    user_ctx = memory.get_user(current_user_email()) or {}
    recent = memory.get_recent_messages(current_user_email(), limit=8)

    reply = generate_reply(prompt, user_ctx=user_ctx, recent=recent)
    reply = cap_30_words(reply)

    try:
        memory.log_conversation(email=current_user_email(), role="user", message=prompt)
        memory.log_conversation(email=current_user_email(), role="assistant", message=reply)
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
    text = cap_30_words(text)
    audio, err = tts_bytes(text)
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
    text = cap_30_words(text)
    payload, err = tts_with_visemes(text)
    return jsonify({"ok": True, **payload})

@app.route("/api/email/test", methods=["POST"])
def api_email_test():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    to_addr = data.get("to") or current_user_email()
    ok, err = send_email(
        to_addr=to_addr,
        subject="Ask Chip Test Email",
        text="This is a test email from Ask Chip. If you received it, SMTP is configured correctly."
    )
    if not ok:
        return jsonify({"ok": False, "error": err or "Send failed"}), 500
    return jsonify({"ok": True})

@app.route("/api/accounts/lookup", methods=["GET"])
def api_accounts_lookup():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    q = (request.args.get("q") or "").strip()
    rows = find_by_account(q, max_rows=10)
    return jsonify({"ok": True, "rows": rows})

@app.route("/api/accounts/team", methods=["GET"])
def api_accounts_team():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    q = (request.args.get("q") or "").strip()
    info = team_for_account(q)
    return jsonify({"ok": True, "team": info})

@app.route("/api/accounts/reload", methods=["POST"])
def api_accounts_reload():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    n = reload_accounts()
    return jsonify({"ok": True, "rows": n})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)

@app.route("/api/email/send", methods=["POST"])
def api_email_send():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    to_addr = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    html = data.get("html")
    if not to_addr:
        return jsonify({"ok": False, "error": "Recipient required"}), 400
    ok, err = send_email(to_addr, subject, body, html)
    if not ok:
        return jsonify({"ok": False, "error": err or "Send failed"}), 502
    try:
        memory.log_conversation(email=current_user_email(), role="assistant", message=f"[Email sent to {to_addr}] {subject}")
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/accounts/search", methods=["GET"])
def api_accounts_search():
    if not current_user_email():
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    q = (request.args.get("q") or "").strip()
    results = search_accounts(q, limit=25) if q else []
    return jsonify({"ok": True, "results": results})
