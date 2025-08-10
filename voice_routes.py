from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from werkzeug.utils import secure_filename
from uuid import uuid4
import json

def create_voice_blueprint(deps):
    """
    deps keys used:
      - oai
      - eleven
      - voice_id
      - TTS_ENABLED
      - generate_chip_response
    """
    oai             = deps["oai"]
    eleven          = deps["eleven"]
    voice_id        = deps["voice_id"]
    TTS_ENABLED     = deps["TTS_ENABLED"]
    gen_reply       = deps["generate_chip_response"]

    bp = Blueprint("voice_bp", __name__)

    # ----------- Streaming / voice path -----------
    @bp.route("/ask-chip", methods=["POST"])
    def ask_chip():
        def generate_stream():
            try:
                user_id = session.get("user_id") or request.remote_addr or str(uuid4())
                name = session.get("name", "User")
                role = "engineer"
                region = "NA"
                if "audio" not in request.files:
                    yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "No audio file uploaded."}).encode() + b"\r\n"
                    return
                audio_file = request.files["audio"]
                audio_file.filename = secure_filename(audio_file.filename)
                audio_path = f"/tmp/{uuid4().hex}.webm"
                audio_file.save(audio_path)

                # --- Transcribe with new client ---
                with open(audio_path, "rb") as f:
                    transcript = oai.audio.transcriptions.create(model="whisper-1", file=f).text

                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"transcript": transcript}).encode() + b"\r\n"
                response_text = gen_reply(user_id, name, transcript, role, region)
                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"response": response_text}).encode() + b"\r\n"
                if TTS_ENABLED and response_text:
                    voice_settings = {"speed": 0.9}
                    audio_stream = eleven.text_to_speech.convert(
                        voice_id=voice_id,
                        model_id="eleven_monolingual_v1",
                        text=response_text,
                        optimize_streaming_latency=1,
                        voice_settings=voice_settings
                    )
                    yield b"--frame\r\nContent-Type: audio/mpeg\r\n\r\n"
                    for chunk in audio_stream:
                        yield chunk
                    yield b"\r\n--frame--\r\n"
            except Exception as e:
                print("🔥 ERROR IN /ask-chip:", str(e))
                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "Voice processing failed."}).encode() + b"\r\n"
        return Response(stream_with_context(generate_stream()), mimetype="multipart/x-mixed-replace; boundary=frame")

    # ---------- Dynamic speech endpoint ----------
    @bp.post("/api/speak")
    def api_speak():
        """
        Expects: { "prompt": "text the user asked Chip to say" }
        Returns: { "audio_url": "/static/audio/<file>.mp3", "visemes": [] }
        """
        try:
            data = request.get_json(silent=True) or {}
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                prompt = "Howdy. Ready when you are."

            if not TTS_ENABLED:
                return jsonify({"audio_url": None, "visemes": [], "disabled": True}), 200

            if not voice_id or not eleven:
                return jsonify({"error": "TTS not configured"}), 503

            voice_settings = {"speed": 0.9}
            audio = eleven.text_to_speech.convert(
                voice_id=voice_id,
                model_id="eleven_monolingual_v1",
                text=prompt,
                optimize_streaming_latency=1,
                voice_settings=voice_settings
            )
            out_path = f"static/audio/{uuid4().hex}.mp3"
            with open(out_path, "wb") as f:
                for chunk in audio:
                    f.write(chunk)
            return jsonify({"audio_url": "/" + out_path, "visemes": []}), 200
        except Exception as e:
            print("⚠️ /api/speak failed:", e)
            return jsonify({"error": "speak failed"}), 500

    # ---------- Greet (kept with voice so TTS is local) ----------
    @bp.post("/greet")
    def greet():
        try:
            user_id = session.get("user_id")
            from memory import get_user as _get_user  # lazy import to avoid cycles
            user = _get_user(user_id) if user_id else None
            name = user.get("name", "there") if user else "there"
            data = request.get_json() or {}
            prompt = data.get("prompt", f"Say hello to {name}.")

            try:
                openai_response = oai.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": prompt}],
                    max_tokens=60
                )
                greeting_text = openai_response.choices[0].message.content.strip()
            except Exception as e:
                greeting_text = "Howdy. Ready when you are."

            audio_url = None
            if TTS_ENABLED:
                voice_settings = {"speed": 0.9}
                audio = eleven.text_to_speech.convert(
                    voice_id=voice_id,
                    model_id="eleven_monolingual_v1",
                    text=greeting_text,
                    optimize_streaming_latency=1,
                    voice_settings=voice_settings
                )
                filename = f"static/audio/{uuid4().hex}.mp3"
                with open(filename, "wb") as f:
                    for chunk in audio:
                        f.write(chunk)
                audio_url = "/" + filename
            return jsonify({"reply": greeting_text, "audio": audio_url})
        except Exception as e:
            print("🔥 ERROR IN /greet:", str(e))
            return jsonify({"error": "Greeting failed"}), 500

    return bp

