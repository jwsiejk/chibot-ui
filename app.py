print("✅ Chip app starting...")

# --- Normalize DATABASE_URL before importing anything that might use it ---
import os
_raw_db = (os.getenv("DATABASE_URL") or "").strip()
if (_raw_db.startswith('"') and _raw_db.endswith('"')) or (_raw_db.startswith("'") and _raw_db.endswith("'")):
    _raw_db = _raw_db[1:-1].strip()
os.environ["DATABASE_URL"] = _raw_db.replace("\n", "").replace("\r", "").replace(" ", "")

# ---------------- standard imports ----------------
import json
import traceback
import re
from uuid import uuid4
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, Response, stream_with_context
from flask_session import Session
from werkzeug.utils import secure_filename
from elevenlabs.client import ElevenLabs
import openai
import psycopg2.extras as extras  # for RealDictCursor

# only import memory after DATABASE_URL is sanitized
from memory import get_user, save_user, log_conversation, get_connection

# -----------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("FLASK_SECRET") or "supersecret"
app.config["SESSION_TYPE"] = "filesystem"
app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
if os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes"):
    app.config["SESSION_COOKIE_SECURE"] = True
Session(app)

voice_id = os.getenv("CHIP_VOICE_ID")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
# NEW: global kill-switch for audio (set TTS_ENABLED=false in Render env to disable)
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------- helpers ----------
def init_conversation_table():
    import psycopg2
    if 'DATABASE_URL' not in os.environ:
        raise ValueError("DATABASE_URL environment variable is not set.")
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

def _merge_profile_fields(row: dict):
    """Return merged (email, name, title) from columns or JSONB profile; plus completeness."""
    if not row:
        return "", "", "", False
    prof = row.get("profile") or {}
    email = (row.get("email") or prof.get("email") or "").strip().lower()
    name  = (row.get("name")  or prof.get("name")  or "").strip()
    title = (row.get("title") or prof.get("title") or "").strip()
    complete = bool(email and name and title)
    return email, name, title, complete

# ---------- chip core ----------
def generate_chip_response(user_id, name, question, role, region):
    # Be tolerant: get_user may not contain messages; default to empty
    user = get_user(user_id) or {}
    messages = user.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    messages.append({"role": "user", "content": question})
    messages = messages[-6:]

    system_prompt = {
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

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[system_prompt] + messages,
        max_tokens=80
    )

    answer = response.choices[0].message.content
    try:
        save_user(user_id, {"name": name, "title": role, "messages": messages})
    except Exception as e:
        print("⚠️ save_user (messages) failed:", e)
    log_conversation(user_id, question, answer)
    return answer

# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login-basic", methods=["POST"])
def login_basic():
    try:
        data = request.get_json()
        login_name = data.get("login","").strip().lower()
        if not (login_name.endswith("@purestorage.com") or login_name.endswith("@trace3.com")):
            return jsonify({"error": "Unauthorized domain"}), 403
        session["user_id"] = login_name
        user = get_user(login_name)
        if user:
            session["name"] = user.get("name", login_name)
            session["role"] = user.get("role", "engineer")
            session["region"] = user.get("region", "NA")
            return jsonify({"first_time": False, "name": user.get("name",""), "title": user.get("role","")})
        else:
            session["name"] = login_name
            session["role"] = "engineer"
            session["region"] = "NA"
            return jsonify({"first_time": True})
    except Exception as e:
        print("🔥 Login error:", str(e))
        return jsonify({"error": "Login failed"}), 500

# --- API aliases so the frontend can call /api/login and /api/logout ---
@app.post("/api/login")
def api_login_alias():
    # forward to the primary login endpoint (expects {"email": "..."} )
    return login()

@app.post("/api/logout")
def api_logout_alias():
    return logout()

# Legacy profile save
@app.route("/profile", methods=["POST"])
def save_profile_legacy():
    try:
        user_id = session.get("user_id")
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        title = (data.get("title") or "").strip()
        existing = get_user(user_id) or {}
        messages = existing.get("messages", []) if isinstance(existing, dict) else []
        save_user(user_id, {"name": name, "title": title, "messages": messages})
        session["name"] = name or user_id
        session["role"] = title or session.get("role", "engineer")
        return jsonify({"success": True})
    except Exception as e:
        print("🔥 Profile save error:", str(e))
        return jsonify({"error": "Save failed"}), 500

# ----------- ASK: now respects Static/Dynamic via `speak` flag -----------
@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_id = session.get("user_id") or request.remote_addr or str(uuid4())
        data = request.get_json() or {}
        question = (data.get("question") or "").strip()
        speak = bool(data.get("speak", False))  # <— Static=false, Dynamic=true

        if not question:
            return jsonify({"error": "Missing question."}), 400

        name   = session.get("name", "User")
        role   = "engineer"
        region = "NA"

        response_text = generate_chip_response(user_id, name, question, role, region)

        audio_url = None
        if TTS_ENABLED and speak and response_text:
            try:
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
                audio_url = "/" + filename
            except Exception as e:
                print("⚠️ TTS generation failed:", e)

        return jsonify({"response": response_text, "audio": audio_url})
    except Exception as e:
        print("🔥 ERROR IN /ask:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Something went wrong. Try again later."}), 500

