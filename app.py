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

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecret")
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
voice_id = os.getenv("CHIP_VOICE_ID")

@app.before_request
def ensure_db_ready():
    if not hasattr(g, "_db_initialized"):
        try:
            print("⏳ Lazy initializing DB...")
            init_db()
            print("✅ DB ready.")
            g._db_initialized = True
        except Exception as e:
            print("🔥 Failed to initialize DB:", e)
            traceback.print_exc()
            g._db_initialized = False

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
        data = request.get_json()
        login_name = data.get("login")
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
            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            name = session.get("name", "User")
            role = "engineer"
            region = "NA"

            if "audio" not in request.files:
                yield b"--frame
Content-Type: application/json

" + json.dumps({"error": "No audio file uploaded."}).encode() + b"
"return

            audio_file = request.files["audio"]
            audio_file.filename = secure_filename(audio_file.filename)
            audio_path = f"/tmp/{uuid4().hex}.webm"
            audio_file.save(audio_path)

            client = openai.OpenAI(api_key=openai.api_key)
            with open(audio_path, "rb") as f:
                transcript = client.audio.transcriptions.create(model="whisper-1", file=f).text

            yield b"--frame
Content-Type: application/json

" + json.dumps({"transcript": transcript}).encode() + b"
"response_text = generate_chip_response(user_id, name, transcript, role, region)
            yield b"--frame
Content-Type: application/json

" + json.dumps({"response": response_text}).encode() + b"
"voice_settings = {"speed": 0.9}
            audio_stream = eleven.text_to_speech.convert(
                voice_id=voice_id,
                model_id="eleven_monolingual_v1",
                text=response_text,
                optimize_streaming_latency=1,
                voice_settings=voice_settings
            )

            yield b"--frame
Content-Type: audio/mpeg

"
            for chunk in audio_stream:
                yield chunk
            yield b"
--frame--
"

        except Exception as e:
            print("🔥 ERROR IN /ask-chip:", str(e))
            traceback.print_exc()
            yield b"--frame
Content-Type: application/json

" + json.dumps({"error": "Voice processing failed."}).encode() + b"
"return Response(stream_with_context(generate_stream()), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/greet")
def greet():
    try:
        text = "Hey there. I'm Chip — ready when you are."
        voice_settings = {"speed": 0.9}
        audio = eleven.text_to_speech.convert(
            voice_id=voice_id,
            model_id="eleven_monolingual_v1",
            text=text,
            optimize_streaming_latency=1,
            voice_settings=voice_settings
        )
        filename = f"static/audio/{uuid4().hex}.mp3"
        with open(filename, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        return jsonify({"reply": text, "audio": "/" + filename})
    except Exception as e:
        print("🔥 ERROR IN /greet:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Greeting failed"}), 500
