# app/api_v1/admin.py
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ..db import db
from ..security_state import get_user
from ..services import config_store
from ..services.mailer import send_transcript
import json, os, time

# DAL
NEON_OK = False
try:
    from ..dal import neon_pg
    NEON_OK = neon_pg.ensure_schema()
except Exception:
    NEON_OK = False

bp = Blueprint("admin", __name__)

# --- Config ---
@bp.get("/config")
def cfg_get():
    cfg = db.get_config()
    ver = 0
    try:
        ver = config_store.get_config_version()
    except Exception:
        pass
    return jsonify({"ok": True, "config": cfg, "version": ver})

@bp.post("/config/update")
def cfg_update():
    data = request.get_json(silent=True) or {}
    updates = data.get("updates") or {}
    cfg = config_store.update_config(updates)
    ver = 0
    try:
        ver = config_store.get_config_version()
    except Exception:
        pass
    return jsonify({"ok": True, "config": cfg, "version": ver})

# --- Layouts with versioning ---
@bp.post("/layouts/publish")
def layouts_publish():
    data = request.get_json(silent=True) or {}
    bp_name = data.get("breakpoint") or "desktop"
    state = data.get("state") or {}
    note = data.get("note") or ""
    if os.environ.get("DATABASE_URL") and NEON_OK:
        v = neon_pg.save_layout(bp_name, state, note)
        db.memory.setdefault('layouts', {})[bp_name] = {"version": v, "state": state}
        return jsonify({"ok": True, "breakpoint": bp_name, "version": v})
    # in-memory fallback
    cur = db.memory.setdefault('layouts', {}).setdefault(bp_name, {"version": 0, "state": {}})
    cur["version"] += 1; cur["state"] = state
    return jsonify({"ok": True, "breakpoint": bp_name, "version": cur["version"]})

@bp.get("/layouts")
def layouts_list():
    bp_name = request.args.get("breakpoint") or "desktop"
    if os.environ.get("DATABASE_URL") and NEON_OK:
        items = neon_pg.list_layouts(bp_name)
        return jsonify({"ok": True, "items": items})
    cur = db.memory.get('layouts', {}).get(bp_name)
    items = []
    if cur:
        items.append({"version": cur["version"], "state": cur["state"], "note":"mem", "created_at":time.time()})
    return jsonify({"ok": True, "items": items})

@bp.post("/layouts/rollback")
def layouts_rollback():
    data = request.get_json(silent=True) or {}
    bp_name = data.get("breakpoint") or "desktop"
    version = int(data.get("version") or 1)
    if os.environ.get("DATABASE_URL") and NEON_OK:
        state = neon_pg.get_layout(bp_name, version) or {}
        v = neon_pg.save_layout(bp_name, state, f"rollback to {version}")
        db.memory.setdefault('layouts', {})[bp_name] = {"version": v, "state": state}
        return jsonify({"ok": True, "breakpoint": bp_name, "version": v, "state": state})
    cur = db.memory.setdefault('layouts', {}).setdefault(bp_name, {"version":0, "state":{}})
    cur["version"] += 1
    return jsonify({"ok": True, "breakpoint": bp_name, "version": cur["version"], "state": cur["state"]})

# --- Users & Memory ---
@bp.get("/users")
def users():
    items = []
    if os.environ.get("DATABASE_URL") and NEON_OK:
        items = neon_pg.list_users()
        if not items:
            # derive from sessions
            sess = neon_pg.list_sessions()
            seen = {}
            for s in sess:
                em = s.get("email") or "user@example.com"
                if em not in seen:
                    seen[em] = {"email": em, "created_at": time.time(), "last_seen": time.time()}
            items = list(seen.values())
    else:
        items = [{"email": e, "created_at": time.time(), "last_seen": time.time()} for e in db.memory.get('users',{}).keys()]
    return jsonify({"ok": True, "items": items})

@bp.get("/sessions")
def sessions_list():
    email = request.args.get("user")
    if os.environ.get("DATABASE_URL") and NEON_OK:
        items = neon_pg.list_sessions(email=email)
        try:
            all_items = neon_pg.list_sessions(email=None)
            ids = {s['id'] for s in items}
            for s in all_items:
                if s['id'] not in ids:
                    items.append(s)
        except Exception:
            pass
    else:
        items = [{"id": sid, "email": s.get("email","user@example.com"), "persona_id": s.get("persona_id","chip"),
                  "started_at": time.time(), "ended_at": None, "summary": {}} for sid,s in db.memory.get('sessions',{}).items()]
    return jsonify({"ok": True, "items": items})

@bp.get("/session/<sid>")
def session_detail(sid):
    # transcript
    if os.environ.get("DATABASE_URL") and NEON_OK:
        msgs = neon_pg.list_messages(sid)
        transcript = "\n".join([f"{m['role'].upper()}: {m['text']}" for m in msgs])
    else:
        msgs = db.memory.get('sessions',{}).get(sid,{}).get('messages',[])
        transcript = "\n".join([f"{r.upper()}: {t}" for r,t in msgs])
    return jsonify({"ok": True, "transcript": transcript, "count": len(msgs)})

@bp.post("/session/<sid>/email")
def session_email(sid):
    to = (request.get_json(silent=True) or {}).get("to") or get_user()
    body = db.get_transcript(sid)
    send_transcript(to, f"Ask Chip transcript: {sid}", body)
    return jsonify({"ok": True, "emailed": True})

@bp.post("/session/<sid>/anonymize")
def session_anonymize(sid):
    if os.environ.get("DATABASE_URL") and NEON_OK:
        neon_pg.anonymize_session(sid)
    else:
        s = db.memory.get('sessions',{}).get(sid)
        if s:
            s['email'] = 'anonymized@example.com'
    return jsonify({"ok": True})