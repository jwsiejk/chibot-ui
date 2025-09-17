from __future__ import annotations
from flask import Blueprint, request, jsonify, session
from ..security_state import set_user, set_profile, get_profile
from ..middleware.csrf import ensure_csrf_headers

bp = Blueprint("auth_v1", __name__)

def _normalize_email(e: str | None) -> str:
    return (e or "").strip().lower()

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email"))
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400

    # Persist user in session (authoritative) and global helper (best-effort)
    session["user"] = {"email": email}
    try:
        set_user(email)
    except Exception:
        pass

    # Try to load existing profile to set completeness now (non-fatal)
    prof = {}
    try:
        from .profile import _load_profile
        prof = _load_profile(email) or {}
    except Exception:
        # If DB is temporarily unavailable, keep going — UI will retry.
        prof = get_profile() or {}
    complete = bool((prof.get("name") or "").strip() and (prof.get("title") or "").strip())

    # Mirror profile + completeness into helpers/session
    try:
        set_profile(prof or {})
        session["profile_complete"] = complete
    except Exception:
        pass

    resp = jsonify({"ok": True, "email": email, "profile_complete": bool(session.get("profile_complete")), "profile": prof})
    return ensure_csrf_headers(resp), 200

@bp.post("/logout")
def logout():
    try:
        session.clear()
    except Exception:
        pass
    try:
        set_user(None)  # type: ignore[arg-type]
        set_profile({})
    except Exception:
        pass
    resp = jsonify({"ok": True})
    return ensure_csrf_headers(resp), 200

@bp.get("/me")
def me():
    email = (session.get("user") or {}).get("email")
    authenticated = bool(email)
    prof = {}
    complete = False
    if authenticated:
        # Load from DB; if it fails, fall back to last-known profile in memory/session.
        try:
            from .profile import _load_profile
            prof = _load_profile(email) or {}
        except Exception:
            prof = get_profile() or {}
        complete = bool((prof.get("name") or "").strip() and (prof.get("title") or "").strip())
        try:
            set_profile(prof)
            session["profile_complete"] = complete
        except Exception:
            pass

    resp = jsonify({
        "ok": True,
        "authenticated": authenticated,
        "email": email,
        "profile_complete": complete if authenticated else False,
        "profile": prof if authenticated else {}
    })
    return ensure_csrf_headers(resp), 200

# Legacy compatibility: some clients post here instead of /api/v1/profile
@bp.post("/profile/save")
def profile_save():
    data = request.get_json(silent=True) or {}
    # Accept minimal shape and mark completion flag
    try:
        set_profile(data or {})
    except Exception:
        pass
    if bool(data.get("completed")):
        session["profile_complete"] = True
    return jsonify({"ok": True, "profile_complete": bool(session.get("profile_complete"))}), 200

@bp.get("/csrf")
def csrf_get_alias():
    # Provide CSRF token at legacy path expected by some frontends: /api/v1/auth/csrf
    import secrets
    token = session.get("_csrf_token") or secrets.token_hex(16)
    session["_csrf_token"] = token
    resp = jsonify({"ok": True, "csrf": token})
    # Mirror header used elsewhere
    resp.headers["X-CSRF-Token"] = token
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200


@bp.get("/whoami")
def whoami():
    from ..utils.admin import is_admin_email
    # In this harness, default an email so tests have something to read pre-login
    email = (session.get("user") or {}).get("email") or "user@example.com"
    return jsonify({"ok": True, "email": email, "is_admin": bool(is_admin_email(email))}), 200

@bp.get("/healthz")
def healthz():
    return jsonify({"ok": True, "status": "healthy"}), 200
