print("✅ Chip app starting...")

# --- Patch eventlet BEFORE importing anything else that uses sockets/threads ---
import eventlet
eventlet.monkey_patch()

import os
import json
import traceback
from uuid import uuid4
from datetime import datetime

from flask import (
    Flask, request, jsonify, render_template, redirect, session,
    url_for, Response, stream_with_context, g, send_from_directory
)
from flask_session import Session
from werkzeug.utils import secure_filename

from elevenlabs.client import ElevenLabs
from memory import get_user, save_user, log_conversation, get_connection

# Modern OpenAI client
from openai import OpenAI

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecret")
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

voice_id = os.getenv("CHIP_VOICE_ID")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# Ensure audio output dir exists
os.makedirs("static/audio", exist_ok=True)

# Single, shared OpenAI client
oai = OpenAI()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _profile_complete(user: dict | None) -> bool:
    """
    A profile is 'complete' only if required fields exist and are non-empty.
    (We intentionally don't require messages.)
    """
    if not user or not isinstance(user, dict):
        return False
    name = (user.get("name") or "").strip()
    role = (user.get("role") or "").strip()
    region = (user.get("region") or "").strip()
    return all([name, role, region])

# -----------------------------------------------------------------------------
# DB helpers
# -----------------------------------------------------------------------------
def init_conversation_table():
    import psycopg2

    if 'DATABASE_URL' not in os.environ:
        raise ValueError("DATABASE_URL is not set.")

    try:
        conn = get_connection() or psycopg2.connect(os.environ['DATABASE_URL'])
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(100),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    question TEXT,
                    answer TEXT
                )
            """)
            conn.commit()
            print("✅ Conversation table verified.")
    except Exception as e:
        print("❌ Error creating conversation table:", e)
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals() and not conn.closed:
            conn.close()
            print("✅ Database connection closed.")

def ensure_db_ready():
    try:
        conn = get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        print("✅ DB connectivity OK")
    except Exception as e:
        print("🔥 DB connectivity check failed:", e)
        raise

# -----------------------------------------------------------------------------
# Core AI
# -----------------------------------------------------------------------------
def _build_system_prompt(name: str) -> dict:
    return {
        "role": "system",
        "content": (
            f"You are Chip, a virtual Pure Storage solution engineer. "
            f"You are relatable, intelligent, and from Nebraska. "
            f"You speak plainly and occasionally use dry humor and Nebraska sayings. "
            f"Your job is to provide technical answers, but with a humble and real personality. "
            f"Keep answers grounded in Pure Storage expertise. Use no more than 10 words. "
            f"The user's name is {name}."
        ),
    }

def generate_chip_response(user_id, name, question, role, region):
    # Load existing profile/messages
    existing = get_user(user_id) or {}
    messages = existing.get("messages", [])

    # Append new user turn and trim history
    messages.append({"role": "user", "content": question})
    messages = messages[-6:]

    # LLM call
    system_prompt = _build_system_prompt(name)
    response = oai.chat.completions.create(
        model="gpt-4o",
        messages=[system_prompt] + messages,
        max_tokens=80,
    )
    answer = response.choices[0].message.content

    # Persist profile with updated history (keep name/role/region in sync)
    messages.append({"role": "assistant", "content": answer})
    profile = {
        "name": existing.get("name", name),
        "role": existing.get("role", role),
        "region": existing.get("region", region),
        "messages": messages,
    }
    save_user(user_id, profile)

    # Log Q/A
    log_conversation(user_id, question, answer)
    return answer

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login-basic", methods=["POST"])
def login_basic():
    try:
        _ = get_connection()  # ping
        data = request.get_json() or {}
        login_name = (data.get("login") or "").strip().lower()

        if not (login_name.endswith("@purestorage.com") or login_name.endswith("@trace3.com")):
            return jsonify({"error": "Unauthorized domain"}), 403

        session["user_id"] = login_name

        user = get_user(login_name) or {}
        session["name"] = user.get("name", login_name)
        session["role"] = user.get("role", "engineer")
        session["region"] = user.get("region", "NA")

        return jsonify({
            "first_time": not _profile_complete(user),
            "name": user.get("name", ""),
            "title": user.get("role", "")
        })

    except Exception as e:
        print("🔥 Login error:", str(e))
        return jsonify({"error": "Login failed"}), 500

@app.route("/profile", methods=["POST"])
def save_profile_route():
    try:
        _ = get_connection()
        user_id = session.get("user_id")
        data = request.get_json() or {}
        name = (data.get("name") or "").strip() or user_id
        title = (data.get("title") or "").strip() or "engineer"
        region = (data.get("region") or "").strip() or "NA"

        existing = get_user(user_id) or {}
        messages = existing.get("messages", [])

        profile = {
            "name": name,
            "role": title,
            "region": region,
            "messages": messages
        }

        save_user(user_id, profile)
        session["name"] = name
        session["role"] = title
        session["region"] = region
        return jsonify({"success": True})

    except Exception as e:
        print("🔥 Profile save error:", str(e))
        return jsonify({"error": "Save failed"}), 500

@app.route("/ask", methods=["POST"])
def ask():
    try:
        _ = get_connection()
        user_id = session.get("user_id") or request.remote_addr or str(uuid4())

        if request.is_json:
            data = request.get_json() or {}
            question = data.get("question")
            name = session.get("name", data.get("name", "User"))
            role = data.get("role", session.get("role", "engineer"))
            region = data.get("region", session.get("region", "NA"))
        else:
            question = request.form.get("question")
            name = session.get("name", request.form.get("name", "User"))
            role = request.form.get("role", session.get("role", "engineer"))
            region = request.form.get("region", session.get("region", "NA"))

        if not question:
            return jsonify({"error": "Missing question."}), 400

        if request.is_json and data.get("greeting"):
            response_text = question
        else:
            response_text = generate_chip_response(user_id, name, question, role, region)

        voice_settings = {"speed": 0.9}
        audio = eleven.text_to_speech.convert(
            voice_id=voice_id,
            model_id="eleven_monolingual_v1",
            text=response_text,
            optimize_streaming_latency=1,
            voice_settings=voice_settings
        )

        filename = f"static/audio/{uuid4().hex}.mp3"
        with open(filename, "wb") as f:
            for chunk in audio:
                f.write(chunk)

        return jsonify({"response": response_text, "audio": "/" + filename})

    except Exception as e:
        print("🔥 ERROR IN /ask:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Something went wrong. Try again later."}), 500

@app.route("/ask-chip", methods=["POST"])
def ask_chip():
    def generate_stream():
        try:
            _ = get_connection()
            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            name = session.get("name", "User")
            role = session.get("role", "engineer")
            region = session.get("region", "NA")

            if "audio" not in request.files:
                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "No audio file uploaded."}).encode() + b"\r\n"
                return

            audio_file = request.files["audio"]
            audio_file.filename = secure_filename(audio_file.filename)
            audio_path = f"/tmp/{uuid4().hex}.webm"
            audio_file.save(audio_path)

            # Whisper transcription via standardized client
            with open(audio_path, "rb") as f:
                transcript = oai.audio.transcriptions.create(
                    model="whisper-1",
                    file=f
                ).text

            yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"transcript": transcript}).encode() + b"\r\n"

            response_text = generate_chip_response(user_id, name, transcript, role, region)
            yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"response": response_text}).encode() + b"\r\n"

            voice_settings = {"speed": 0.9}
            audio_stream = eleven.text_to_speech.convert(
                voice_id=voice_id,
                model_id="eleven_monolingual_v1",
                text=response_text,
                optimize_streaming_latency=1,
                voice_settings=voice_settings
            )

            yield b"--frame\r\nContent-Type: audio/mpeg\r\n\r\n"
            for chunk in audio_stream:
                yield chunk
            yield b"\r\n--frame--\r\n"

        except Exception as e:
            print("🔥 ERROR IN /ask-chip:", str(e))
            traceback.print_exc()
            yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "Voice processing failed."}).encode() + b"\r\n"

    return Response(stream_with_context(generate_stream()), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/history", methods=["POST"])
def retrieve_history():
    try:
        _ = get_connection()
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401

        query = (request.json or {}).get("query", "").strip()
        if not query:
            return jsonify({"error": "Missing query"}), 400

        user = get_user(user_id) or {}
        if not user.get("messages"):
            return jsonify({"response": "I don’t have any past conversations to look at yet."})

        past_dialogue = user["messages"][-12:]
        flat_history = "\n".join([f"{m['role']}: {m['content']}" for m in past_dialogue])

        prompt = f"""
