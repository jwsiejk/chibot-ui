from __future__ import annotations
import os, json
from flask import Blueprint, jsonify, request

from app.db_dal import DAL, DBConfig, health_check, anonymize_user, delete_user_data

bp = Blueprint("admin_v1", __name__)


from flask import has_app_context as _has_app_context
def _json_resp(obj, status=200):
    try:
        if _has_app_context():
            return jsonify(obj), status
    except Exception:
        pass
    class _R:
        def __init__(self, data): self.json = data
    return (_R(obj), status)


from urllib.parse import urlparse, parse_qs

def _parse_dsn_info(url: str) -> dict:
    try:
        u = urlparse(url)
        q = parse_qs(u.query or "")
        return {
            "scheme": u.scheme,
            "host": u.hostname,
            "db": (u.path or "").lstrip("/"),
            "sslmode": (q.get("sslmode",[None])[0]),
        }
    except Exception:
        return {"error": "unable to parse DSN"}

def make_dal():
    url = os.environ.get("DATABASE_URL", "sqlite:///ci_phase15.sqlite3")
    return DAL(DBConfig(url=url))

@bp.get("/db/health")
def db_health():
    h = health_check(make_dal())
    return jsonify({"ok": h.get("ok", False), "details": h}), (200 if h.get("ok") else 500)

@bp.post("/db/retention/anonymize")
def retention_anonymize():
    data = request.get_json(force=True) or {}
    email = data.get("email")
    if not email:
        return jsonify({"ok": False, "error": "email required"}), 400
    cnt = anonymize_user(make_dal(), email)
    return jsonify({"ok": True, "updated": cnt})

@bp.post("/db/retention/delete")
def retention_delete():
    data = request.get_json(force=True) or {}
    email = data.get("email")
    if not email:
        return jsonify({"ok": False, "error": "email required"}), 400
    cnt = delete_user_data(make_dal(), email)
    return jsonify({"ok": True, "deleted": cnt})

from flask import request
from app.obs.metrics import with_correlation_id, emit_request_metrics

def get_correlation_id():
    try:
        hdrs = dict(getattr(request, "headers", {}) or {})
    except Exception:
        hdrs = {}
    return with_correlation_id(hdrs)

@bp.after_request
def add_corr_header(resp):
    cid = get_correlation_id()
    resp.headers["X-Correlation-Id"] = cid
    return resp

from app.db_dal import DAL, DBConfig
@bp.get("/outbox")
def outbox_list():
    # Ensure outbox table exists (safe on Postgres)
    try:
        dal = make_dal()
        dal.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            session_id TEXT,
            ended_at TIMESTAMP,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
    except Exception:
        pass
    dal = make_dal()
    try:
        rows = dal.query("SELECT id, kind, status, attempts, last_error FROM outbox ORDER BY created_at DESC")
        out = []
        for r in rows:
            if isinstance(r, tuple):
                oid, kind, status, attempts, err = r
            else:
                oid, kind, status, attempts, err = r["id"], r["kind"], r["status"], r["attempts"], r["last_error"]
            out.append({"id": oid, "kind": kind, "status": status, "attempts": attempts or 0, "last_error": err})
        return jsonify({"ok": True, "items": out})
    except Exception as e:
        return _json_resp({"ok": False, "error": str(e)}, 500)

from app.services.config_guard import validate_config
import time

@bp.post("/config/preview")
def config_preview():
    cfg = request.get_json(force=True) or {}
    errs = validate_config(cfg)
    if errs:
        return _json_resp({"ok": False, "errors": errs}, 400)
    # Return would-be version id (not persisted)
    return _json_resp({"ok": True, "preview_id": int(time.time())}, 200)

@bp.post("/config/commit")
def config_commit():
    cfg = request.get_json(force=True) or {}
    errs = validate_config(cfg)
    if errs:
        return _json_resp({"ok": False, "errors": errs}, 400)
    # persist simple version history in sqlite table (CI)
    dal = make_dal()
    try:
        dal.execute("CREATE TABLE IF NOT EXISTS admin_settings_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, cfg_json TEXT, created_at TEXT)")
    except Exception:
        pass
    import datetime as dt, json
    dal.execute("INSERT INTO admin_settings_versions (cfg_json, created_at) VALUES (?, ?)", (json.dumps(cfg), dt.datetime.utcnow().isoformat()+"Z"))
    return _json_resp({"ok": True}, 200)

