print("✅ Chip app starting...")

import os
import json
import traceback
from flask import Flask, request, jsonify, render_template, redirect, session, url_for
from flask_session import Session
from elevenlabs.client import ElevenLabs
from memory import init_db, get_user, save_user, log_conversation
import openai
from uuid import uuid4

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecret")
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Initialize API clients
openai.api_key = os.getenv("OPENAI_API_KEY")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
voice_id = os.getenv("CHIP_VOICE_ID")

# Initialize memory database
init_db()

def generate_chip_response(user_id, name, question, role, region):
    user = get_user(user_id)
    messages = user["messages"] if user else []
    messages.append({"role": "user", "content": question})

    system_prompt = {
        "role": "system",
        "content": (
            f"You are Chip, a virtual Pure Storage solution engineer. "
            f"You are relatable, intelligent, and from Nebraska. "
            f"You speak plainly and occasionally use dry humor and Nebraska sayings. "
            f"Your job is to provide technical answers, but with a humble and real personality. "
            f"Keep answers grounded in Pure Storage expertise. Use no more than 60 words. "
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

def generate_audio(response_text):
    if not voice_id:
        raise ValueError("CHIP_VOICE_ID environment variable is missing.")

    voice_settings = {
        "speed": 0.9  # Slightly slower than default
    }

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
    return filename

@app.route("/")
def index():
    user_id = session.get("user_id") or request.remote_addr
    name = session.get("name")
    user = get_user(user_id)
    if not name or not user or not user.get("role") or not user.get("region"):
        return redirect("/login")
    return render_template("index.html")

@app.route("/3d")
def index_3d():
    user_id = session.get("user_id") or request.remote_addr
    name = session.get("name")
    user = get_user(user_id)
    if not name or not user or not user.get("role") or not user.get("region"):
        return redirect("/login")
    return render_template("index_3d.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_id = session.get("user_id") or request.remote_addr or str(uuid4())
        name = session.get("name", request.form.get("name", "User"))
        role = request.form.get("role", "engineer")
        region = request.form.get("region", "NA")
        question = request.form.get("question")

        print("🔹 Question received:", question)
        print("🧑 Name:", name)
        print("🔸 Role:", role)
        print("🔸 Region:", region)

        response_text = generate_chip_response(user_id, name, question, role, region)
        audio_path = generate_audio(response_text)

        return jsonify({"response": response_text, "audio": audio_path})
    except Exception as e:
        print("🔥 ERROR IN /ask:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Something went wrong. Try again later."}), 500

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name")
        session["user_id"] = request.remote_addr
        session["name"] = name
        return redirect("/profile")
    return '''
        <h3>Please complete your profile to continue.</h3>
        <form method="POST">
            <input name="name" placeholder="Enter your name" required />
            <button type="submit">Login</button>
        </form>
    '''

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/profile", methods=["GET", "POST"])
def profile():
    user_id = session.get("user_id") or request.remote_addr
    if not session.get("name"):
        return redirect("/login")

    if request.method == "POST":
        name = request.form.get("name")
        role = request.form.get("role")
        region = request.form.get("region")
        messages = get_user(user_id)["messages"] if get_user(user_id) else []
        session["name"] = name
        save_user(user_id, json.dumps(messages), role, region, name)
        return redirect("/3d")

    user = get_user(user_id) or {"name": session.get("name", "User"), "role": "engineer", "region": "NA"}
    return render_template("profile.html", user=user)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=3000)
