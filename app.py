from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from elevenlabs import ElevenLabs
import os

app = Flask(__name__, static_folder="static", template_folder="templates")

# Load API keys from environment
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]

# Initialize clients
openai_client = OpenAI(api_key=OPENAI_API_KEY)
voice_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask_chip():
    data = request.get_json()
    question = data.get("question", "")

    gpt_response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are Chip Tracewell, a witty, Midwestern technical expert who explains Pure Storage topics clearly and with a touch of humor."},
            {"role": "user", "content": question}
        ]
    )

    chip_text = gpt_response.choices[0].message.content.strip()

    # Generate audio with latest ElevenLabs SDK
    audio = voice_client.text_to_speech.convert(
        voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel's default voice ID
        model_id="eleven_multilingual_v2",
        text=chip_text,
        output_format="mp3_44100_128"
    )

    audio_path = "static/audio/chip_output.mp3"
    with open(audio_path, "wb") as f:
        f.write(audio)

    return jsonify({
        "text": chip_text,
        "audio_url": "/static/audio/chip_output.mp3"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
