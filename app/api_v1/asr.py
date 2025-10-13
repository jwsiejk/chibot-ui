from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from ..security_state import get_user
from ..services.streaming_asr.deepgram_client import (
    build_client_session_descriptor,
)


bp = Blueprint("asr_v1", __name__, url_prefix="/api/v1/asr")


def _require_user() -> str | None:
    email = (session.get("user") or {}).get("email") or (get_user() or "")
    return email or None


@bp.get("/client-session")
def client_session():
    if not _require_user():
        return jsonify({"ok": False, "error": "auth_required"}), 401

    sid = (request.args.get("session_id") or request.args.get("sid") or "").strip()

    overrides: dict[str, object] = {
        "session_id": sid or None,
        "_transport": {
            "containerized_opus": True,
            "container": "webm",
            "codec": "opus",
        },
    }

    descriptor = build_client_session_descriptor(overrides)

    return (
        jsonify(
            {
                "ok": True,
                "session": descriptor,
            }
        ),
        200,
    )

