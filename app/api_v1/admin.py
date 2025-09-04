from __future__ import annotations
from flask import Blueprint, jsonify, Response

bp = Blueprint("admin_v1", __name__)

@bp.get("/admin/logs")
def admin_logs_sse():
    # SSE stub: Phase 5 will stream JSON lines.
    return Response("retry: 3000\n\n", mimetype="text/event-stream")
