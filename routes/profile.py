# routes/profile.py
from flask import Blueprint, request, jsonify, session, current_app
from utils.call_log import call_log

profile_bp = Blueprint("profile_bp", __name__)

def _current_email():
    return (session.get("user", {}) or {}).get("email") or session.get("email")

def _sanitize(s):
    return (s or "").strip()

@profile_bp.route("/profile", methods=["GET"])
def get_profile():
    email = _current_email()
    if not email:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    # Prefer session (fast, always available)
    p = session.get("profile") or {}
    # Shape the response
    resp = {
        "ok": True,
        "email": p.get("email") or email,
        "name": p.get("name") or "",
        "title": p.get("title") or "",
        "region": p.get("region") or "",
    }
    call_log.add("profile:get", "ok", email=resp["email"])
    return jsonify(resp)

@profile_bp.route("/profile", methods=["POST"])
def save_profile():
    data = request.get_json(silent=True) or {}
    email = _sanitize(data.get("email") or _current_email())
    if not email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    profile = {
        "email": email,
        "name": _sanitize(data.get("name")),
        "title": _sanitize(data.get("title")),
        "region": _sanitize(data.get("region")),
    }
    # Always persist in session as a safe fallback
    session["profile"] = profile
    session["email"] = email
    call_log.add("profile:save", "session", **profile)

    # Optional: attempt DB write if a helper is available (non-fatal if missing)
    try:
        # Lazy import to avoid hard dependency
        from utils.db import upsert_profile  # expected helper if present
        upsert_profile(profile)
        call_log.add("profile:save", "db_ok", email=email)
    except Exception as e:
        # Only log; never fail the request
        current_app.logger.info("profile db save skipped/failed: %s", e)
        call_log.add("profile:save", "db_skip", error=str(e))

    return jsonify({"ok": True, "profile": profile})
