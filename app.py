print("✅ Chip app starting...")

import os
import json
import traceback
from flask import Flask, request, jsonify, render_template, redirect, session, url_for, Response, stream_with_context, g
from flask_session import Session
from elevenlabs.client import ElevenLabs
from memory import init_db, get_user, save_user, log_conversation
import openai
from uuid import uuid4
from werkzeug.utils import secure_filename
from datetime import datetime

def get_connection():
    import psycopg2
    import urllib.parse as up
    db_url = os.getenv("DATABASE_URL")

    if not db_url:
        raise ValueError("DATABASE_URL is not set in environment.")

    if "sslmode" not in db_url:
        db_url += "?sslmode=require"

    return psycopg2.connect(db_url)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecret")
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# 🔧 Auto-create conversations table if missing
def init_conversation_table():
    import os
    import psycopg2
    from memory import get_connection

    if 'DATABASE_URL' not in os.environ:
        raise ValueError("DATABASE_URL environment variable is not set. Use the internal connection string for Render-managed databases.")

    try:
        # Attempt to get a connection from memory module or fallback to psycopg2
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
    if not hasattr(g, "_db_initialized"):
        try:
            conn = get_connection()
            print("⏳ Lazy initializing DB...")
            init_db()
            print("✅ DB ready.")
            g._db_initialized = True
        except Exception as e:
            print("🔥 Failed to initialize DB:", e)
            traceback.print_exc()
            g._db_initialized = False
        finally:
            if 'conn' in locals() and not conn.closed:
                conn.close()
                print("✅ Database connection closed.")

def generate_chip_response(user_id, name, question, role, region):
    user = get_user(user_id)
    messages = user["messages"] if user else []
    messages.append({"role": "user", "content": question})
    messages = messages[-6:]

    system_prompt = {
        "role": "system",
        "content": (
            f"You are Chip, a virtual Pure Storage solution engineer. "
            f"You are relatable, intelligent, and from Nebraska. "
            f"You speak plainly and occasionally use dry humor and Nebraska sayings. "
            f"Your job is to provide technical answers, but with a humble and real personality. "
            f"Keep answers grounded in Pure Storage expertise. Use no more than 30 words. "
            f"The user's name is {name}."
        ),
    }

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[system_prompt] + messages,
        max_tokens=80
    )

    answer = response.choices[0].message.content
    save_user(user_id, json.dumps(messages), role, region, name)
    log_conversation(user_id, question, answer)
    return answer

@app.route("/3d")
def index_3d():
    return render_template("index_3d_v4.html")

@app.route("/login-basic", methods=["POST"])
def login_basic():
    try:
        conn = get_connection()
        data = request.get_json()
        login_name = data.get("login")
        if not (login_name.endswith("@purestorage.com") or login_name.endswith("@trace3.com")):
            return jsonify({"error": "Unauthorized domain"}), 403
        session["user_id"] = login_name

        user = get_user(login_name)
        if user:
            session["name"] = user.get("name", login_name)
            session["role"] = user.get("role", "engineer")
            session["region"] = user.get("region", "NA")
            return jsonify({
                "first_time": False,
                "name": user.get("name", ""),
                "title": user.get("role", "")
            })
        else:
            session["name"] = login_name
            session["role"] = "engineer"
            session["region"] = "NA"
            return jsonify({"first_time": True})

    except Exception as e:
        print("🔥 Login error:", str(e))
        return jsonify({"error": "Login failed"}), 500

@app.route("/profile", methods=["POST"])
def save_profile():
    try:
        conn = get_connection()
        user_id = session.get("user_id")
        data = request.get_json()
        name = data.get("name")
        title = data.get("title")

        messages = get_user(user_id)["messages"] if get_user(user_id) else []
        save_user(user_id, json.dumps(messages), title, "NA", name)
        session["name"] = name
        session["role"] = title
        return jsonify({"success": True})
    except Exception as e:
        print("🔥 Profile save error:", str(e))
        return jsonify({"error": "Save failed"}), 500

@app.route("/ask", methods=["POST"])
def ask():
    try:
        conn = get_connection()
        user_id = session.get("user_id") or request.remote_addr or str(uuid4())

        if request.is_json:
            data = request.get_json()
            question = data.get("question")
            name = session.get("name", data.get("name", "User"))
            role = data.get("role", "engineer")
            region = data.get("region", "NA")
        else:
            question = request.form.get("question")
            name = session.get("name", request.form.get("name", "User"))
            role = request.form.get("role", "engineer")
            region = request.form.get("region", "NA")

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
            conn = get_connection()
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

# <!-- PATCH: Persistent Memory + Dynamic Greeting | 2025-08-07 -->

# <!-- PATCH: Conversation History Retrieval | 2025-08-07 -->

@app.route("/history", methods=["POST"])
def retrieve_history():
    try:
        conn = get_connection()
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401

        query = request.json.get("query", "").strip()
        if not query:
            return jsonify({"error": "Missing query"}), 400

        user = get_user(user_id)
        if not user or not user.get("messages"):
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
        conn = get_connection()
        user_id = session.get("user_id")
        user = get_user(user_id) if user_id else None
        name = user.get("name", "there") if user else "there"

        data = request.get_json()
        prompt = data.get("prompt", f"Say hello to {name}.")

        openai_response = openai.chat.completions.create(
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
