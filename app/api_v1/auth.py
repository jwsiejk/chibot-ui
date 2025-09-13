
from flask import Blueprint, request, jsonify, session, abort
from ..security_state import set_user, get_user, set_profile, get_profile
from ..db import persist_enabled

bp = Blueprint("auth_v1", __name__, url_prefix="/api/v1/auth")

def _normalize_email(e):
    return (e or "").strip().lower()

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email") or "")
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    # Persist user in session
    set_user(email)
    session["user"] = {"email": email}

    # If a profile payload is provided, accept it; otherwise, try to load from Neon
    provided_profile = data.get("profile") or {}
    if provided_profile:
        set_profile(provided_profile)
        session["profile_complete"] = bool(provided_profile.get("completed") or (provided_profile.get("name") and provided_profile.get("title")))
        prof = provided_profile
    else:
        # Load existing profile from Neon (or in-memory fallback) and compute completeness
        try:
            from .profile import _load_profile
            prof = _load_profile(email) or {}
        except Exception:
            prof = {}
        set_profile(prof or {})
        session["profile_complete"] = bool((prof or {}).get("profile_complete") or ((prof or {}).get("name") and (prof or {}).get("title")))

    return jsonify({"ok": True, "email": email, "profile_complete": bool(session.get("profile_complete")), "profile": prof}), 200

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
    prof = {}
    profile_complete = False
    if authenticated:
        try:
            from .profile import _load_profile
            prof = _load_profile(email) or {}
            profile_complete = bool(prof.get("profile_complete") or (prof.get("name") and prof.get("title")))
            from ..security_state import set_profile
            set_profile(prof)
            session['profile_complete'] = profile_complete
        except Exception:
            prof = {}
            profile_complete = bool(session.get('profile_complete', False))
    return jsonify({
        "ok": True,
        "authenticated": authenticated,
        "email": email,
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

@bp.get("/csrf")
def csrf_get_alias():
    # Provide CSRF token at legacy path expected by some frontends: /api/v1/auth/csrf
    import secrets
    from flask import jsonify, session
    token = session.get("_csrf_token") or secrets.token_hex(16)
    session["_csrf_token"] = token
    resp = jsonify({"ok": True, "csrf": token})
    # Mirror header used elsewhere
    resp.headers["X-CSRF-Token"] = token
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200
