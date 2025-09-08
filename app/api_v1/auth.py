from flask import Blueprint, request, jsonify, session, abort
from ..security_state import set_user, get_user, set_profile, get_profile

bp = Blueprint("auth_v1", __name__, url_prefix="/api/v1/auth")

def _normalize_email(e):
    return (e or "").strip().lower()

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    session["user"] = {"email": email}
    return jsonify({"ok": True}), 200

@bp.post("/logout")
def logout():
    session.clear()
    set_user(None)
    set_profile({})
    return jsonify({"ok": True}), 200

@bp.get("/me")
def me():
    u = session.get("user") or {}
    email = u.get("email") or ""
    authenticated = bool(email)
    from ..security_state import get_profile
    prof = get_profile() if authenticated else {}
    profile_complete = bool(session.get("profile_complete") or (prof.get("completed") if prof else False))
    return jsonify({"ok": True, "authenticated": authenticated, "email": email, "profile_complete": profile_complete, "profile": prof}), 200

@bp.post("/profile/save")
def profile_save():
    data = request.get_json(silent=True) or {}
    from ..security_state import set_profile
    set_profile(data or {})
    if data.get("completed"):
        session["profile_complete"] = True
    return jsonify({"ok": True, "profile_complete": bool(session.get("profile_complete"))}), 200
