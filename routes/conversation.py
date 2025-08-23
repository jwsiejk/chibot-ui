# routes/conversation.py
from flask import Blueprint, request, jsonify
from services.llm_service import generate_response
import logging

# Blueprint must be defined before decorators
conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")

# ---------------------------- helpers ----------------------------------------
def _extract_text_and_history():
    """
    Accept JSON, form-encoded, or query (?q=...).
    Normalize to (text, history list).
    """
    text, history = "", []

    # JSON
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        text = data.get("message") or data.get("text") or data.get("prompt") or text
        history = data.get("history") or data.get("messages") or history

    # form
    if not text:
        text = (request.form.get("message")
                or request.form.get("text")
                or request.form.get("prompt")
                or "")

    # query
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
    return {"ok": True, "text": text, "reply": text, "message": text}

def _safe_orchestrate(text, history):
    """
    Call generate_response with full protection:
    - Never bubbles an exception to the client.
    - Always returns (json_dict, http_status=200).
    """
    try:
        resp = generate_response(user_text=text, history=history)
        return _ok_payload(resp), 200
    except Exception as e:
        logging.exception("orchestrator crashed: %s", e)
        fallback = (
            "I hit a snag interpreting that, but I’m ready to keep going. "
            "Want a quick overview, or step-by-step guidance?"
        )
        return _ok_payload(fallback), 200

# -------------------- existing endpoint (kept) -------------------------------
@conversation_bp.route("/chat_orchestrated", methods=["POST", "OPTIONS"])
def chat_orchestrated():
    if request.method == "OPTIONS":
        return ("", 204)

    text, history = _extract_text_and_history()
    if not text:
        # Still return 200 with guidance so the UI never trips
        return jsonify(_ok_payload("Give me your question and I’ll draft a precise answer.")), 200

    payload, status = _safe_orchestrate(text, history)
    return jsonify(payload), status

# ------------------- robust aliases (added) ---------------------------------
# These cover common names your UI might use and tolerate GET (for quick tests)
@conversation_bp.route("/orchestrator", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/orchestrate", methods=["GET", "POST", "OPTIONS"])
@conversation_bp.route("/conversation", methods=["GET", "POST", "OPTIONS"])
def chat_orchestrator_alias():
    if request.method == "OPTIONS":
        return ("", 204)

    text, history = _extract_text_and_history()
    if not text:
        return jsonify(_ok_payload("Tell me what you want to tackle and I’ll jump in.")), 200

    payload, status = _safe_orchestrate(text, history)
    return jsonify(payload), status

# Simple ping for wiring checks (always 200)
@conversation_bp.route("/orchestrator/ping", methods=["GET"])
def orchestrator_ping():
    q = (request.args.get("q") or "ok").strip()
    return jsonify(_ok_payload(q)), 200
