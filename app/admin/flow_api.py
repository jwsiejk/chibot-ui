"""Admin HTTP endpoints exposing flow trace streams and ZIP archives."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterable, Optional, Sequence
from urllib.parse import parse_qs

from app.admin.flow_zip import build_flow_zip
from app.security.auth import authorize_admin


EXPORT_ROOT = Path("exports")
_EVENTS_FILENAME = "events.ndjson"
_ARCHIVE_FILENAME = "flow.zip"
_CONTENT_TYPE_NDJSON = b"application/x-ndjson"
_CONTENT_TYPE_ZIP = b"application/zip"


async def handle_flow_trace(
    scope: dict, receive: Callable[[], Awaitable[dict]], *, sid: str
):
    """Stream filtered redacted events for the given session identifier."""

    from app.asgi_gateway import Response, json_response  # local import to avoid cycles

    if _method(scope) != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_body(receive)

    headers = _decode_headers(scope.get("headers", ()))
    authorized, reason, _claims = authorize_admin(headers, scope)
    if not authorized:
        return json_response(status=401, error="unauthorized", detail=reason)

    try:
        requested_types, since_ms, limit = _parse_trace_filters(scope.get("query_string", b""))
    except ValueError as exc:
        return json_response(status=400, error="invalid_query", detail=str(exc))

    events_path = EXPORT_ROOT / sid / _EVENTS_FILENAME
    if not events_path.is_file():
        return json_response(status=404, error="not_found")

    try:
        body_bytes = _filter_events(events_path, requested_types, since_ms, limit)
    except ValueError as exc:
        return json_response(status=400, error="invalid_event", detail=str(exc))

    headers = (
        (b"content-type", _CONTENT_TYPE_NDJSON),
        (b"content-length", str(len(body_bytes)).encode("ascii")),
    )
    return Response(status=200, body=body_bytes, headers=headers)


async def handle_flow_zip(
    scope: dict, receive: Callable[[], Awaitable[dict]], *, sid: str
):
    """Return the packaged flow.zip archive for the requested session."""

    from app.asgi_gateway import Response, json_response  # local import to avoid cycles

    if _method(scope) != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_body(receive)

    headers = _decode_headers(scope.get("headers", ()))
    authorized, reason, _claims = authorize_admin(headers, scope)
    if not authorized:
        return json_response(status=401, error="unauthorized", detail=reason)

    session_dir = EXPORT_ROOT / sid
    if not session_dir.exists():
        return json_response(status=404, error="not_found")

    archive_path = session_dir / _ARCHIVE_FILENAME
    if not archive_path.is_file():
        try:
            archive_path = build_flow_zip(sid, root=EXPORT_ROOT)
        except FileNotFoundError:
            return json_response(status=404, error="not_found")

    archive_bytes = archive_path.read_bytes()
    headers = (
        (b"content-type", _CONTENT_TYPE_ZIP),
        (b"content-length", str(len(archive_bytes)).encode("ascii")),
    )
    return Response(status=200, body=archive_bytes, headers=headers)


def _method(scope: dict) -> str:
    return scope.get("method", "GET").upper()


def _decode_headers(raw_headers: Iterable[Sequence[bytes]]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for name, value in raw_headers:
        headers[name.decode("latin1").lower()] = value.decode("latin1")
    return headers


async def _drain_body(receive: Callable[[], Awaitable[dict]]) -> None:
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body", False):
            break


def _parse_trace_filters(query: bytes) -> tuple[Optional[set[str]], Optional[int], Optional[int]]:
    qs = query.decode("utf-8", errors="ignore")
    parsed = parse_qs(qs, keep_blank_values=False)

    requested_types: Optional[set[str]] = None
    if "type" in parsed and parsed["type"]:
        types_raw = parsed["type"][0].split(",")
        requested_types = {value for value in (t.strip() for t in types_raw) if value}
        if not requested_types:
            requested_types = None

    since_ms: Optional[int] = None
    if "since_ms" in parsed and parsed["since_ms"]:
        since_raw = parsed["since_ms"][0]
        try:
            since_ms = int(since_raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError("since_ms must be an integer") from exc
        if since_ms < 0:
            raise ValueError("since_ms must be non-negative")

    limit: Optional[int] = None
    if "limit" in parsed and parsed["limit"]:
        limit_raw = parsed["limit"][0]
        try:
            limit = int(limit_raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError("limit must be an integer") from exc
        if limit < 0:
            raise ValueError("limit must be non-negative")

    return requested_types, since_ms, limit


def _filter_events(
    events_path: Path,
    requested_types: Optional[set[str]],
    since_ms: Optional[int],
    limit: Optional[int],
) -> bytes:
    matched = BytesIO()
    remaining = limit if limit is not None else None

    with events_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if remaining is not None and remaining <= 0:
                break

            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ValueError("event payload must be valid JSON") from exc

            if requested_types and event.get("type") not in requested_types:
                continue

            if since_ms is not None:
                ts_value = event.get("ts_ms")
                if not isinstance(ts_value, int):
                    continue
                if ts_value < since_ms:
                    continue

            matched.write(stripped.encode("utf-8"))
            matched.write(b"\n")
            if remaining is not None:
                remaining -= 1

    return matched.getvalue()


__all__ = ["handle_flow_trace", "handle_flow_zip"]
