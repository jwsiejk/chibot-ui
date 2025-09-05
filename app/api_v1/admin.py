# app/api_v1/admin.py
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ..db import db
from ..security_state import get_user
from ..admin_events import admin_events
from ..logging import admin_log
import json, os, time

bp = Blueprint("admin", __name__)

def _parse_allowlist(raw: str) -> set[str]:
    raw = (raw or "").strip()
    if not raw:
        return set()
    for ch in (";", " "):
        raw = raw.replace(ch, ",")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}

def _layouts():
    return db.memory.setdefault("layouts", {})

@bp.before_request
def _guard():
    # Allow logs SSE unauthenticated so diagnostics/ops can see health.
    # All other admin endpoints remain gated.
    if request.endpoint and request.endpoint.endswith("admin.logs"):
        return None

    allowed = _parse_allowlist(os.getenv("ADMIN_EMAILS", ""))
    # If no ADMIN_EMAILS configured, admin is open (dev mode)
    if not allowed:
        return None

    email = (get_user() or "").lower()
    if email not in allowed:
        return jsonify({"ok": False, "error": "forbidden"}), 403

# ---------- Live Logs (SSE) ----------
@bp.get("/logs")
def logs():
    """Server-Sent Events stream for the Admin Log."""
    q = admin_events.subscribe()

    def event(data: str, ev: str | None = None) -> str:
        prefix = f"event: {ev}\n" if ev else ""
        return prefix + f"data: {data}\n\n"

    def gen():
        # Initial hello (clients PASS on first message)
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

# ---------- Config (runtime editable) ----------
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

# ---------- Layouts (Design Mode) ----------
# Shape we persist:
# layouts = {
#   "draft": {"desktop": {"version": 1, "updated_by": "...", "updated_at": 123.4,
#              "layout": {"stage_side":"left|right",
#                         "show_instruction_strip": true,
#                         "show_state_dots": true}}},
#   "published": {...}
# }

def _default_layout():
    cfg = db.get_config()
    return {
        "stage_side": "left",
        "show_instruction_strip": bool(cfg.get("show_instruction_strip", True)),
        "show_state_dots": bool(cfg.get("show_state_dots", True)),
    }

@bp.get("/layouts")
def get_layout():
    variant = (request.args.get("variant") or "published").lower()
    breakpoint = request.args.get("breakpoint") or "desktop"
    layouts = _layouts()
    entry = (layouts.get(variant) or {}).get(breakpoint)
    if not entry:
        entry = {
            "version": 1,
            "updated_by": get_user() or "system",
            "updated_at": time.time(),
            "layout": _default_layout(),
        }
    return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, **entry})

@bp.post("/layouts")
def set_layout():
    data = request.get_json(silent=True) or {}
    # variant: "draft" or "published" (default: draft)
    variant = (data.get("variant") or "draft").lower()
    breakpoint = data.get("breakpoint") or "desktop"
    layout = data.get("layout") or {}

    layouts = _layouts()
    layouts.setdefault(variant, {})
    prev = layouts[variant].get(breakpoint) or {}
    entry = {
        "version": int(prev.get("version") or 0) + 1,
        "updated_by": get_user() or "admin",
        "updated_at": time.time(),
        "layout": {
            "stage_side": layout.get("stage_side", "left"),
            "show_instruction_strip": bool(layout.get("show_instruction_strip", True)),
            "show_state_dots": bool(layout.get("show_state_dots", True)),
        },
    }
    layouts[variant][breakpoint] = entry
    if variant == "published":
        admin_events.emit(
            "layout_updated",
            {"variant": variant, "breakpoint": breakpoint, "entry": entry},
        )
    return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, "version": entry["version"]})

# ---------- Sessions (stub, unchanged) ----------
@bp.get("/sessions")
def list_sessions():
    out = []
    for sid, s in db.memory.get("sessions", {}).items():
        out.append(
            {
                "id": sid,
                "email": s.get("email", "user@example.com"),
                "message_count": len(s.get("messages", [])),
            }
        )
    return jsonify({"ok": True, "sessions": out})

@bp.get("/sessions/<sid>")
def get_session(sid):
    return jsonify({"ok": True, "session_id": sid, "transcript": db.get_transcript(sid)})

# ---------- Snapshot/Restore (unchanged) ----------
@bp.post("/storage/neon/snapshot")
def storage_neon_snapshot():
    from ..dal.neon_sqlite import connect, snapshot_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path)
    snapshot_memory(conn, db.memory, session_id=sess)
    admin_log(f"Snapshot to {path}")
    return jsonify({"ok": True, "path": path})

@bp.post("/storage/neon/restore")
def storage_neon_restore():
    from ..dal.neon_sqlite import connect, restore_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path)
    restore_memory(conn, db.memory, session_id=sess)
    admin_log(f"Restore from {path}")
    return jsonify({"ok": True, "path": path})
