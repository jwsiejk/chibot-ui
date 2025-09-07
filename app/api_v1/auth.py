from flask import Blueprint, jsonify, request
from ..security_state import set_user
from ..middleware.csrf import _issue_token as issue_csrf  # session-based

bp = Blueprint("auth", __name__)
@bp.get("/csrf")
def csrf():
    token = issue_csrf()
    resp = jsonify({"ok": True, "csrf": token})
    resp.headers["X-CSRF-Token"] = token
    resp.headers["Cache-Control"] = "no-store"
    return resp
@bp.post("/login")
def login():
    email=(request.get_json(silent=True) or {}).get("email","user@example.com").strip().lower()
    set_user(email); return jsonify({"ok": True, "email": email})
@bp.post("/logout")
def logout(): set_user(None); return jsonify({"ok": True})
