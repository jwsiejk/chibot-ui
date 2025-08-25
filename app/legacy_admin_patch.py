# app/legacy_admin_patch.py
import os, time, logging
from typing import Any, Dict, List
from flask import request, jsonify

log = logging.getLogger(__name__)

class RingLog:
    def __init__(self, capacity: int = 500):
        self.capacity = max(10, int(capacity))
        self._items: List[Dict[str, Any]] = []

    def add(self, item: Dict[str, Any]):
        now = int(time.time() * 1000)
        d = dict(item or {})
        d.setdefault("ts", now)
        self._items.append(d)
        if len(self._items) > self.capacity:
            self._items = self._items[-self.capacity:]

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), self.capacity))
        return list(reversed(self._items[-limit:]))

    def clear(self):
        self._items.clear()

_CALL_LOG = RingLog(capacity=int(os.getenv("ADMIN_CALL_LOG_CAPACITY", "500")))

def _admin_emails() -> List[str]:
    raw = os.getenv("ADMIN_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]

def _is_admin() -> bool:
    # header or bearer token convenience
    hdr = (request.headers.get("X-User-Email") or "").strip().lower()
    auth = (request.headers.get("Authorization") or "").strip()
    tok = ""
    if auth.lower().startswith("bearer "):
        tok = auth.split(" ", 1)[1].strip().lower()

    admins = _admin_emails()
    if not admins:
        # bootstrap mode: allow when not configured
        return True
    return hdr in admins or tok in admins

def _openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

def _eleven_configured() -> bool:
    return bool(os.getenv("ELEVEN_API_KEY")) or bool(os.getenv("ELEVENLABS_API_KEY"))

def _database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))

def extend_app(app):
    # Tap incoming requests so we can log orchestrator and TTS calls
    @app.before_request
    def _tap_requests():
        path = request.path
        if path in ("/orchestrator", "/api/orchestrator"):
            body = request.get_json(silent=True) or {}
            txt = (body.get("text") or "")[:500]
            hist = body.get("history") or []
            _CALL_LOG.add({"type": "chat", "path": path, "text": txt, "history_len": len(hist)})
        elif path in ("/api/voice/tts", "/api/voice/tts_with_visemes"):
            _CALL_LOG.add({"type": "tts", "path": path})

    @app.after_request
    def _no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/health")
    def api_health():
        return jsonify({
            "openai_configured": _openai_configured(),
            "eleven_configured": _eleven_configured(),
            "database_configured": _database_configured(),
            "is_admin": _is_admin(),
        }), 200

    @app.route("/api/admin/calls/recent")
    def api_admin_calls_recent():
        if not _is_admin():
            return jsonify({"error": "forbidden"}), 403
        limit = int(request.args.get("limit", 100))
        return jsonify({"items": _CALL_LOG.recent(limit)}), 200

    @app.route("/api/admin/calls/clear", methods=["POST"])
    def api_admin_calls_clear():
        if not _is_admin():
            return jsonify({"error": "forbidden"}), 403
        _CALL_LOG.clear()
        return jsonify({"ok": True}), 200
