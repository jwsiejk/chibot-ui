from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from elevenlabs import ElevenLabs
import os
from memory import init_db, get_user, save_user, log_conversation

app = Flask(__name__, static_folder="static", template_folder="templates")

# Load API keys from environment
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]

# Initialize clients
openai_client = OpenAI(api_key=OPENAI_API_KEY)
voice_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Initialize Chip's memory database
init_db()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask_chip():
    data = request.get_json()
    question = data.get("question", "")
    user = data.get("user", {})
    user_id = user.get("email", "anonymous")
    name = user.get("name", "")
    role = user.get("role", "")
    region = user.get("region", "")

    # Save or update user memory
    save_user(user_id, name, role, region)
    user_record = get_user(user_id)

    # Inject memory into the prompt if available
    context_note = f"User: {name}, Role: {role}, Region: {region}. " if user_record else ""

    gpt_response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are Chip Tracewell, a witty, funny without trying technical expert from Nebraska who always ties answers back to Pure Storage.\n"
                    f"Respond as if you know this person. {context_note}\n"
                    f"If the question is off-topic, redirect playfully. Stay concise and limit your response to 40 words max."
                )
            },
            {"role": "user", "content": question}
        ]
    )

    chip_text = gpt_response.choices[0].message.content.strip()

    audio_stream = voice_client.text_to_speech.stream(
        voice_id="MIAWBMadvHL0ek6oJEXD",  # Chip's custom voice
        model_id="eleven_multilingual_v2",
        text=chip_text,
        output_format="mp3_44100_128"
    )

    audio_path = "static/audio/chip_output.mp3"
    with open(audio_path, "wb") as f:
        for chunk in audio_stream:
            f.write(chunk)

    # Log the conversation
    log_conversation(user_id, question, chip_text)

    return jsonify({
        "text": chip_text,
        "audio_url": "/static/audio/chip_output.mp3"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
