
from flask import Blueprint, request, jsonify, session
from ..db import db, persist_enabled
from ..middleware.csrf import ensure_csrf_headers
from ..security_state import get_user, set_profile

bp = Blueprint("profile_v1", __name__, url_prefix="/api/v1/profile")

def _empty(email):
    return {"email": email or "", "name":"", "title":"", "region":"", "profile_complete": False}

def _load_profile(email: str) -> dict:
    if persist_enabled():
        try:
            from ..dal.neon_pg import load_profile
            prof = load_profile(email) or {}
            if not prof:
                return _empty(email)
        except Exception:
            return _empty(email)
    else:
        prof = (db.memory.setdefault("profiles", {})).get(email) or _empty(email)
    prof["email"] = email
    prof["profile_complete"] = bool((prof.get("name") or "").strip() and (prof.get("title") or "").strip())
    return prof

def _save_profile(email: str, prof: dict):
    # Always persist; Neon if enabled, else in-memory
    prof = dict(prof or {})
    prof["email"] = email
    prof["profile_complete"] = bool((prof.get("name") or "").strip() and (prof.get("title") or "").strip())
    if persist_enabled():
        try:
            from ..dal.neon_pg import save_profile
            save_profile(email, prof)
        except Exception:
            # If Neon write fails, do NOT silently drop; keep an in-memory copy to avoid UX dead-end
            db.memory.setdefault("profiles", {})[email] = prof
    else:
        db.memory.setdefault("profiles", {})[email] = prof
    # Mirror to session cache
    try:
        set_profile(prof)
        session['profile_complete'] = prof["profile_complete"]
    except Exception:
        pass

@bp.get("")
def get_profile():
    email = get_user()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    prof = _load_profile(email)
    try:
        set_profile(prof)
        session['profile_complete'] = prof["profile_complete"]
    except Exception:
        pass
    resp = jsonify({"ok": True, "profile": prof}); return ensure_csrf_headers(resp)

@bp.post("")
def save_profile():
    email = get_user()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    data = request.get_json(silent=True) or {}
    prof = {
        "email": email,
        "name": (data.get("name") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "region": (data.get("region") or "").strip(),
    }
    _save_profile(email, prof)
    try:
        from ..api_v1.admin import _emit as _admin_emit
        _admin_emit("profile:save", email=email, complete=bool(prof["name"] and prof["title"]))
    except Exception:
        pass
    out = _load_profile(email)
    resp = jsonify({"ok": True, "profile": out}); return ensure_csrf_headers(resp)
