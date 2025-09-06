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
    # Do NOT call ensure_schema() at import time (can hang if DB unreachable).
    # We will try inside endpoints on-demand with a short timeout handled in neon_pg.
    NEON_OK = True
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
    cur = db.memory.setdefault('layouts', {}).setdefault(bp_name, {"version": 0, "state": {}})
    cur["version"] += 1; cur["state"] = state
    try:
        _emit('layout_publish', breakpoint=bp_name, version=cur["version"])
    except Exception:
        pass
    return jsonify({"ok": True, "breakpoint": bp_name, "version": cur["version"]})
    cur = db.memory.get('layouts', {}).get(bp_name)
    items = []
    if cur:
        items.append({"version": cur["version"], "state": cur["state"], "note":"mem", "created_at":time.time()})
    return jsonify({"ok": True, "items": items})

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


    def sse():
        import time, json
        # Flush existing lines first
        snapshot = list(_log_buffer)
        for line in snapshot:
            yield f"data: {line}\n\n"
        idx = len(snapshot)
        last_ping = time.time()
        # Continuous stream
        while True:
            # Send any new lines
            if idx < len(_log_buffer):
                for line in _log_buffer[idx:]:
                    yield f"data: {line}\n\n"
                idx = len(_log_buffer)
                last_ping = time.time()
            # Heartbeat every 10s
            if time.time() - last_ping >= 10:
                yield "event: ping\ndata: keepalive\n\n"
                last_ping = time.time()
            time.sleep(1.0)
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(sse(), status=200, headers=headers)



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

@bp.get("/runtime")
def runtime():
    import os, time, platform
    from flask import jsonify
    from ..db import db
    from ..services.llm_provider import get_provider_name as llm_name
    from ..services.tts_provider import get_tts_provider_name as tts_name
    from ..services.stt_provider import get_stt_provider_name as stt_name
    import app.drain_state as drain_state
    cfg = db.get_config()
    def provider_info(name_fn):
        try: return {"ok": True, "name": name_fn(cfg), "error": None}
        except Exception as e: return {"ok": False, "name": None, "error": str(e)}
    runtime = {
        "timestamp": time.time(),
        "python": platform.python_version(),
        "versions": {"python": platform.python_version()},
        "env": {"APP_ENV": os.getenv("APP_ENV",""), "ENV": os.getenv("ENV","")},
        "keys": {"OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")), "ELEVENLABS_API_KEY": bool(os.getenv("ELEVENLABS_API_KEY"))},
        "providers": {"llm": provider_info(llm_name), "stt": provider_info(stt_name), "tts": provider_info(tts_name)},
        "smtp_ready": bool(os.getenv("EMAIL_HOST") and os.getenv("EMAIL_HOST_USER") and os.getenv("EMAIL_HOST_PASSWORD")),
        "commit": os.getenv("RENDER_GIT_COMMIT") or os.getenv("COMMIT") or "",
        "draining": drain_state.is_draining(),
    }
    return jsonify({"ok": True, "runtime": runtime})


@bp.get("/logs")
def logs():
    """Server-Sent Events stream of the Admin Log."""
    from flask import Response
    def sse():
        import time, json
        # Immediate hello so EventSource resolves quickly
        yield "data: " + json.dumps({"ts": time.time(), "kind": "hello", "msg": "admin log up"}) + "\n\n"
        # Flush existing buffered lines
        snapshot = list(_log_buffer)
        for line in snapshot:
            yield f"data: {line}\n\n"
        idx = len(snapshot)
        last_ping = time.time()
        while True:
            # Send any new lines
            if idx < len(_log_buffer):
                for line in _log_buffer[idx:]:
                    yield f"data: {line}\n\n"
                idx = len(_log_buffer)
                last_ping = time.time()
            # Heartbeat every 10s
            if time.time() - last_ping >= 10:
                yield "event: ping\ndata: keepalive\n\n"
                last_ping = time.time()
            time.sleep(1.0)
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(sse(), status=200, headers=headers)
