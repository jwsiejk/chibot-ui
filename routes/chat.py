# routes/chat.py
from flask import Blueprint, request, jsonify, current_app, session
from services import llm_service
from utils.call_log import call_log

chat_bp = Blueprint("chat_bp", __name__)  # url_prefix is applied by your _register_bp()

def _norm(s: str) -> str:
    return (s or "").strip().lower()

# IMPORTANT: rule must start with '/', and we allow both /api/chat and /api/chat/
@chat_bp.route("/", methods=["POST"], strict_slashes=False)
def chat():
    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or data.get("text") or "").strip()
    if not user_text:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    call_log.add("chat:request", "user", text=user_text)

    try:
        reply = llm_service.chat(user_text=user_text, session_id=session.get("sid"))
    except Exception as e:
        current_app.logger.exception("LLM failure")
        call_log.add("error", "llm_service.chat exception", error=str(e))
        reply = "Sorry—I'm having trouble thinking right now. Please try again."

    # Parrot guard
    if _norm(reply) == _norm(user_text):
        current_app.logger.warning("Parrot trap triggered (reply == user_text)")
        call_log.add("warn", "parrot_trap", user_text=user_text, raw_reply=reply)
        reply = "I hear you. What outcome are you aiming for so I can help?"

    call_log.add("chat:response", "assistant", text=reply)
    return jsonify({"ok": True, "reply": reply})
