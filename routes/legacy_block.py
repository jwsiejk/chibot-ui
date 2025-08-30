
# routes/legacy_block.py
from flask import Blueprint, jsonify, request
from utils.call_log import call_log

legacy_block_bp = Blueprint("legacy_block", __name__, url_prefix="/api")

def _payload():
    return {
        "ok": False,
        "error": "legacy_endpoint_removed",
        "message": "This legacy endpoint has been removed. Please update the UI/client to use /api/chat.",
        "migrate_to": "/api/chat",
        "route_version": "2025-08-30"
    }

@legacy_block_bp.route("/conversation", methods=["GET","POST","OPTIONS"])
@legacy_block_bp.route("/orchestrator", methods=["GET","POST","OPTIONS"])
def legacy_block_route():
    # Always log and return 410 Gone to make the failure visible and actionable.
    call_log.add("legacy", "blocked", path=request.path, method=request.method)
    return jsonify(_payload()), 410
