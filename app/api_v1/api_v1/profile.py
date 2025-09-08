from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
bp = Blueprint("profile", __name__)

@bp.get("/get")
def profile_get():
    email = get_user() or "user@example.com"
    prof = db.memory.get('profiles',{}).get(email)
    exists = bool(prof)
    return jsonify({"ok": True, "exists": exists, "profile": prof or {}})

@bp.post("/save")
def profile_save():
    email = get_user() or "user@example.com"
    data = request.get_json(silent=True) or {}
    db.memory.setdefault('profiles',{})[email] = data
    try:
        import os
        if os.environ.get("DATABASE_URL"):
            from ..dal import neon_pg
            neon_pg.ensure_schema(); neon_pg.upsert_user(email, data.get("name"), data.get("title"), data.get("region"))
            neon_pg.save_profile(email, data)
    except Exception:
        pass
    return jsonify({"ok": True})