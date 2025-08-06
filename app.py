from flask import Flask, request, jsonify, session
import openai
import os
from elevenlabs.client import ElevenLabs
from uuid import uuid4

app = Flask(__name__)
app.secret_key = "supersecret"
openai.api_key = os.getenv("OPENAI_API_KEY")
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
voice_id = os.getenv("CHIP_VOICE_ID")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json()
        question = data.get("question", "Hello!")
        greeting = data.get("greeting", False)
        name = session.get("name", "User")
        role = "engineer"
        region = "NA"
        user_id = session.get("user_id", "chip")

        if greeting:
            response_text = question
        else:
            system_prompt = {
                "role": "system",
                "content": (
                    f"You are Chip, a virtual Pure Storage solution engineer. "
                    f"You are relatable, intelligent, and from Nebraska. "
                    f"Your job is to provide technical answers in 30 words or less. "
                    f"The user's name is {name}."
                ),
            }
            chat = openai.chat.completions.create(
                model="gpt-4o",
                messages=[system_prompt, {"role": "user", "content": question}],
                max_tokens=80
            )
            response_text = chat.choices[0].message.content

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
        print("🔥 Error in /ask:", str(e))
        return jsonify({"error": "Something went wrong."}), 500

if __name__ == "__main__":
    app.run(debug=True)
