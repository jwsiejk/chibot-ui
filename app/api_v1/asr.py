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


def _extract_session_id(payload: dict[str, object] | None = None) -> str:
    payload = payload or {}
    sid = (
        payload.get("session_id")
        or payload.get("sid")
        or request.args.get("session_id")
        or request.args.get("sid")
        or ""
    )
    try:
        return str(sid).strip()
    except Exception:
        return ""


def _client_session_response(payload: dict[str, object] | None = None):
    if not _require_user():
        return jsonify({"ok": False, "error": "auth_required"}), 401

    sid = _extract_session_id(payload)

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


@bp.get("/client-session")
def client_session_get():
    return _client_session_response()


@bp.post("/client-session")
def client_session_post():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return _client_session_response(payload)

