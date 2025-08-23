# routes/conversation.py
from flask import Blueprint, request, jsonify
from services.llm_service import generate_response

# Blueprint must exist before decorators
conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")

# -------- helpers ------------------------------------------------------------
def _extract_text_and_history():
    """
    Accepts JSON, form-encoded, or query (?q=...).
    Normalizes to (text, history list).
    """
    text = ""
    history = []

    # JSON body
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        text = data.get("message") or data.get("text") or data.get("prompt") or text
        history = data.get("history") or data.get("messages") or history

    # Form/body fallback
    if not text:
        text = (request.form.get("message")
                or request.form.get("text")
                or request.form.get("prompt")
                or "")

    # Query fallback (useful for quick pings/tests)
    if not text:
        text = request.args.get("q", "").strip()

    # Normalize types
    if not isinstance(history, (list, tuple)):
        history = []
    text = (text or "").strip()
    return text, history

def _respond_ok(resp):
    # normalize various return shapes to {"ok": True, "text": "..."}
    if isinstance(resp, dict) and "text" in resp:
        return jsonify({"ok": True, **resp}), 200
    if isinstance(resp, str):
        return jsonify({"ok": True, "text": resp}), 200
    return jsonify({"ok": True, "text": str(resp)}), 200

# -------- existing endpoint (kept) ------------------------------------------
@conversation_bp.route("/chat_orchestrated", methods=["POST", "OPTIONS"])
def chat_orchestrated():
    if request.method == "OPTIONS":
        return ("", 204)

    text, history = _extract_text_and_history()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    try:
        resp = generate_response(user_text=text, history=history)
        return _respond_ok(resp)
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

# -------- new robust aliases ------------------------------------------------
# These cover common names your UI might use and tolerate GET (for quick tests)
@conversation_bp.route("/orchestrator", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/orchestrate", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/conversation", methods=["GET", "POST", "OPTIONS"])
def chat_orchestrator_alias():
    if request.method == "OPTIONS":
        return ("", 204)

    text, history = _extract_text_and_history()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    try:
        resp = generate_response(user_text=text, history=history)
        return _respond_ok(resp)
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

# Simple ping for UI wiring checks
@conversation_bp.route("/orchestrator/ping", methods=["GET"])
def orchestrator_ping():
    q = (request.args.get("q") or "ok").strip()
    return jsonify({"ok": True, "text": q})
