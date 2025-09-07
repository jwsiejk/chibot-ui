from flask import Blueprint, request, jsonify, session
from ..security_state import set_user, get_user, set_profile, get_profile

bp = Blueprint("auth_v1", __name__, url_prefix="/api/v1/auth")

def _norm(e): return (e or '').strip().lower()

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = _norm(data.get("email"))
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    set_user(email)
    session['user'] = {'email': email}
    profile = data.get("profile") or {}
    if profile:
        set_profile(profile)
        session['profile_complete'] = bool(profile.get("completed"))
    else:
        session.setdefault('profile_complete', False)
    return jsonify({"ok": True, "email": email, "profile_complete": bool(session['profile_complete'])})

@bp.post("/logout")
def logout():
    session.clear()
    set_user(None)
    set_profile({})
    return jsonify({"ok": True})

@bp.get("/me")
def me():
    email = (session.get('user') or {}).get('email') or get_user()
    prof = get_profile()
    profile_complete = bool(session.get('profile_complete') or prof.get("completed"))
    return jsonify({"ok": True, "email": email, "profile_complete": profile_complete, "profile": prof})

@bp.post("/profile/save")
def profile_save():
    data = request.get_json(silent=True) or {}
    set_profile(data)
    if data.get("completed"):
        session['profile_complete'] = True
    return jsonify({"ok": True, "profile_complete": bool(session.get('profile_complete'))})
