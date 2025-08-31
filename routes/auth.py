from __future__ import annotations

from flask import Blueprint, request, jsonify, session
try:
    import memory  # optional
except Exception:  # pragma: no cover
    memory = None  # type: ignore

auth_bp = Blueprint("auth_bp", __name__, url_prefix="/api")

@auth_bp.post("/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({ "ok": False, "error": "missing_email" }), 400
    session["email"] = email
    # Touch user profile storage if available
    try:
        if memory is not None:
            _ = getattr(memory, "get_user", lambda e: {})(email)
    except Exception:
        pass
    return jsonify({ "ok": True })

@auth_bp.post("/logout")
def api_logout():
    session.clear()
    return jsonify({ "ok": True })
