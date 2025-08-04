import os
from flask import Flask, request, jsonify, render_template
from elevenlabs import generate_speech, save, Voice, VoiceSettings, set_api_key
from memory import init_db, get_user, save_user, log_conversation
import openai
from uuid import uuid4

app = Flask(__name__)

# Set API keys from environment variables
openai.api_key = os.getenv("OPENAI_API_KEY")
set_api_key(os.getenv("ELEVENLABS_API_KEY"))

# Initialize database
init_db()

def generate_chip_response(user_id, question):
    user = get_user(user_id)
    messages = user["messages"] if user else []
    messages.append({"role": "user", "content": question})

    system_prompt = {
        "role": "system",
        "content": (
            "You are Chip, a virtual Pure Storage solution engineer. "
            "You are relatable, intelligent, and from Nebraska. "
            "You speak plainly and occasionally use dry humor and Nebraska sayings. "
            "Your job is to provide technical answers, but with a humble and real personality. "
            "Keep answers grounded in Pure Storage expertise."
        ),
    }

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[system_prompt] + messages,
        max_tokens=300
    )

    answer = response.choices[0].message.content
    save_user(user_id, messages)
    log_conversation(user_id, question, answer)
    return answer

def generate_audio(response_text):
    voice = Voice(
        voice_id=os.getenv("CHIP_VOICE_ID"),
        settings=VoiceSettings(stability=0.4, similarity_boost=0.8)
    )

    audio = generate_speech(
        text=response_text,
        voice=voice,
        model="eleven_monolingual_v1"
    )

    filename = f"static/audio/{uuid4().hex}.mp3"
    save(audio, filename)
    return filename

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/3d")
def index_3d():
    return render_template("index_3d.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_id = request.remote_addr or str(uuid4())
        question = request.form.get("question")

        response_text = generate_chip_response(user_id, question)
        audio_path = generate_audio(response_text)

        return jsonify({"response": response_text, "audio": audio_path})

    except Exception as e:
        print("Error:", e)
        return jsonify({"error": "Something went wrong. Try again later."}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=3000)
