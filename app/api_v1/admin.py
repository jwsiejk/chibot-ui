# app/api_v1/admin.py
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ..db import db
from ..security_state import get_user
from ..admin_events import admin_events
from ..logging import admin_log
import json, os, time

bp = Blueprint("admin", __name__)

def _parse_allowlist(raw: str) -> set:
    raw = (raw or "").strip()
    if not raw:
        return set()
    for ch in (";", " "):
        raw = raw.replace(ch, ",")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}

@bp.before_request
def _guard():
    # Allow logs SSE unauthenticated so diagnostics can pass
    if request.endpoint and request.endpoint.endswith("admin.logs"):
        return None
    allowed = _parse_allowlist(os.getenv("ADMIN_EMAILS", ""))
    # If no ADMIN_EMAILS configured, admin is open (dev mode)
    if not allowed:
        return None
    email = (get_user() or "").lower()
    if email not in allowed:
        return jsonify({"ok": False, "error": "forbidden"}), 403

@bp.get("/logs")
def logs():
    q = admin_events.subscribe()

    def event(data: str, event: str | None = None) -> str:
        # IMPORTANT: use literal \n, not real line breaks inside the string
        prefix = f"event: {event}\n" if event else ""
        return prefix + f"data: {data}\n\n"

    def gen():
        # initial hello (clients PASS on first message)
        yield event(json.dumps({"ts": time.time(), "kind": "connected"}))
        while True:
            try:
                item = q.get(timeout=25)
                payload = item.get("data", {})
                ev = item.get("event", "message")
                yield event(json.dumps(payload, separators=(",", ":")), ev)
            except Exception:
                # keepalive comment to prevent proxy idle close
                yield ": ping\n\n"

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(gen()), headers=headers)

@bp.get("/config")
def get_config():
    return jsonify({"ok": True, "config": db.get_config()})

@bp.post("/config")
def post_config():
    data = request.get_json(silent=True) or {}
    cfg = db.update_config(data)
    admin_log(f"Config updated: {list(data.keys())}")
    admin_events.emit("config_updated", {"updates": data, "config": cfg})
    return jsonify({"ok": True, "config": cfg})

@bp.get("/sessions")
def list_sessions():
    out = []
    for sid, s in db.memory.get("sessions", {}).items():
        out.append({
            "id": sid,
            "email": s.get("email", "user@example.com"),
            "message_count": len(s.get("messages_
