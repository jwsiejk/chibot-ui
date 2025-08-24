
# admin_trace.py — Admin-only call tracing views.
# Usage:
#   from admin_trace import trace_bp, begin_trace, add_trace, end_trace
#   app.register_blueprint(trace_bp)
#
#   t = begin_trace(user_email, route="/api/chat")
#   add_trace(t, "llm_request", {...})
#   add_trace(t, "llm_response", {...})
#   end_trace(t, "ok", {"summary":"..."})
#
# Requires: Flask app session to include 'user' dict with 'email' or a function get_current_user().
# Storage: if DATABASE_URL is configured and 'logs' table exists, entries are persisted.
# Otherwise falls back to an in-memory ring buffer.
#
import os
import json
import time
import uuid
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from flask import Blueprint, request, abort, Response, current_app

try:
    import psycopg2, psycopg2.extras
    HAS_DB = True
except Exception:
    HAS_DB = False

ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "jwsiejk@purestorage.com").split(",") if e.strip()}
TRACE_MEMORY_LIMIT = int(os.getenv("TRACE_MEMORY_LIMIT", "500"))  # max entries in memory

trace_bp = Blueprint("trace", __name__, url_prefix="/admin")

# In-memory ring buffer fallback
_TRACE_LOCK = threading.Lock()
_TRACE_ENTRIES = []  # list of dicts

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _db_conn():
    dsn = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URI")
    if not dsn:
        return None
    try:
        return psycopg2.connect(dsn, sslmode="require")
    except Exception:
        return None

def _db_exec(sql, params=None):
    conn = _db_conn()
    if not conn:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or [])
            if cur.description:
                return cur.fetchall()
            return None
    finally:
        try: conn.close()
        except Exception: pass

def ensure_table():
    if not HAS_DB:
        return
    try:
        _db_exec("""
        create table if not exists logs (
            id bigserial primary key,
            ts timestamptz not null default now(),
            trace_id text not null,
            user_email text,
            route text,
            phase text,
            details jsonb
        );
        """)
    except Exception:
        # ignore
        pass

ensure_table()

def _authorized():
    # You can replace this with real session user lookup
    email = None
    try:
        u = getattr(request, "user", None)
        if isinstance(u, dict):
            email = (u.get("email") or "").lower()
    except Exception:
        email = None
    # Also allow header override if behind your own auth
    if not email:
        email = (request.headers.get("X-User-Email") or "").lower()
    if not email:
        # final attempt: cookie/session handshake via app-provided function
        try:
            get_user = current_app.config.get("GET_CURRENT_USER_FUNC")
            if callable(get_user):
                u = get_user()
                if isinstance(u, dict):
                    email = (u.get("email") or "").lower()
        except Exception:
            pass
    if not email or email not in ADMIN_EMAILS:
        return False
    return True

def begin_trace(user_email: Optional[str], route: str) -> Dict[str, Any]:
    t = {
        "trace_id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "user_email": (user_email or "").lower(),
        "route": route,
        "events": [],
        "t0": time.perf_counter(),
    }
    return t

def add_trace(trace: Dict[str, Any], phase: str, details: Dict[str, Any]):
    evt = {"at": _now_iso(), "phase": phase, "details": details}
    trace["events"].append(evt)

def end_trace(trace: Dict[str, Any], status: str, details: Optional[Dict[str, Any]] = None):
    t1 = time.perf_counter()
    trace["duration_ms"] = int((t1 - trace["t0"]) * 1000)
    trace["status"] = status
    if details:
        add_trace(trace, "done", details)
    # persist
    try:
        if HAS_DB and _db_conn() is not None:
            _db_exec(
                "insert into logs (trace_id, user_email, route, phase, details) values (%s,%s,%s,%s,%s)",
                [trace["trace_id"], trace.get("user_email"), trace.get("route"), "trace", json.dumps(trace)],
            )
        else:
            with _TRACE_LOCK:
                _TRACE_ENTRIES.append(trace)
                if len(_TRACE_ENTRIES) > TRACE_MEMORY_LIMIT:
                    _TRACE_ENTRIES.pop(0)
    except Exception:
        pass
    return trace

@trace_bp.get("/calls")
def calls_index():
    if not _authorized():
        return abort(403)
    # Prefer DB if available
    rows = None
    if HAS_DB and _db_conn() is not None:
        rows = _db_exec("select ts, details->>'trace_id' as trace_id, details->>'user_email' as user_email, details->>'route' as route, (details->>'duration_ms')::int as duration_ms, details from logs where phase='trace' order by id desc limit 200")
    if not rows:
        with _TRACE_LOCK:
            rows = list(reversed(_TRACE_ENTRIES[-200:]))
        # Normalize to the same shape
        rows = [
            {"ts": r.get("ts") or r.get("events")[0]["at"], "trace_id": r["trace_id"], "user_email": r.get("user_email"), "route": r.get("route"), "duration_ms": r.get("duration_ms"), "details": r}
            for r in rows
        ]
    # Render minimal HTML
    html_parts = [
        "<h1>Ask Chip — Admin Call Log</h1>",
        "<style>body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial} table{border-collapse:collapse;width:100%} th,td{border:1px solid #ddd;padding:8px} th{background:#f4f4f4;text-align:left}</style>",
        "<table><thead><tr><th>Time (UTC)</th><th>Trace ID</th><th>User</th><th>Route</th><th>Duration</th><th>View</th></tr></thead><tbody>"
    ]
    for r in rows:
        html_parts.append(f"<tr><td>{r.get('ts')}</td><td>{r.get('trace_id')}</td><td>{r.get('user_email','')}</td><td>{r.get('route')}</td><td>{r.get('duration_ms')} ms</td><td><a href=\"/admin/calls/{r.get('trace_id')}\">open</a></td></tr>")
    html_parts.append("</tbody></table>")
    return Response("\n".join(html_parts), mimetype="text/html")

@trace_bp.get("/calls/<trace_id>")
def calls_show(trace_id):
    if not _authorized():
        return abort(403)
    doc = None
    if HAS_DB and _db_conn() is not None:
        rows = _db_exec("select details from logs where phase='trace' and details->>'trace_id'=%s order by id desc limit 1", [trace_id])
        if rows:
            doc = rows[0]["details"]
    if not doc:
        with _TRACE_LOCK:
            for r in _TRACE_ENTRIES:
                if r.get("trace_id") == trace_id:
                    doc = r
                    break
    if not doc:
        return abort(404)
    # pretty json
    return Response(json.dumps(doc, indent=2), mimetype="application/json")

