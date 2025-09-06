from __future__ import annotations
import os, json
from flask import Blueprint, jsonify, request

from app.db_dal import DAL, DBConfig, health_check, anonymize_user, delete_user_data

bp = Blueprint("admin_v1", __name__)

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
    return with_correlation_id(dict(request.headers))

@bp.after_request
def add_corr_header(resp):
    cid = get_correlation_id()
    resp.headers["X-Correlation-Id"] = cid
    return resp

from app.db_dal import DAL, DBConfig
@bp.get("/outbox")
def outbox_list():
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
        return jsonify({"ok": False, "error": str(e)}), 500

from app.services.config_guard import validate_config
import time

@bp.post("/config/preview")
def config_preview():
    cfg = request.get_json(force=True) or {}
    errs = validate_config(cfg)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400
    # Return would-be version id (not persisted)
    return jsonify({"ok": True, "preview_id": int(time.time())})

@bp.post("/config/commit")
def config_commit():
    cfg = request.get_json(force=True) or {}
    errs = validate_config(cfg)
    if errs:
        return jsonify({"ok": False, "errors": errs}), 400
    # persist simple version history in sqlite table (CI)
    dal = make_dal()
    try:
        dal.execute("CREATE TABLE IF NOT EXISTS admin_settings_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, cfg_json TEXT, created_at TEXT)")
    except Exception:
        pass
    import datetime as dt, json
    dal.execute("INSERT INTO admin_settings_versions (cfg_json, created_at) VALUES (?, ?)", (json.dumps(cfg), dt.datetime.utcnow().isoformat()+"Z"))
    return jsonify({"ok": True})

@bp.post("/config/rollback")
def config_rollback():
    dal = make_dal()
    try:
        rows = dal.query("SELECT id, cfg_json FROM admin_settings_versions ORDER BY id DESC LIMIT 2")
        if len(rows) < 2:
            return jsonify({"ok": False, "error":"no previous version"}), 400
        # 'rollback' means discard latest and keep prior
        last_id = rows[0][0] if isinstance(rows[0], tuple) else rows[0]["id"]
        dal.execute("DELETE FROM admin_settings_versions WHERE id=?", (last_id,))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
