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
    try:
        _emit('config_update', config=cfg)
    except Exception:
        pass
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
        try:
            _emit('layout_publish', breakpoint=bp_name, version=v)
        except Exception:
            pass
        return jsonify({"ok": True, "breakpoint": bp_name, "version": v})
    cur = db.memory.setdefault('layouts', {}).setdefault(bp_name, {"version": 0, "state": {}})
    cur["version"] += 1; cur["state"] = state
    try:
        _emit('layout_publish', breakpoint=bp_name, version=cur["version"])
    except Exception:
        pass
    return jsonify({"ok": True, "breakpoint": bp_name, "version": cur["version"]})
@bp.get("/layouts")
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


# --- Admin Log (SSE) ---
_log_buffer = []  # simple in-memory ring
def _emit(kind: str, **fields):
    import json, time
    evt = {"ts": time.time(), "kind": kind}
    evt.update(fields or {})
    line = json.dumps(evt, separators=(",",":"))
    _log_buffer.append(line)
    # trim
    if len(_log_buffer) > 2000:
        del _log_buffer[:len(_log_buffer)-2000]

@bp.get("/logs")
def logs():
    from flask import Response
    hdrs = {"Content-Type":"text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering":"no"}
    # Emit a small payload as ndjson (simple for tests)
    payload = "\n".join(_log_buffer + [json.dumps({"ts": time.time(), "kind":"admin_log", "msg":"hello"})])
    return Response(payload, status=200, headers=hdrs)


@bp.get("/db/health")
def db_health():
    import os, time
    from flask import jsonify
    dialect = "memory"
    connected = False
    info = {}
    try:
        if os.environ.get("DATABASE_URL"):
            try:
                from ..dal import neon_pg
                # connect & ensure schema; neon_pg will set its internal dialect
                neon_pg.ensure_schema()
                # a lightweight query to verify connectivity
                try:
                    neon_pg._exec("SELECT 1", fetch=True)
                    connected = True
                except Exception:
                    connected = False
                # detect dialect
                try:
                    # force init
                    neon_pg._connect()
                    dialect = getattr(neon_pg, "_DIALECT", "postgresql")
                except Exception:
                    dialect = "postgresql"
            except Exception:
                dialect = "unknown"
                connected = False
        else:
            # no DATABASE_URL → in-memory "connected"
            connected = True
            dialect = "memory"
        return jsonify({"ok": True, "dialect": dialect, "connected": connected, "ts": time.time()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/kb/seed")
def kb_seed():
    from ..services.retrieval import add_document
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "Doc").strip()
    body  = (data.get("body") or "").strip()
    tags  = data.get("tags") or ""
    if not body: return jsonify({"ok": False, "error": "body_required"}), 400
    doc_id = add_document(title, body, tags)
    return jsonify({"ok": True, "doc_id": doc_id})


@bp.get("/kb/docs")
def kb_docs():
    from ..services.retrieval import list_documents
    q = request.args.get("query",""); tag = request.args.get("tag",""); page = int(request.args.get("page", "0") or 0); size = int(request.args.get("size","50") or 50)
    items = list_documents(q, tag, size, page*size)
    return jsonify({"ok": True, "items": items})

@bp.get("/kb/docs/<int:doc_id>")
def kb_doc_get(doc_id: int):
    from ..services.retrieval import get_document
    d = get_document(doc_id)
    if not d: return jsonify({"ok": False, "error":"not_found"}), 404
    return jsonify({"ok": True, "doc": d})

@bp.delete("/kb/docs/<int:doc_id>")
def kb_doc_delete(doc_id: int):
    from ..services.retrieval import delete_document
    ok = delete_document(doc_id)
    return jsonify({"ok": bool(ok)})
