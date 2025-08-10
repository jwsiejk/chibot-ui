from flask import Blueprint, request, jsonify, session
import re
import base64
from uuid import uuid4

def create_chat_blueprint(deps):
    """
    deps keys used:
      - oai (unused directly here; generate_chip_response uses it)
      - eleven
      - voice_id
      - TTS_ENABLED
      - generate_chip_response
      - find_account_row
      - repo_search
    """
    eleven         = deps["eleven"]
    voice_id       = deps["voice_id"]
    TTS_ENABLED    = deps["TTS_ENABLED"]
    gen_reply      = deps["generate_chip_response"]
    find_account   = deps["find_account_row"]
    repo_search    = deps["repo_search"]

    bp = Blueprint("chat_bp", __name__)

    # ----------- ASK (text + optional one-shot TTS mp3) -----------
    @bp.route("/ask", methods=["POST"])
    def ask():
        try:
            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            data = request.get_json() or {}
            question = (data.get("question") or "").strip()
            speak = bool(data.get("speak", False))  # Static=false, Dynamic=true

            if not question:
                return jsonify({"error": "Missing question."}), 400

            name   = session.get("name", "User")
            role   = "engineer"
            region = "NA"

            response_text = gen_reply(user_id, name, question, role, region)

            audio_url = None
            if TTS_ENABLED and speak and response_text:
                try:
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
                    audio_url = "/" + filename
                except Exception as e:
                    print("⚠️ TTS generation failed:", e)

            return jsonify({"response": response_text, "audio": audio_url})
        except Exception as e:
            print("🔥 ERROR IN /ask:", str(e))
            return jsonify({"error": "Something went wrong. Try again later."}), 500

    # ----------- CHAT (Text or Live TTS) -----------
    @bp.post("/chat")
    def chat():
        """
        Accepts: { "message": "...", "lane": "text" | "live" }
        Returns:
          {
            "reply_text": "...",
            "audio_b64": "...." (only when lane==live and TTS enabled),
            "visemes": [],      (placeholder for ElevenLabs viseme timestamps),
            "actions": [ {type, title, url, filename} ]
          }
        """
        try:
            data = request.get_json(force=True) or {}
            message = (data.get("message") or "").strip()
            lane = (data.get("lane") or "live").lower()
            if not message:
                return jsonify({"error": "Missing message"}), 400

            user_id = session.get("user_id") or request.remote_addr or str(uuid4())
            name    = session.get("name", "there")
            role    = session.get("role", "engineer")
            region  = session.get("region", "NA")

            # LLM reply (reuse persona + logging)
            reply_text = gen_reply(user_id, name, message, role, region)

            # --- Account Q&A intents ---
            owner_q = re.search(r"(who\s+(is\s+)?(the\s+)?(account\s+)?owner\s+(for|of)\s+)(?P<acct>.+)", message, re.I)
            team_q  = re.search(r"(who\s+(is\s+)?(the\s+)?account\s+team\s+(for|of)\s+)(?P<acct>.+)", message, re.I)

            actions = []
            if owner_q or team_q:
                acct = (owner_q or team_q).group("acct").strip().rstrip("?!.")
                acct = re.sub(r"^(the\s+|\"|')+", "", acct, flags=re.I).strip(' "\'')
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
                            actions.append({
                                "type": "open_url",
                                "title": "Email owner",
                                "url": f"mailto:{owner_email}"
                            })
                    elif team_q:
                        parts = [f"Owner: {owner}" + (f" ({owner_email})" if owner_email else "")]
                        if manager: parts.append(f"Manager: {manager}")
                        if pam:     parts.append(f"PAM: {pam}")
                        reply_text = f"{acct_name} team — " + "; ".join(parts)

            # --------- Doc intent & retrieval (present or download) ----------
            present_intent = bool(re.search(r"\b(show|open|bring up|present|display)\b", message, re.I))
            need_doc = bool(re.search(r"\b(deck|slides?|doc|document|pdf|download|send|share|presentation|pptx?)\b", message, re.I))

            snippet = None
            top = None
            if need_doc or present_intent:
                hits = repo_search(message, limit=5)
                if hits:
                    top = hits[0]
                    snippet = top.get("snippet")
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

            if snippet and isinstance(reply_text, str) and len(reply_text) < 220:
                reply_text = f"{reply_text}\n\n{snippet}"

            # TTS if lane == live
            audio_b64 = None
            visemes = None
            if lane == "live" and TTS_ENABLED and reply_text:
                try:
                    voice_settings = {"speed": 0.9}
                    audio_stream = eleven.text_to_speech.convert(
                        voice_id=voice_id,
                        model_id="eleven_monolingual_v1",
                        text=reply_text,
                        optimize_streaming_latency=1,
                        voice_settings=voice_settings
                    )
                    audio_bytes = b"".join(chunk for chunk in audio_stream)
                    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                    visemes = []  # populate when switching to viseme_timestamps endpoint
                except Exception as e:
                    print("⚠️ Chat TTS failed:", e)

            return jsonify({
                "reply_text": reply_text,
                "audio_b64": audio_b64,
                "visemes": visemes,
                "actions": actions
            })
        except Exception:
            import traceback as _tb
            print(_tb.format_exc())
            return jsonify({"error": "chat failed"}), 500

    return bp

