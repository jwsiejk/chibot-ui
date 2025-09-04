from flask import Blueprint, jsonify, request
from ..db import db
from ..security_state import get_user
bp = Blueprint("profile", __name__)
@bp.get("")
def get_profile():
    email = get_user(); prof = db.memory['profiles'].get(email)
    return jsonify({"ok": True, "has_profile": bool(prof), "profile": prof or None})
@bp.post("")
def set_profile():
    email = get_user(); data=request.get_json(silent=True) or {}
    prof = {"name": data.get("name","User"), "title": data.get("title","Engineer"), "region": data.get("region","NA")}
    db.memory['profiles'][email]=prof; return jsonify({"ok": True, "profile": prof})
