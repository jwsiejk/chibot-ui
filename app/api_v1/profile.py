from flask import Blueprint, request, jsonify
from ..db import db
from ..security_state import get_user

bp = Blueprint("profile_v1", __name__, url_prefix="/api/v1/profile")

def _empty(email):
    return {"email": email or "", "name":"", "title":"", "region":"", "profile_complete": False}

@bp.get("")
def get_profile():
    email = get_user()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    users = db.memory.setdefault("users", {})
    prof = users.get(email) or _empty(email)
    return jsonify({"ok": True, "profile": prof})

@bp.post("")
def save_profile():
    email = get_user()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    users = db.memory.setdefault("users", {})
    data = request.get_json(silent=True) or {}
    prof = users.get(email) or _empty(email)
    prof["email"] = email
    prof["name"] = (data.get("name") or "").strip()
    prof["title"] = (data.get("title") or "").strip()
    prof["region"] = (data.get("region") or "").strip()
    prof["profile_complete"] = bool(prof["name"] and prof["title"])
    users[email] = prof
    try:
        from ..api_v1.admin import _emit as _admin_emit
        _admin_emit("profile:save", email=email, complete=prof["profile_complete"])
    except Exception:
        pass
    return jsonify({"ok": True, "profile": prof})
