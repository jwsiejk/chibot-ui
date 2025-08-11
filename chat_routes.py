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

    # ----------------- Helpers -----------------
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

    OWNER_PAT = re.compile(r"(who\s+(is\s+)?(the\s+)?(account\s+)?owner\s+(for|of)\s+)(?P<acct>.+)", re.I)
    TEAM_PAT  = re.compile(r"(who\s+(is\s+)?(the\s+)?account\s+team\s+(for|of)\s+)(?P<acct>.+)", re.I)

    def is_end_intent(msg: str) -> bool:
        if not msg:
            return False
        return bool(END_PAT.search(msg))

    def clean_account_name(raw: str) -> str:
        s = (raw or "").strip().rstrip("?!.")
        s = re.sub(r"^(the\s+|\"|')+", "", s, flags=re.I).strip(' "\'')
        return s

    def build_suggestions(message: str, reply_text: str, top_hit: dict | None) -> list[str]:
        """
        Provide 2–4 small follow-ups that feel helpful and human.
        Always include 'End chat' as the final safety exit.
        """
        sugs: list[str] = []

        # If we presented/found a doc, offer natural continuations
        if top_hit:
            title = top_hit.get("title") or "that doc"
            if not PRESENT_PAT.search(message or ""):
                sugs.append(f"Open {title}")
            sugs.append("Summarize this for me")
            sugs.append("Send me the download")
        else:
            # General-purpose follow-ups to keep flow natural
            sugs.extend([
                "Give me a quick example",
                "Explain a bit more",
            ])

        # If user asked about an account owner/team, offer next steps
        if OWNER_PAT.search(message or "") or TEAM_PAT.search(message or ""):
            sugs.append("Email the owner")
            sugs.append("Show account team again")

        # Keep list short and purposeful
        sugs = sugs[:3]

        # Always add a graceful exit
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

    # ----------------- /ask (one-shot) -----------------
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
            role   = session.get("role", "engineer")
            region = session.get("region", "NA")

            response_text = gen_reply(user_id, name, question, role, region)

            audio_b64 = None
            if speak:
                audio_b64 = maybe_make_tts(response_text)

            return jsonify({
                "response": response_text,
                "audio_b64": audio_b64,
                "visemes": [],
                "actions": [],
                "suggestions": build_suggestions(question, response_text, None),
                "end": False
            })
        except Exception as e:
            print("🔥 ERROR IN /ask:", str(e))
            return jsonify({"error": "Something went wrong. Try again later."}), 500

    # ----------------- /chat (conversational) -----------------
    @bp.post("/chat")
    def chat():
        """
        Accepts: { "message": "...", "lane": "text" | "live" }
        Returns:
          {
            "reply_text": "...",
            "audio_b64": "...." (when lane==live and TTS enabled),
            "visemes": [],      (placeholder for future ElevenLabs viseme timestamps),
            "actions": [ {type, title, url, filename} ],
            "suggestions": ["...", "...", "End chat"],
            "end": false
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

            # ---- End intent (user wants to exit gracefully) ----
            if is_end_intent(message):
                farewell = "Anytime. I’ll be right here when you need me."
                audio_b64 = maybe_make_tts(farewell) if lane == "live" else None
                return jsonify({
                    "reply_text": farewell,
                    "audio_b64": audio_b64,
                    "visemes": [],
                    "actions": [],
                    "suggestions": ["Start over", "No suggestions", "End chat"],
                    "end": True
                })

            # ---- LLM reply (persona + logging happens inside gen_reply) ----
            reply_text = gen_reply(user_id, name, message, role, region)

            actions = []
            top = None

            # ---- Account Q&A intents ----
            owner_q = OWNER_PAT.search(message)
            team_q  = TEAM_PAT.search(message)

            if owner_q or team_q:
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

            # ---- Doc retrieval intents ----
            need_doc = bool(DOC_NEED_PAT.search(message)) or bool(PRESENT_PAT.search(message))
            if need_doc:
                hits = repo_search(message, limit=5)
                if hits:
                    top = hits[0]
                    # Offer both present and download actions
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

                    # If the LLM reply is short, enrich with snippet
                    snippet = top.get("snippet")
                    if snippet and isinstance(reply_text, str) and len(reply_text) < 220:
                        reply_text = f"{reply_text}\n\n{snippet}"

            # ---- TTS for live lane ----
            audio_b64 = None
            visemes = None
            if lane == "live":
                audio_b64 = maybe_make_tts(reply_text)
                visemes = []  # populate when switching to viseme_timestamps endpoint

            # ---- Suggestions to keep it human & flowing ----
            suggestions = build_suggestions(message, reply_text, top)

            return jsonify({
                "reply_text": reply_text,
                "audio_b64": audio_b64,
                "visemes": visemes,
                "actions": actions,
                "suggestions": suggestions,
                "end": False
            })
        except Exception:
            import traceback as _tb
            print(_tb.format_exc())
            return jsonify({"error": "chat failed"}), 500

    return bp
