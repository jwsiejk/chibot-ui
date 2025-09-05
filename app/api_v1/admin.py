# app/api_v1/admin.py
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ..db import db
from ..security_state import get_user
from ..admin_events import admin_events
from ..logging import admin_log
import json, os, time

# Optional Neon adapter (persists layouts/config if DATABASE_URL is set)
try:
    from ..dal import neon_pg  # add file if you haven't (see earlier message)
    NEON_OK = neon_pg.ensure_schema()
except Exception:
    NEON_OK = False

bp = Blueprint("admin", __name__)

def _parse_allowlist(raw: str) -> set[str]:
    raw = (raw or "").strip()
    if not raw: return set()
    for ch in (";", " "): raw = raw.replace(ch, ",")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}

def _layouts_mem():
    return db.memory.setdefault("layouts", {})  # {"draft": {...}, "published": {...}}

def _default_layout():
    cfg = db.get_config()
    return {
        "mode": "grid",                          # "grid" or "free"
        "stage_side": "left",
        "show_instruction_strip": bool(cfg.get("show_instruction_strip", True)),
        "show_state_dots": bool(cfg.get("show_state_dots", True)),
        # in "free" mode we'll also store: "nodes": { key: {x,y,w,h} } (percentages)
    }

@bp.before_request
def _guard():
    # Logs SSE is readable so operators/diagnostics can see health
    if request.endpoint and request.endpoint.endswith("admin.logs"):
        return None
    allowed = _parse_allowlist(os.getenv("ADMIN_EMAILS", ""))
    if not allowed:  # dev mode
        return None
    email = (get_user() or "").lower()
    if email not in allowed:
        return jsonify({"ok": False, "error": "forbidden"}), 403

# ---------- Live Logs (SSE) ----------
@bp.get("/logs")
def logs():
    q = admin_events.subscribe()
    def event(data: str, ev: str | None = None) -> str:
        prefix = f"event: {ev}\n" if ev else ""
        return prefix + f"data: {data}\n\n"
    def gen():
        yield event(json.dumps({"ts": time.time(), "kind": "connected"}))
        while True:
            try:
                item = q.get(timeout=25)
                payload = item.get("data", {})
                ev = item.get("event", "message")
                yield event(json.dumps(payload, separators=(",", ":")), ev)
            except Exception:
                yield ": ping\n\n"
    headers = {"Content-Type":"text/event-stream","Cache-Control":"no-cache","X-Accel-Buffering":"no"}
    return Response(stream_with_context(gen()), headers=headers)

# ---------- Config (unchanged) ----------
@bp.get("/config")
def get_config():
    if NEON_OK:
        got = neon_pg.config_get()
        if got is not None:
            return jsonify({"ok": True, "config": got})
    return jsonify({"ok": True, "config": db.get_config()})

@bp.post("/config")
def post_config():
    data = request.get_json(silent=True) or {}
    if NEON_OK:
        cfg = neon_pg.config_set(data, updated_by=get_user() or "admin")
    else:
        cfg = db.update_config(data)
    admin_log(f"Config updated: {list(data.keys())}")
    admin_events.emit("config_updated", {"updates": data, "config": cfg})
    return jsonify({"ok": True, "config": cfg})

# ---------- Layouts: GET/POST full JSON (grid or free with nodes) ----------
@bp.get("/layouts")
def get_layout():
    variant = (request.args.get("variant") or "published").lower()
    breakpoint = request.args.get("breakpoint") or "desktop"
    if NEON_OK:
        got = neon_pg.layout_get(variant, breakpoint)
        if got:
            return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, **got})
    # memory fallback
    store = _layouts_mem()
    entry = (store.get(variant) or {}).get(breakpoint)
    if not entry:
        entry = {"version": 1, "updated_by": get_user() or "system", "updated_at": time.time(), "layout": _default_layout()}
    return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, **entry})

@bp.post("/layouts")
def set_layout():
    data = request.get_json(silent=True) or {}
    variant = (data.get("variant") or "draft").lower()
    breakpoint = data.get("breakpoint") or "desktop"
    layout_in = data.get("layout") or {}
    # merge over defaults so we never lose required flags
    merged = {**_default_layout(), **layout_in}

    if NEON_OK:
        saved = neon_pg.layout_set(variant, breakpoint, merged, updated_by=get_user() or "admin")
        if saved and variant == "published":
            admin_events.emit("layout_updated", {"variant": variant, "breakpoint": breakpoint, "entry": saved})
        return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, "version": saved["version"]})

    # memory fallback
    store = _layouts_mem()
    prev = (store.get(variant) or {}).get(breakpoint) or {"version": 0}
    entry = {
        "version": int(prev["version"]) + 1,
        "updated_by": get_user() or "admin",
        "updated_at": time.time(),
        "layout": merged,
    }
    store.setdefault(variant, {})[breakpoint] = entry
    if variant == "published":
        admin_events.emit("layout_updated", {"variant": variant, "breakpoint": breakpoint, "entry": entry})
    return jsonify({"ok": True, "variant": variant, "breakpoint": breakpoint, "version": entry["version"]})

# ---------- Sessions / Snapshot (unchanged) ----------
@bp.get("/sessions")
def list_sessions():
    out = []
    for sid, s in db.memory.get("sessions", {}).items():
        out.append({"id": sid, "email": s.get("email", "user@example.com"), "message_count": len(s.get("messages", []))})
    return jsonify({"ok": True, "sessions": out})

@bp.get("/sessions/<sid>")
def get_session(sid):
    return jsonify({"ok": True, "session_id": sid, "transcript": db.get_transcript(sid)})

@bp.post("/storage/neon/snapshot")
def storage_neon_snapshot():
    from ..dal.neon_sqlite import connect, snapshot_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path); snapshot_memory(conn, db.memory, session_id=sess)
    admin_log(f"Snapshot to {path}")
    return jsonify({"ok": True, "path": path})

@bp.post("/storage/neon/restore")
def storage_neon_restore():
    from ..dal.neon_sqlite import connect, restore_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path); restore_memory(conn, db.memory, session_id=sess)
    admin_log(f"Restore from {path}")
    return jsonify({"ok": True, "path": path})
