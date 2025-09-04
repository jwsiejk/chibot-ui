from flask import Blueprint, jsonify, request, Response
from ..db import db
from ..security_state import get_user
from ..admin_events import admin_events
from ..logging import admin_log
import json, os, time, re
bp = Blueprint("admin", __name__)
def _parse_allowlist(raw: str)->set:
    raw=(raw or "").strip()
    if not raw: return set()
    for ch in [";"," "]: raw=raw.replace(ch,",")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}
@bp.before_request
def _guard():
    allowed=_parse_allowlist(os.getenv("ADMIN_EMAILS",""))
    # If no ADMIN_EMAILS configured, admin is open (dev mode)
    if not allowed: return None
    # Allow logs SSE unauthenticated so diagnostics can run
    if request.endpoint and request.endpoint.endswith('admin.logs'):
        return None
    email=(get_user() or "").lower()
    if email not in allowed: return jsonify({"ok": False, "error":"forbidden"}),403
@bp.get("/logs")
def logs():
    q=admin_events.subscribe()
    def event(data: str, event: str | None = None) -> str:
        return (f"event: {event}
" if event else "") + f"data: {data}

"
    def gen():
        # Initial hello
        yield event(json.dumps({"ts": time.time(), "kind":"connected"}))
        last = time.time()
        while True:
            try:
                item=q.get(timeout=25)
                yield event(json.dumps(item.get('data',{})), item.get('event','message'))
            except Exception:
                # keepalive comment to prevent idle close
                yield ": ping

"
    headers = {"Content-Type":"text/event-stream","Cache-Control":"no-cache","X-Accel-Buffering":"no"}
    return Response(gen(), headers=headers)
@bp.get("/config")
def get_config(): return jsonify({"ok": True, "config": db.get_config()})
@bp.post("/config")
def post_config():
    data=request.get_json(silent=True) or {}; cfg=db.update_config(data)
    admin_log(f"Config updated: {list(data.keys())}"); admin_events.emit("config_updated", {"updates": data, "config": cfg})
    return jsonify({"ok": True, "config": cfg})
@bp.get("/sessions")
def list_sessions():
    out=[]; 
    for sid,s in db.memory.get('sessions',{}).items():
        out.append({"id":sid,"email":s.get("email","user@example.com"),"message_count":len(s.get("messages",[]))})
    return jsonify({"ok": True, "sessions": out})
@bp.get("/sessions/<sid>")
def get_session(sid): return jsonify({"ok": True, "session_id": sid, "transcript": db.get_transcript(sid)})
@bp.post("/sessions/<sid>/export.csv")
def export_csv(sid):
    lines=["role,text"]; s=db.memory.get('sessions',{}).get(sid,{"messages":[]})
    for r,t in s['messages']:
        safe=t.replace('"','""'); lines.append(f'{r},"{safe}"')
    return Response("\n".join(lines), mimetype="text/csv")
@bp.post("/sessions/<sid>/export.html")
def export_html(sid):
    s=db.memory.get('sessions',{}).get(sid,{"messages":[]})
    rows="".join([f"<tr><td>{r}</td><td>{t}</td></tr>" for r,t in s['messages']])
    return Response(f"<html><body><table>{rows}</table></body></html>", mimetype="text/html")
@bp.post("/sessions/<sid>/email")
def email_session(sid):
    from ..services.mock_emailer import send_transcript
    email=request.get_json(silent=True).get("email","user@example.com"); ok=send_transcript(email,"Ask Chip — Session transcript",db.get_transcript(sid))
    admin_log(f"Emailed transcript for {sid} to {email}"); return jsonify({"ok": bool(ok)})
@bp.post("/sessions/<sid>/anonymize")
def anonymize(sid):
    s=db.memory.get('sessions',{}).get(sid); 
    if not s: return jsonify({"ok": False, "error":"not_found"}),404
    nm=[]; 
    for role,text in s['messages']:
        t=re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+","[redacted@email]",text); t=t.replace("User","[redacted]"); nm.append((role,t))
    s['messages']=nm; admin_log(f"Anonymized session {sid}"); return jsonify({"ok": True})
@bp.get("/persona")
def persona():
    return jsonify({"ok": True, "persona": {"id":"chip"}})


@bp.post("/storage/neon/init")
def storage_neon_init():
    from ..dal.neon_sqlite import connect, init_schema
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    conn = connect(path); init_schema(conn); admin_log(f"Neon(sqlite) schema at {path}")
    return jsonify({"ok": True, "path": path})

@bp.post("/storage/neon/snapshot")
def storage_neon_snapshot():
    from ..dal.neon_sqlite import connect, snapshot_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path); snapshot_memory(conn, db.memory, session_id=sess); admin_log(f"Snapshot to {path}")
    return jsonify({"ok": True, "path": path})

@bp.post("/storage/neon/restore")
def storage_neon_restore():
    from ..dal.neon_sqlite import connect, restore_memory
    path = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
    sess = (request.get_json(silent=True) or {}).get("session_id")
    conn = connect(path); restore_memory(conn, db.memory, session_id=sess); admin_log(f"Restore from {path}")
    return jsonify({"ok": True, "path": path})
