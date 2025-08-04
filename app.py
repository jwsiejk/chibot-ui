import os
from flask import Flask, request, jsonify, render_template
from elevenlabs import generate, save, Voice, VoiceSettings, set_api_key
import openai
from dotenv import load_dotenv

load_dotenv()

# Initialize Flask app
app = Flask(__name__, static_folder="static")

# Load API keys from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")

# Set ElevenLabs API key
set_api_key(ELEVEN_API_KEY)

# Configure OpenAI client using new v1 syntax
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Chip's personality prompt
chip_system_prompt = """
You are Chip Tracewell, a slightly awkward but brilliant virtual solutions engineer from Nebraska. 
You’re highly technical, humble, occasionally funny without realizing it, and focused on Pure Storage and related technologies. 
Always answer like Chip, in his voice.
"""

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/3d")
def home_3d():
    return render_template("index3d.html")

@app.route("/ask", methods=["POST"])
def ask_chip():
    try:
        user_question = request.json.get("question")

        if not user_question:
            return jsonify({"error": "Missing question"}), 400

        # Generate Chip's response from OpenAI
        chat_response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": chip_system_prompt},
                {"role": "user", "content": user_question}
            ]
        )

        chip_reply = chat_response.choices[0].message.content.strip()

        # Generate audio with ElevenLabs
        audio = generate(
            text=chip_reply,
            voice=Voice(
                voice_id="EXAVITQu4vr4xnSDxMaL",  # Replace with your Chip voice ID
                settings=VoiceSettings(
                    stability=0.45,
                    similarity_boost=0.75,
                    style=0.35,
                    use_speaker_boost=True
                )
            ),
            model="eleven_monolingual_v1"
        )

        output_path = os.path.join("static", "output.wav")
        save(audio, output_path)

        return jsonify({
            "answer": chip_reply,
            "audio_url": "/static/output.wav"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
