from __future__ import annotations
from flask import Blueprint, request, jsonify, session, Response, stream_with_context
import memory, json, time, re as _re
from services.llm_service import generate_response
from services.call_log import log_event
from services.email_service import send_email
from services.accounts_service import search_accounts

conversation_bp = Blueprint("conversation", __name__, url_prefix="/api")

def _db_history():
    try:
        email = session.get("email")
        if not email: return []
        return memory.get_recent_conversation(email, limit=10)
    except Exception:
        return []

def _ok_payload(resp):
    if isinstance(resp, dict):
        text = resp.get("text") or resp.get("reply") or resp.get("message") or ""
    elif isinstance(resp, str):
        text = resp
    else:
        text = str(resp)
    text = (text or "").strip()
    return {"ok": True, "text": text, "reply": text, "message": text}

def _extract_text_and_history():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or data.get("text") or data.get("prompt") or request.args.get("q") or "").strip()
    history = data.get("history") or data.get("messages") or _db_history() or []
    if not isinstance(history, (list, tuple)):
        history = []
    return text, history

def _safe_orchestrate(text, history):
    try:
        return _ok_payload(text), 200  # passthrough placeholder
    except Exception:
        return _ok_payload("I hit a snag but I’m ready to continue. Want a quick overview or step-by-step?"), 200

@conversation_bp.route("/conversation", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/orchestrator", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/orchestrate", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/v1/orchestrator", methods=["GET","POST","OPTIONS"])
@conversation_bp.route("/api/v1/orchestrator", methods=["GET","POST","OPTIONS"])
def chat_orchestrator_all():
    if request.method == "OPTIONS":
        return ("", 204)
    text, history = _extract_text_and_history()
    if not text:
        return jsonify(_ok_payload("Tell me what you want to tackle and I’ll jump in.")), 200
    payload, status = _safe_orchestrate(text, history)
    return jsonify(payload), status

# --- SSE chat stream (server-sent events) ---
_CANCEL = {}

def _mark_cancel(email: str):
    _CANCEL[email] = time.time()

def _was_cancelled(email: str, since: float) -> bool:
    return _CANCEL.get(email, 0) > since

@conversation_bp.post("/interrupt")
def api_interrupt():
    if not session.get("email"):
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    _mark_cancel(session.get("email"))
    return jsonify({"ok": True})

@conversation_bp.get("/chat/stream")
def api_chat_stream():
    if not session.get("email"):
        return Response("unauthorized", 401)
    user = session.get("email")
    started = time.time()
    text = (request.args.get("q") or "").strip()
    if not text:
        return Response("missing q", 400)

    def _events():
        yield 'event: start\\ndata: {}\\n\\n'

        # basic tool demo: email/accounts
        t = (text or "").lower()
        tool = None
        if t.startswith("email ") or " email " in t:
            to = None
            m = _re.search(r'email\\s+([^\\s]+)', t);  to = m.group(1) if m else None
            ms = _re.search(r'subject:\\s*([^;]+)', t); subject = ms.group(1).strip() if ms else "(no subject)"
            mb = _re.search(r'body:\\s*(.+)', t);      body = mb.group(1).strip() if mb else ""
            tool = {"name": "email.send", "args": {"to": to, "subject": subject, "body": body}}
        elif "account" in t or "search" in t:
            m = _re.search(r'for\\s+(.+)', t); q = m.group(1).strip() if m else text
            tool = {"name": "accounts.search", "args": {"q": q, "limit": 25}}

        if tool:
            yield 'event: tool_call\\ndata: ' + json.dumps(tool) + '\\n\\n'
            try:
                if tool["name"] == "email.send":
                    ok, err = send_email(tool["args"]["to"], tool["args"].get("subject") or "(no subject)", tool["args"].get("body") or "", None)
                    data = {"ok": bool(ok), "error": err}
                elif tool["name"] == "accounts.search":
                    res = search_accounts(tool["args"]["q"], limit=tool["args"].get("limit", 25))
                    data = {"ok": True, "results": res}
                else:
                    data = {"ok": False, "error": "unknown tool"}
            except Exception as e:
                data = {"ok": False, "error": str(e)}
            yield 'event: tool_result\\ndata: ' + json.dumps(data) + '\\n\\n'
            if _was_cancelled(user, started):
                yield 'event: interrupted\\ndata: {}\\n\\n';  return

        try:
            hist = memory.get_recent_conversation(user, limit=10)
        except Exception:
            hist = []
        resp = generate_response(user_text=text, history=hist)
        reply = (resp.get("text") if isinstance(resp, dict) else str(resp or "")).strip()

        if not reply:
            yield 'event: done\\ndata: {}\\n\\n';  return

        chunks = _re.split(r'(?<=[.!?])\\s+', reply)
        for c in chunks:
            if not c: continue
            if _was_cancelled(user, started):
                yield 'event: interrupted\\ndata: {}\\n\\n';  return
            yield 'event: token\\ndata: ' + json.dumps({"delta": c + " "}) + '\\n\\n'
            time.sleep(0.05)

        yield 'event: done\\ndata: {}\\n\\n'

    return Response(stream_with_context(_events()), mimetype="text/event-stream")
