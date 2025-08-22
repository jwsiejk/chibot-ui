# chat_routes.py — Ask Chip (lean route, no self-contained logic)
# Route-only file. All business logic is delegated to app services via deps.
# This matches our agreed structure and preserves all functionality.

from __future__ import annotations

from flask import Blueprint, request, jsonify, session, current_app


def create_chat_blueprint(deps: dict):
    """
    Expected deps (callables) provided by the app:
      - parse_email_intent(text:str) -> dict|None
      - handle_account_intents(text:str, base_url:str, sess:dict) -> str|None
      - fulfill_email_intent(intent:dict, base_url:str, user_id:str) -> str
      - generate_chip_response(user_id:str, name:str, text:str, role:str, region:str) -> str
      - log_conversation(user_id:str, user_text:str, reply_text:str) -> None   (optional)
      - word_cap(text:str) -> str                                             (optional; 30-word cap)
    """

    # Resolve deps with clear errors for missing pieces
    def _need(key):
        fn = deps.get(key)
        if not callable(fn):
            raise RuntimeError(f"Missing required dependency: {key}")
        return fn

    # --- Robust parse_email_intent resolution ---
    try:
        parse_email_intent = _need("parse_email_intent")
    except RuntimeError:
        try:
            from services.intents import parse_email_intent
            current_app.logger.warning(
                "Dependency 'parse_email_intent' not provided by app — using fallback from services.intents"
            )
        except ImportError:
            def parse_email_intent(text: str):
                current_app.logger.error(
                    "No parse_email_intent available — returning None."
                )
                return None

    handle_account_intents = _need("handle_account_intents")
    fulfill_email_intent   = _need("fulfill_email_intent")
    generate_chip_response = _need("generate_chip_response")

    log_conversation = deps.get("log_conversation") if callable(deps.get("log_conversation")) else None
    word_cap         = deps.get("word_cap") if callable(deps.get("word_cap")) else (lambda x: x)

    bp = Blueprint("chat", __name__)

    @bp.post("/chat")
    def chat():
        data = request.get_json(force=True) or {}
        user_text = (data.get("message") or "").strip()
        if not user_text:
            reply = "Say that again?"
            return jsonify({"reply_text": reply, "reply": reply})

        # Session context (owned by the app)
        user_id = (session.get("user_id") or "").strip().lower()
        name    = session.get("name")   or (user_id or "there")
        role    = session.get("role")   or "engineer"
        region  = session.get("region") or "NA"

        base_url = request.host_url.rstrip("/")

        # ---------- Fast-path A: email intents ----------
        intent = parse_email_intent(user_text)
        if intent:
            if not user_id:
                reply = "You’ll need to be logged in so I know where to send it."
                if log_conversation: log_conversation(user_id, user_text, reply)
                return jsonify({"reply_text": reply, "reply": reply})

            try:
                reply = fulfill_email_intent(intent, base_url, user_id)
            except Exception:
                reply = "Email looks down—try again in a bit."
            if log_conversation: log_conversation(user_id, user_text, reply)
            return jsonify({"reply_text": reply, "reply": reply})

        # ---------- Fast-path B: account intents ----------
        quick = handle_account_intents(user_text, base_url, session)
        if quick:
            if log_conversation: log_conversation(user_id, user_text, quick)
            return jsonify({"reply_text": quick, "reply": quick})

        # ---------- Normal LLM path ----------
        reply_text = generate_chip_response(user_id, name, user_text, role, region)
        reply_text = word_cap(str(reply_text))  # app-enforced 30-word cap
        if log_conversation: log_conversation(user_id, user_text, reply_text)
        return jsonify({"reply_text": reply_text, "reply": reply})

    return bp
