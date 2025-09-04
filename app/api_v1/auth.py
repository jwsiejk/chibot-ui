from flask import Blueprint, jsonify, request
from ..security_state import issue_csrf, set_user

bp = Blueprint("auth", __name__)
@bp.get("/csrf")
def csrf(): return jsonify({"ok": True, "csrf": issue_csrf()})
@bp.post("/login")
def login():
    email=(request.get_json(silent=True) or {}).get("email","user@example.com").strip().lower()
    set_user(email); return jsonify({"ok": True, "email": email})
@bp.post("/logout")
def logout(): set_user(None); return jsonify({"ok": True})