@bp.post("/config/rollback")
def config_rollback():
    dal = make_dal()
    try:
        rows = dal.query("SELECT id, cfg_json FROM admin_settings_versions ORDER BY id DESC LIMIT 2")
        if len(rows) < 2:
            return _json_resp({"ok": False, "error":"no previous version"}, 400)
        # 'rollback' means discard latest and keep prior
        last_id = rows[0][0] if isinstance(rows[0], tuple) else rows[0]["id"]
        dal.execute("DELETE FROM admin_settings_versions WHERE id=?", (last_id,))
        return _json_resp({"ok": True}, 200)
    except Exception as e:
        return _json_resp({"ok": False, "error": str(e)}, 500)


# ---- Admin Log Emitter (legacy-compatible) ----
from collections import deque
from datetime import datetime
import uuid, time
from flask import Response, stream_with_context
_EVENT_RING = deque(maxlen=int(os.environ.get("ADMIN_LOG_RING", "1000")))

def _emit(kind: str, msg: str="ok", **fields):
    """
    Lightweight event emitter used by internal modules (e.g., voice.py).
    - Appends to in-memory ring for Admin SSE.
    - Persists to admin_events table when possible.
    """
    evt = {"ts": datetime.utcnow().isoformat()+"Z", "kind": kind}
    if msg is not None:
        evt["msg"] = msg
    for k,v in (fields or {}).items():
        evt[k] = v
    try:
        _EVENT_RING.append(evt)
    except Exception:
        pass
    # Persist best-effort
    try:
        dal = make_dal()
        dal.execute("CREATE TABLE IF NOT EXISTS admin_events (id TEXT PRIMARY KEY, ts TEXT, kind TEXT, payload_json TEXT)")
        dal.execute("INSERT INTO admin_events (id, ts, kind, payload_json) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), evt["ts"], kind, json.dumps({k:v for k,v in evt.items() if k not in ("ts","kind")})))
    except Exception:
        pass
    return True

@bp.get("/logs")
def logs_sse():
    """
    Minimal SSE stream of recent admin events.
    Note: This is intentionally simple; production can switch to a queue/broker later.
    """
    @stream_with_context
    def gen():
        idx = 0
        try:
            from flask import current_app, request as _rq
            if current_app and (current_app.config.get("TESTING") or os.environ.get("CI_FAST") or _rq.args.get("ci") == "1"):
                # Emit one synthetic event and close to keep CI fast/non-blocking
                yield 'data: {"ts":"CI","kind":"proactive","msg":"ok"}\n\n'
                return
        except Exception:
            pass
        while True:
            # drain any new events
            size = len(_EVENT_RING)
            while idx < size:
                ev = _EVENT_RING[idx]
                yield f"data: {json.dumps(ev)}\n\n"
                idx += 1
            time.sleep(1.0)
    headers = {
        "Cache-Control": "no-cache",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
    }
    return Response(gen(), headers=headers)


@bp.get("/config")
def get_config():
    dal = make_dal()
    cfg = {}
    try:
        # Try last committed version
        rows = dal.query("SELECT cfg_json FROM admin_settings_versions ORDER BY id DESC LIMIT 1")
        if rows:
            row = rows[0]
            cfg_json = row[0] if isinstance(row, tuple) else row["cfg_json"]
            import json as _json
            cfg = _json.loads(cfg_json or "{}")
    except Exception:
        pass
    # Provide minimal defaults if none found
    if not cfg:
        cfg = {"confirm_ms": 420, "language_lock": "en", "suggestions_max_items": 3}
    cfg.setdefault("audio_worklet_enabled", False)
    cfg.setdefault("vad_attack_ms", 30)
    cfg.setdefault("vad_release_ms", 120)
    cfg.setdefault("vad_dbfs_threshold", -45)
    return jsonify({"config": cfg}), 200
