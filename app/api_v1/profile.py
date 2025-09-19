from __future__ import annotations
from flask import Blueprint, request, jsonify
from ..db import persist_enabled
from ..middleware.csrf import ensure_csrf_headers
from ..security_state import get_user, set_profile

bp = Blueprint("profile_v1", __name__)

def _empty(email: str):
    return {"email": email or "", "name": "", "title": "", "region": "", "profile_complete": False}

def _load_profile(email: str) -> dict:
    if persist_enabled():
        try:
            from ..dal.neon_pg import load_profile
            prof = load_profile(email) or {}
        except Exception:
            prof = set_profile.__self__._CURRENT.get("profile", {}) if hasattr(set_profile, "__self__") else {}
    else:
        from ..security_state import get_profile
        prof = get_profile() or {}
    # Normalize + compute completion
    prof = {
        "email": (prof.get("email") or email or "").strip(),
        "name": (prof.get("name") or "").strip(),
        "title": (prof.get("title") or "").strip(),
        "region": (prof.get("region") or "").strip(),
    }
    prof["profile_complete"] = bool(prof["name"] and prof["title"])
    return prof

def _save_profile(email: str, data: dict) -> dict:
    # Persist or fallback to memory
    prof = {
        "email": email.strip(),
        "name": (data.get("name") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "region": (data.get("region") or "").strip(),
    }
    if persist_enabled():
        try:
            from ..dal.neon_pg import save_profile
            save_profile(email, prof)
        except Exception:
            from ..security_state import set_profile
            set_profile(prof)
    else:
        from ..security_state import set_profile
        set_profile(prof)
    return _load_profile(email)

@bp.get("/get")
def get_profile():
    email = get_user() or "user@example.com"
    out = _load_profile(email)
    resp = jsonify({"ok": True, "exists": bool(out.get("name") or out.get("title")), "profile": out})
    return ensure_csrf_headers(resp), 200

@bp.post("/save")
def save_profile():
    email = get_user() or "user@example.com"
    data = request.get_json(silent=True) or {}
    out = _save_profile(email, data)
    resp = jsonify({"ok": True, "profile": out})
    return ensure_csrf_headers(resp), 200
