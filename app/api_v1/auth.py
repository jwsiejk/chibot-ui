from flask import Blueprint, request, jsonify, session, abort
from ..security_state import set_user, get_user, set_profile, get_profile

bp = Blueprint("auth_v1", __name__, url_prefix="/api/v1/auth")

def _normalize_email(e):
    return (e or "").strip().lower()

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email") or "")
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    # Persist in both process-global (for backward compatibility) and session
    set_user(email)
    session["user"] = {"email": email}
    # Optional profile bootstrap
    profile = data.get("profile") or {}
    if profile:
        set_profile(profile)
        session["profile_complete"] = bool(profile.get("completed", False))
    else:
        session.setdefault("profile_complete", False)
    return jsonify({"ok": True, "email": email, "profile_complete": bool(session["profile_complete"])}), 200

@bp.post("/logout")
def logout():
    session.clear()
    set_user(None)
    set_profile({})
    return jsonify({"ok": True}), 200

@bp.get("/me")
def me():
    email = (session.get("user") or {}).get("email")
    authenticated = bool(email)
    prof = get_profile() if authenticated else {}
    profile_complete = bool(session.get("profile_complete") or (prof.get("completed") if prof else False))
    return jsonify({
        "ok": True,
        "authenticated": authenticated,
        "email": email or "",
        "profile_complete": profile_complete,
        "profile": prof
    }), 200

@bp.post("/profile/save")
def profile_save():
    data = request.get_json(silent=True) or {}
    # basic shape: {name, company, role, ... , completed: true}
    set_profile(data)
    if data.get("completed"):
        session["profile_complete"] = True
    return jsonify({"ok": True, "profile_complete": bool(session.get("profile_complete"))}), 200