# (Streaming / voice path left as-is)
@app.route("/ask-chip", methods=["POST"])
def ask_chip():
    def generate_stream():
        try:
            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            name = session.get("name", "User")
            role = "engineer"
            region = "NA"
            if "audio" not in request.files:
                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "No audio file uploaded."}).encode() + b"\r\n"
                return
            audio_file = request.files["audio"]
            audio_file.filename = secure_filename(audio_file.filename)
            audio_path = f"/tmp/{uuid4().hex}.webm"
            audio_file.save(audio_path)
            client = openai.OpenAI(api_key=openai.api_key)
            with open(audio_path, "rb") as f:
                transcript = client.audio.transcriptions.create(model="whisper-1", file=f).text
            yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"transcript": transcript}).encode() + b"\r\n"
            response_text = generate_chip_response(user_id, name, transcript, role, region)
            yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"response": response_text}).encode() + b"\r\n"
            if TTS_ENABLED and response_text:
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

# ---------- Other endpoints (auth/profile/history) ----------
@app.route("/history", methods=["POST"])
def retrieve_history():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        user = get_user(user_id) or {}
        past_dialogue = (user.get("messages") or [])[-12:]
        if not past_dialogue:
            return jsonify({"response": "I don’t have any past conversations to look at yet."})
        flat_history = "\n".join([f"{m['role']}: {m['content']}" for m in past_dialogue])
        prompt = f"""
You are Chip, a helpful Pure Storage AI. The user asked a question that references past conversations.

Conversation history:
{flat_history}

Current user query: "What did we talk about last time?"

If something in the history matches what the user is referring to, summarize or clarify the key detail.
If not, say you couldn't find it.
"""
        response = openai.chat.completions.create(
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
        user_id = session.get("user_id")
        user = get_user(user_id) if user_id else None
        name = user.get("name", "there") if user else "there"
        data = request.get_json() or {}
        prompt = data.get("prompt", f"Say hello to {name}.")
        openai_response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=60
        )
        greeting_text = openai_response.choices[0].message.content.strip()
        audio_url = None
        if TTS_ENABLED:
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
            audio_url = "/" + filename
        return jsonify({"reply": greeting_text, "audio": audio_url})
    except Exception as e:
        print("🔥 ERROR IN /greet:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Greeting failed"}), 500

@app.route("/auth/status", methods=["GET"])
def auth_status():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"authenticated": False})
        user = get_user(user_id) or {}
        return jsonify({
            "authenticated": True,
            "user_id": user_id,
            "name": (user.get("name") if user else user_id) or user_id,
            "role": (user.get("role") if user else "engineer"),
            "region": (user.get("region") if user else "NA"),
            "first_time": False if user else True
        })
    except Exception as e:
        print("🔥 ERROR IN /auth/status:", str(e))
        return jsonify({"authenticated": False, "error": "status check failed"}), 500

@app.route("/logout", methods=["POST"])
def logout():
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
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        if not email or not (email.endswith("@purestorage.com") or email.endswith("@trace3.com")):
            return jsonify({"error": "Unauthorized domain"}), 403
        session["user_id"] = email
        user = get_user(email)
        if user:
            session["name"] = user.get("name", email)
            session["role"] = user.get("role", "engineer")
            session["region"] = user.get("region", "NA")
            return jsonify({"first_time": False, "name": user.get("name",""), "title": user.get("role","")})
        else:
            session["name"] = email
            session["role"] = "engineer"
            session["region"] = "NA"
            return jsonify({"first_time": True})
    except Exception as e:
        print("🔥 /login error:", str(e))
        return jsonify({"error": "Login failed"}), 500

@app.get("/api/me")
def api_me():
    try:
        user = session.get("user_id")
        if not user:
            return jsonify({"error": "unauthenticated"}), 401
        email = (user or "").strip().lower()
        with get_connection() as conn, conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT email, name, title, profile
                FROM public.users
                WHERE email = %s
            """, (email,))
            row = cur.fetchone()
        email_m, name_m, title_m, complete = _merge_profile_fields(row or {"email": email})
        return jsonify({
            "email": email_m or email,
            "name": name_m,
            "title": title_m,
            "profileComplete": bool(complete)
        }), 200
    except Exception:
        app.logger.exception("/api/me crashed")
        return jsonify({"error": "temporary"}), 200

@app.get("/api/profile")
def api_profile_get():
    try:
        email = session.get("user_id")
        if not email:
            return jsonify({"error": "unauthenticated"}), 401
        with get_connection() as conn, conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute("SELECT email, name, title, profile FROM public.users WHERE email = %s", (email,))
            row = cur.fetchone()
        email_m, name_m, title_m, complete = _merge_profile_fields(row or {"email": email})
        return jsonify({
            "exists": bool(row),
            "profile": {"email": email_m, "name": name_m, "title": title_m},
            "profileComplete": bool(complete)
        }), 200
    except Exception:
        app.logger.exception("/api/profile GET failed")
        return jsonify({"error": "profile lookup failed"}), 500

@app.post("/api/profile")
def api_profile_post():
    try:
        email = (session.get("user_id") or "").strip().lower()
        if not email:
            return jsonify({"error": "unauthenticated"}), 401
        data = request.get_json(force=True) or {}
        name  = (data.get("name")  or "").strip()
        title = (data.get("title") or "").strip()
        if not name or not title:
            return jsonify({"error": "name and title are required"}), 400
        row = save_user(email, {"name": name, "title": title})
        complete = bool(row.get("email") and row.get("name") and row.get("title"))
        session["name"]  = name or email
        session["role"]  = title or session.get("role", "engineer")
        return jsonify({"ok": True, "profileComplete": complete, "user": row}), 200
    except Exception:
        app.logger.exception("/api/profile POST failed")
        return jsonify({"error": "profile save failed"}), 500
