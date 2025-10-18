from __future__ import annotations

import json
from typing import Iterable, List, Optional

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    request,
    session as flask_session,
    stream_with_context,
)

from app.flow import FlowStore
from app.flow.catalog import FLOW_EVENT_CATALOG
from app.security_state import get_user
from app.utils.admin import is_admin_email

bp = Blueprint("flow", __name__)


def _normalize_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _require_admin() -> None:
    email = (
        (flask_session.get("user") or {}).get("email")
        or flask_session.get("email")
        or request.headers.get("X-User-Email")
        or (get_user() or "")
    )
    if not is_admin_email((email or "").strip().lower()):
        abort(403)


def _parse_int(value: Optional[str], *, name: str, minimum: Optional[int] = None) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        abort(400, description=f"Invalid {name}")
    if minimum is not None and parsed < minimum:
        abort(400, description=f"{name} must be >= {minimum}")
    return parsed


def _parse_levels(raw: Optional[str]) -> Iterable[str]:
    if raw is None:
        return ("flow", "transition")
    levels: List[str] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if piece not in levels:
            levels.append(piece)
    return levels


def _parse_bool(value: Optional[str], *, default: bool = True) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


@bp.get("/flow/catalog")
def flow_catalog():
    _require_admin()
    return jsonify({"catalog": FLOW_EVENT_CATALOG})


@bp.get("/flow/sessions")
def flow_sessions():
    _require_admin()

    search_value = _normalize_str(request.args.get("q") or request.args.get("query"))
    limit_value = _parse_int(request.args.get("limit"), name="limit", minimum=0)

    store = FlowStore()
    limit = limit_value if limit_value is not None else 50
    sessions = store.sessions(query=search_value, limit=limit)
    return jsonify({"sessions": sessions})


@bp.get("/flow/trace")
def flow_trace():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    if not session_id:
        abort(400, description="session_id is required")

    since_value = _parse_int(request.args.get("since_ms"), name="since_ms", minimum=0)
    limit_value = _parse_int(request.args.get("limit"), name="limit", minimum=1)
    if limit_value is None:
        limit_value = 200
    limit_value = min(limit_value, 1000)

    levels = _parse_levels(request.args.get("levels"))
    expand = request.args.get("expand") or "flow"

    store = FlowStore()
    payload = store.list(
        session_id=session_id,
        since_ms=since_value,
        limit=limit_value,
        levels=levels,
        expand=expand,
    )
    return jsonify(payload)


@bp.get("/flow/event")
def flow_event():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    event_id = _normalize_str(request.args.get("id"))
    if not session_id or not event_id:
        abort(400, description="session_id and id are required")

    store = FlowStore()
    event = store.get(session_id, event_id)
    if not event:
        abort(404)
    return jsonify(event)


@bp.get("/flow/export.ndjson")
def flow_export_ndjson():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    if not session_id:
        abort(400, description="session_id is required")

    since_value = _parse_int(request.args.get("since_ms"), name="since_ms", minimum=0)
    levels = _parse_levels(request.args.get("levels"))
    redacted = _parse_bool(request.args.get("redacted"), default=True)

    store = FlowStore()

    def _generate() -> Iterable[str]:
        cursor = since_value if since_value is not None else 0
        while True:
            chunk = store.list(
                session_id=session_id,
                since_ms=cursor,
                limit=500,
                levels=levels,
                expand="all",
            )
            events = chunk.get("events", [])
            if not events:
                break
            for event in events:
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            next_since = chunk.get("next_since_ms")
            if not isinstance(next_since, int) or next_since <= cursor:
                break
            cursor = next_since

    response = Response(stream_with_context(_generate()), mimetype="application/x-ndjson")
    response.headers["X-Flow-Redacted"] = "1" if redacted else "0"
    return response


__all__ = ["bp"]
