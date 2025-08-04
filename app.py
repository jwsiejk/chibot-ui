from flask import Flask, render_template, request, jsonify
import openai
import os
from memory import init_db, save_user, get_user, log_conversation
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

init_db()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/3d")
def index_3d():
    return render_template("index_3d.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_id = request.remote_addr
    user_input = data.get("message")

    if not user_input:
        return jsonify({"error": "Empty message received."}), 400

    save_user(user_id)
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are Chip, a helpful AI expert in Pure Storage."},
            {"role": "user", "content": user_input}
        ]
    )

    chip_reply = response.choices[0].message.content.strip()
    log_conversation(user_id, user_input, chip_reply)

    return jsonify({"reply": chip_reply})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
