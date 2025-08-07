
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from flask_session import Session
from dotenv import load_dotenv
import os
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default_secret")
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Register Google OAuth blueprint
from auth.google import bp as google_bp
app.register_blueprint(google_bp, url_prefix="")

@app.route("/")
def index():
    return render_template("index_3d_v4.html")

@app.route("/transcribe", methods=["POST"])
def transcribe_audio():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio = request.files["audio"]
    filename = f"temp_{int(time.time())}.wav"
    filepath = os.path.join("/tmp", filename)
    audio.save(filepath)

    # Placeholder for Whisper or other transcription logic
    transcript = "Transcription logic not implemented."

    os.remove(filepath)
    return jsonify({"transcript": transcript})

if __name__ == "__main__":
    app.run(debug=True)
