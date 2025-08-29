# routes/email_api.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from utils.call_log import call_log

try:
    from services.email_service import send_email
except Exception:
    def send_email(*_a, **_k):  # type: ignore
        return False

email_bp = Blueprint("email_bp", __name__, url_prefix="/api")

@email_bp.post("/email/send")
def email_send():
    data = request.get_json(silent=True) or {}
    to = data.get("to") or data.get("recipients") or ""
    # Allow both string and list input
    if isinstance(to, str):
        to = [t.strip() for t in to.split(",") if t.strip()]
    elif isinstance(to, (list, tuple)):
        to = [str(x).strip() for x in to if str(x).strip()]
    else:
        to = []
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    html = (data.get("html") or "").strip() or None
    if not to or not subject:
        return jsonify({"ok": False, "error": "to_and_subject_required"}), 400
    ok = False
    try:
        ok = bool(send_email(to, subject, html=html, text=body))
    except Exception as e:
        call_log.add("email", "send_error", error=str(e))
        ok = False
    if ok:
        call_log.add("email", "send_ok", to=to, subject=subject)
        return jsonify({"ok": True})
    else:
        call_log.add("email", "send_fail", to=to, subject=subject)
        return jsonify({"ok": False, "error": "send_failed"}), 200
