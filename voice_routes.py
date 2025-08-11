from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from werkzeug.utils import secure_filename
from uuid import uuid4
import json, os, re, base64

def create_voice_blueprint(deps):
    """
    deps keys used:
      - oai
      - eleven
      - voice_id
      - TTS_ENABLED
      - generate_chip_response
      - find_account_row
      - repo_search
    """
    oai             = deps["oai"]
    eleven          = deps["eleven"]
    voice_id        = deps["voice_id"]
    TTS_ENABLED     = deps["TTS_ENABLED"]
    gen_reply       = deps["generate_chip_response"]
    find_account    = deps.get("find_account_row")
    repo_search     = deps.get("repo_search")

    bp = Blueprint("voice_bp", __name__)

    # ----------------- Shared helpers -----------------
    END_PAT = re.compile(
        r"\b("
        r"(that'?s\s+)?all( for now)?"
        r"|stop( here)?"
        r"|end( chat| conversation)?"
        r"|thanks( a lot| so much)?"
        r"|thank you"
        r"|bye|goodbye|that'?ll be it|we'?re done|finished|i'?m done"
        r")\b",
        re.I
    )
    DOC_NEED_PAT = re.compile(r"\b(deck|slides?|doc|document|pdf|presentation|pptx?)\b", re.I)
    PRESENT_PAT  = re.compile(r"\b(show|open|bring up|present|display)\b", re.I)
    OWNER_PAT    = re.compile(r"(who\s+(is\s+)?(the\s+)?(account\s+)?owner\s+(for|of)\s+)(?P<acct>.+)", re.I)
    TEAM_PAT     = re.compile(r"(who\s+(is\s+)?(the\s+)?account\s+team\s+(for|of)\s+)(?P<acct>.+)", re.I)

    def is_end_intent(msg: str) -> bool:
        return bool(msg and END_PAT.search(msg))

    def clean_account_name(raw: str) -> str:
        import re as _re
        s = (raw or "").strip().rstrip("?!.")
        s = _re.sub(r"^(the\s+|\"|')+", "", s, flags=_re.I).strip(' "\'')
        return s

    def build_suggestions(message: str, reply_text: str, top_hit: dict | None) -> list[str]:
        sugs: list[str] = []
        if top_hit:
            title = top_hit.get("title") or "that doc"
            if not PRESENT_PAT.search(message or ""):
                sugs.append(f"Open {title}")
            sugs.append("Summarize this for me")
            sugs.append("Send me the download")
        else:
            sugs.extend(["Give me a quick example", "Explain a bit more"])
        if OWNER_PAT.search(message or "") or TEAM_PAT.search(message or ""):
            sugs.append("Email the owner")
            sugs.append("Show account team again")
        sugs = sugs[:3]
        sugs.append("End chat")
        return sugs

    def maybe_make_tts(text: str) -> str | None:
        if not (TTS_ENABLED and text and voice_id and eleven):
            return None
        try:
            voice_settings = {"speed": 0.9}
            audio_stream = eleven.text_to_speech.convert(
                voice_id=voice_id,
                model_id="eleven_monolingual_v1",
                text=text,
                optimize_streaming_latency=1,
                voice_settings=voice_settings
            )
            audio_bytes = b"".join(chunk for chunk in audio_stream)
            return base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as e:
            print("⚠️ TTS failed:", e)
            return None

    # -------------------------------------------------------------------------
    # A) Streaming endpoint (kept for compatibility with your earlier code)
    # -------------------------------------------------------------------------
    @bp.route("/ask-chip", methods=["POST"])
    def ask_chip():
        def generate_stream():
            try:
                user_id = session.get("user_id") or request.remote_addr or str(uuid4())
                name = session.get("name", "User")
                role = session.get("role", "engineer")
                region = session.get("region", "NA")
                if "audio" not in request.files:
                    yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"error": "No audio file uploaded."}).encode() + b"\r\n"
                    return
                audio_file = request.files["audio"]
                audio_file.filename = secure_filename(audio_file.filename)
                audio_path = f"/tmp/{uuid4().hex}.webm"
                audio_file.save(audio_path)

                # Transcribe
                with open(audio_path, "rb") as f:
                    transcript = oai.audio.transcriptions.create(model="whisper-1", file=f).text

                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"transcript": transcript}).encode() + b"\r\n"

                # End intent short-circuit
                if is_end_intent(transcript):
                    farewell = "Anytime. I’ll be right here when you need me."
                    yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"response": farewell, "end": True}).encode() + b"\r\n"
                    if TTS_ENABLED:
                        voice_settings = {"speed": 0.9}
                        audio_stream = eleven.text_to_speech.convert(
                            voice_id=voice_id, model_id="eleven_monolingual_v1",
                            text=farewell, optimize_streaming_latency=1, voice_settings=voice_settings
                        )
                        yield b"--frame\r\nContent-Type: audio/mpeg\r\n\r\n"
                        for chunk in audio_stream:
                            yield chunk
                        yield b"\r\n--frame--\r\n"
                    return

                # Generate reply
                response_text = gen_reply(user_id, name, transcript, role, region)
                yield b"--frame\r\nContent-Type: application/json\r\n\r\n" + json.dumps({"response": response_text, "end": False}).encode() + b"\r\n"

                # TTS
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

    # -------------------------------------------------------------------------
    # B) Simple one-shot JSON endpoint (recommended for the web UI)
    #     POST form-data: audio=<webm/mp4/wav>
    #     Returns: { transcript, reply_text, audio_b64, visemes, actions, suggestions, end }
    # -------------------------------------------------------------------------
    @bp.post("/api/voice-once")
    def api_voice_once():
        try:
            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            name    = session.get("name", "User")
            role    = session.get("role", "engineer")
            region  = session.get("region", "NA")

            if "audio" not in request.files:
                return jsonify({"error": "No audio file uploaded."}), 400

            f = request.files["audio"]
            fname = secure_filename(f.filename or f"clip-{uuid4().hex}.webm")
            tmp_path = os.path.join("/tmp", fname)
            f.save(tmp_path)

            # Transcribe with Whisper
            with open(tmp_path, "rb") as fh:
                transcript = oai.audio.transcriptions.create(model="whisper-1", file=fh).text

            # End intent?
            if is_end_intent(transcript):
                farewell = "Anytime. I’ll be right here when you need me."
                audio_b64 = maybe_make_tts(farewell)
                return jsonify({
                    "transcript": transcript,
                    "reply_text": farewell,
                    "audio_b64": audio_b64,
                    "visemes": [],
                    "actions": [],
                    "suggestions": ["Start over", "No suggestions", "End chat"],
                    "end": True
                })

            # Generate reply (persona + logging inside)
            reply_text = gen_reply(user_id, name, transcript, role, region)

            actions = []
            top = None

            # Account intents
            owner_q = OWNER_PAT.search(transcript or "")
            team_q  = TEAM_PAT.search(transcript or "")
            if (owner_q or team_q) and callable(find_account):
                acct = clean_account_name((owner_q or team_q).group("acct"))
                row = find_account(acct)
                if row:
                    acct_name   = row.get("account_name") or "that account"
                    owner       = row.get("owner") or "Unknown"
                    owner_email = row.get("owner_email") or ""
                    manager     = row.get("manager") or ""
                    pam         = row.get("pam") or ""
                    if owner_q:
                        reply_text = f"{acct_name}: {owner}" + (f" ({owner_email})" if owner_email else "")
                        if owner_email:
                            actions.append({"type": "open_url", "title": "Email owner", "url": f"mailto:{owner_email}"})
                    elif team_q:
                        parts = [f"Owner: {owner}" + (f" ({owner_email})" if owner_email else "")]
                        if manager: parts.append(f"Manager: {manager}")
                        if pam:     parts.append(f"PAM: {pam}")
                        reply_text = f"{acct_name} team — " + "; ".join(parts)

            # Doc retrieval
            need_doc = bool(DOC_NEED_PAT.search(transcript or "")) or bool(PRESENT_PAT.search(transcript or ""))
            if need_doc and callable(repo_search):
                hits = repo_search(transcript, limit=5)
                if hits:
                    top = hits[0]
                    actions.append({
                        "type": "download",
                        "title": top["title"],
                        "url": f"/repo/file/{top['id']}",
                        "filename": top["filename"]
                    })
                    actions.append({
                        "type": "open_url",
                        "title": "Present now",
                        "url": f"/repo/view/{top['id']}"
                    })
                    snippet = top.get("snippet")
                    if snippet and isinstance(reply_text, str) and len(reply_text) < 220:
                        reply_text = f"{reply_text}\n\n{snippet}"

            # TTS
            audio_b64 = maybe_make_tts(reply_text)
            suggestions = build_suggestions(transcript, reply_text, top)

            return jsonify({
                "transcript": transcript,
                "reply_text": reply_text,
                "audio_b64": audio_b64,
                "visemes": [],
                "actions": actions,
                "suggestions": suggestions,
                "end": False
            })
        except Exception as e:
            import traceback as _tb
            print("🔥 /api/voice-once failed:", _tb.format_exc())
            return jsonify({"error": "voice failed"}), 500

    # -------------------------------------------------------------------------
    # C) Dynamic speech endpoint (unchanged)
    # -------------------------------------------------------------------------
    @bp.post("/api/speak")
    def api_speak():
        try:
            data = request.get_json(silent=True) or {}
            prompt = (data.get("prompt") or "").strip() or "Howdy. Ready when you are."
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

    # -------------------------------------------------------------------------
    # D) Greet (unchanged) — returns TTS mp3 URL
    # -------------------------------------------------------------------------
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
