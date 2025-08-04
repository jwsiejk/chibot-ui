
import os
from flask import Flask, request, render_template, jsonify
from dotenv import load_dotenv
import openai
import requests

load_dotenv()
app = Flask(__name__)

# Set your API keys from environment variables
openai.api_key = os.getenv("OPENAI_API_KEY")
elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")

def generate_chip_response(prompt):
    messages = [
        {
            "role": "system",
            "content": (
                "You are Chip, a Virtual Solutions Engineer from Nebraska. "
                "You’re smart, humble, unintentionally funny, and extremely knowledgeable about Pure Storage. "
                "You speak in a down-to-earth way, using clear analogies and practical insight. "
                "If someone asks something you don't know, be honest about it but offer to help find out. "
                "Always relate answers back to Pure Storage and their value."
            )
        },
        {"role": "user", "content": prompt}
    ]
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
        temperature=0.7
    )
    return response.choices[0].message["content"].strip()

def synthesize_speech(text):
    url = "https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL"
    headers = {
        "xi-api-key": elevenlabs_api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8
        }
    }
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        audio_path = os.path.join("static/audio", "chip_response.mp3")
        with open(audio_path, "wb") as f:
            f.write(response.content)
        return audio_path
    else:
        print("ElevenLabs error:", response.text)
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/3d")
def index_3d():
    return render_template("index_3d.html")

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400

    chip_reply = generate_chip_response(prompt)
    audio_path = synthesize_speech(chip_reply)

    return jsonify({
        "response": chip_reply,
        "audio": audio_path
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
