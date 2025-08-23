from flask import Blueprint, request, jsonify, session
import memory
from services.llm_service import generate_response
import logging

conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")


def _db_history():
    try:
        email = session.get("email")
        if not email: return []
        return memory.get_recent_conversation(email, limit=10)
    except Exception:
        return []


def _extract_text_and_history():
    text, history = "", []
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        text = data.get("message") or data.get("text") or data.get("prompt") or text
        history = data.get("history") or data.get("messages") or history
    if not text:
        text = (request.form.get("message") or request.form.get("text") or request.form.get("prompt") or "")
    if not text:
        text = (request.args.get("q") or "").strip()
    if not isinstance(history, (list, tuple)):
        history = []
    return (text or "").strip(), history

def _ok_payload(resp):
    if isinstance(resp, dict):
        text = resp.get("text") or resp.get("reply") or resp.get("message") or ""
    elif isinstance(resp, str):
        text = resp
    else:
        text = str(resp)
    text = (text or "").strip()
    return {"ok": True, "text": text, "reply": text, "message": text}

def _safe_orchestrate(text, history):
    try:
        resp = generate_response(user_text=text, history=history)
        return _ok_payload(resp), 200
    except Exception as e:
        logging.exception("orchestrator crashed: %s", e)
        fallback = ("I hit a snag, but I’m ready to proceed. "
                    "Want a quick overview or step-by-step guidance?")
        return _ok_payload(fallback), 200

@conversation_bp.route("/chat_orchestrated", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/orchestrator", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/orchestrate", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/conversation", methods=["GET","POST","OPTIONS"])
def chat_orchestrator_all():
    if request.method == "OPTIONS":
        return ("", 204)
    text, history = _extract_text_and_history()
    if not text:
        return jsonify(_ok_payload("Tell me what you want to tackle and I’ll jump in.")), 200
    payload, status = _safe_orchestrate(text, history)
    return jsonify(payload), status

@conversation_bp.route("/orchestrator/ping", methods=["GET"])
def orchestrator_ping():
    q = (request.args.get("q") or "ok").strip()
    return jsonify(_ok_payload(q)), 200
