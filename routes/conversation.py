# routes/conversation.py
from flask import Blueprint, request, jsonify
from services.llm_service import generate_response

# Blueprint must be defined before decorators
conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")

# --- helpers ---------------------------------------------------------------
def _extract_text_and_history():
    """
    Accept JSON, form-encoded, or query (?q=...).
    Normalize to (text, history list).
    """
    text, history = "", []

    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        text = data.get("message") or data.get("text") or data.get("prompt") or text
        history = data.get("history") or data.get("messages") or history

    if not text:
        text = (request.form.get("message")
                or request.form.get("text")
                or request.form.get("prompt")
                or "")

    if not text:
        text = (request.args.get("q") or "").strip()

    if not isinstance(history, (list, tuple)):
        history = []
    return (text or "").strip(), history

def _ok_payload(resp):
    """
    Normalize various return types to a superset the UI can handle.
    Always include: ok, text, reply, message.
    """
    if isinstance(resp, dict):
        text = resp.get("text") or resp.get("reply") or resp.get("message") or ""
    elif isinstance(resp, str):
        text = resp
    else:
        text = str(resp)

    text = (text or "").strip()
    return {
        "ok": True,
        "text": text,
        "reply": text,     # alias for UIs expecting 'reply'
        "message": text,   # alias for UIs expecting 'message'
    }

# --- existing endpoint (kept) ----------------------------------------------
@conversation_bp.route("/chat_orchestrated", methods=["POST", "OPTIONS"])
def chat_orchestrated():
    if request.method == "OPTIONS":
        return ("", 204)

    text, history = _extract_text_and_history()
    if not text:
        return jsonify({"ok": False, "error": "Prompt required"}), 400

    try:
        resp = generate_response(user_text=text, history=history)
        return jsonify(_ok_payload(resp)), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

# --- robust aliases (added) ------------------------------------------------
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
        return jsonify(_ok_payload(resp)), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"orchestrator failed: {e}"}), 500

# Quick ping for wiring checks
@conversation_bp.route("/orchestrator/ping", methods=["GET"])
def orchestrator_ping():
    q = (request.args.get("q") or "ok").strip()
    return jsonify({"ok": True, "text": q, "reply": q, "message": q}), 200
