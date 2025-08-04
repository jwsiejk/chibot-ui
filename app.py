import os
from flask import Flask, request, jsonify, render_template
from elevenlabs.client import ElevenLabs
from memory import init_db, get_user, save_user, log_conversation
import openai
from uuid import uuid4
import traceback

app = Flask(__name__)

# Initialize API clients
openai.api_key = os.getenv("OPENAI_API_KEY")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
voice_id = os.getenv("CHIP_VOICE_ID")

# Initialize memory database
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
    if not voice_id:
        raise ValueError("CHIP_VOICE_ID environment variable is missing.")

    audio = eleven.text_to_speech.convert(
        voice_id=voice_id,
        model_id="eleven_monolingual_v1",
        text=response_text,
        optimize_streaming_latency=1
    )

    filename = f"static/audio/{uuid4().hex}.mp3"
    with open(filename, "wb") as f:
        f.write(audio)
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
        print("🔹 Question received:", question)

        response_text = generate_chip_response(user_id, question)
        print("✅ OpenAI response:", response_text)

        audio_path = generate_audio(response_text)
        print("✅ Audio saved at:", audio_path)

        return jsonify({"response": response_text, "audio": audio_path})
    except Exception as e:
        print("🔥 ERROR IN /ask:", str(e))
        traceback.print_exc()
        return jsonify({"error": "Something went wrong. Try again later."}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=3000)
