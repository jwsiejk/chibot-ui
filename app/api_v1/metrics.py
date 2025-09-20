from __future__ import annotations
from flask import Blueprint, jsonify, request, session, abort
import os
from ..metrics import ws_metrics
from ..utils.admin import is_admin_email
from ..security_state import get_user

bp = Blueprint("metrics", __name__)

def _require_admin() -> None:
    email = (session.get("user") or {}).get("email") or request.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)

@bp.get("/metrics")
def metrics():
    _require_admin()
    snap = ws_metrics.snapshot()
    return jsonify({
        "ok": True,
        "ws": {
            "total_fails": snap["total_fails"],
            "overlimit_fails": snap["overlimit_fails"],
            "ip_buckets": snap["ips"],
            "process_start_ts": snap["process_start_ts"],
            "now": snap["now"],
            "fail_limit": int(os.getenv("WS_FAIL_LIMIT","10")),
            "fail_window_sec": float(os.getenv("WS_FAIL_WINDOW_SEC","60")),
            "bearer_only": os.getenv("WS_BEARER_ONLY","1"),
            "token_required": os.getenv("WS_TOKEN_REQUIRED","1"),
        }
    }), 200
