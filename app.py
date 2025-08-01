from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from elevenlabs import ElevenLabs
import os

app = Flask(__name__, static_folder="static", template_folder="templates")

# Load API keys from environment
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]

# Initialize OpenAI and ElevenLabs clients
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
            {"role": "system", "content": "You are Chip Tracewell, a witty, down to earth, technical expert from Nebraska who everyone loves with a touch of humor and always ties asnwers back to Pure Storage.  If the answer is off topic you always tie it back to Pure Storage, be consise and limit your response to 40 words),
                        {"role": "user", "content": question}
        ]
    )

    chip_text = gpt_response.choices[0].message.content.strip()

    audio_stream = voice_client.text_to_speech.stream(
        voice_id="MIAWBMadvHL0ek6oJEXD",  # Chip's voice
        model_id="eleven_multilingual_v2",
        text=chip_text,
        output_format="mp3_44100_128"
    )

    audio_path = "static/audio/chip_output.mp3"
    with open(audio_path, "wb") as f:
        for chunk in audio_stream:
            f.write(chunk)

    return jsonify({
        "text": chip_text,
        "audio_url": "/static/audio/chip_output.mp3"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