You are Chip, a helpful Pure Storage AI. The user asked a question that references past conversations.

Conversation history:
{flat_history}

Current user query: "{query}"

If something in the history matches what the user is referring to, summarize or clarify the key detail.
If not, say you couldn't find it.
"""

        response = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=150
        )
        return jsonify({"response": response.choices[0].message.content.strip()})
    except Exception as e:
        print("🔥 ERROR IN /history:", str(e))
        traceback.print_exc()
        return jsonify({"error": "History lookup failed"}), 500

@app.route("/greet", methods=["POST"])
def greet():
    try:
        _ = get_connection()
        user_id = session.get("user_id")
        user = get_user(user_id) if user_id else None
        name = user.get("name", "there") if user else "there"

        data = request.get_json() or {}
        prompt = data.get("prompt", f"Say hello to {name}.")

        openai_response = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=60
        )
        greeting_text = openai_response.choices[0].message.content.strip()

        voice_settings = {"speed": 0.9}
        audio = eleven.text_to_speech.convert(
            voice_id=voice_id,
            model_id="eleven_monolingual_v1",
            text=greeting_text,
            optimize_streaming_latency=1,
            voice_settings=voice_settings
        )
        filename = f"static/audio/{uuid4().hex}.mp3"
        with open(filename, "wb") as f:
            for chunk in audio:
                f.write(chunk)

        return jsonify({"reply": greeting_text, "audio": "/" + filename})
    except Exception as e:
        print("🔥 ERROR IN /greet:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Greeting failed"}), 500

@app.route("/auth/status", methods=["GET"])
def auth_status():
    """Lightweight session check so the frontend can decide whether to show the login or profile prompt."""
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"authenticated": False})

        user = get_user(user_id) or {}
        complete = _profile_complete(user)

        return jsonify({
            "authenticated": True,
            "user_id": user_id,
            "name": (user.get("name") if user else user_id) or user_id,
            "role": (user.get("role") if user else "engineer"),
            "region": (user.get("region") if user else "NA"),
            "first_time": not complete,            # <-- only true if profile incomplete
            "profile_complete": complete           # extra flag if the frontend wants it
        })
    except Exception as e:
        print("🔥 ERROR IN /auth/status:", str(e))
        return jsonify({"authenticated": False, "error": "status check failed"}), 500

@app.route("/logout", methods=["POST"])
def logout():
    """Clear session and return ok; frontend can redirect to landing screen and show login prompt."""
    try:
        session.clear()
        return jsonify({"ok": True})
    except Exception as e:
        print("🔥 ERROR IN /logout:", str(e))
        return jsonify({"ok": False}), 500

@app.route("/healthz/db", methods=["GET"])
def healthz_db():
    try:
        ensure_db_ready()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/login", methods=["POST"])
def login():
    # Accepts JSON {"email": "..."} and sets session. Mirrors /login-basic.
    try:
        _ = get_connection()
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()

        if not email or not (email.endswith("@purestorage.com") or email.endswith("@trace3.com")):
            return jsonify({"error": "Unauthorized domain"}), 403

        session["user_id"] = email

        user = get_user(email) or {}
        session["name"] = user.get("name", email)
        session["role"] = user.get("role", "engineer")
        session["region"] = user.get("region", "NA")

        return jsonify({
            "first_time": not _profile_complete(user),
            "name": user.get("name", ""),
            "title": user.get("role", "")
        })

    except Exception as e:
        print("🔥 /login error:", str(e))
        return jsonify({"error": "Login failed"}), 500

# -----------------------------------------------------------------------------
# API aliases expected by the frontend (prevents 404s like /api/me and /api/login)
# -----------------------------------------------------------------------------
@app.route("/api/me", methods=["GET"])
def api_me():
    return auth_status()

@app.route("/api/login", methods=["POST"])
def api_login():
    return login()

@app.route("/api/logout", methods=["POST"])
def api_logout():
    return logout()
