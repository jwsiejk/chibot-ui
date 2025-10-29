"""Admin HTTP endpoints exposing flow trace streams and ZIP archives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import parse_qs

from app.admin.flow_zip import build_flow_zip


EXPORT_ROOT = Path("exports")
_EVENTS_FILENAME = "events.ndjson"
_ARCHIVE_FILENAME = "flow.zip"
_CONTENT_TYPE_NDJSON = b"application/x-ndjson"
_CONTENT_TYPE_ZIP = b"application/zip"

_TYPE_PREFIX_ALIASES: dict[str, tuple[str, ...]] = {
    "EVT_DIAG_HUD": ("EVT_HUD_", "EVT_CLIENT_"),
    "EVT_DIAG_FIRST_AUDIO_FRAME": ("EVT_AG_",),
    "EVT_DIAG_NO_AUDIO_FROM_CLIENT": ("EVT_AG_",),
}


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: tuple[tuple[bytes, bytes], ...]


def json_response(*, status: int = 200, **payload: object) -> Response:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = (
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    )
    return Response(status=status, body=body, headers=headers)


async def handle_flow_trace(
    scope: dict, receive: Callable[[], Awaitable[dict]], *, sid: str
):
    """Stream filtered redacted events for the given session identifier."""

    if _method(scope) != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_body(receive)

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

    if _method(scope) != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_body(receive)

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


async def handle_flow_sessions(
    scope: dict, receive: Callable[[], Awaitable[dict]]
):
    """Enumerate flow sessions by inspecting export manifests."""

    if _method(scope) != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_body(receive)

    qs = scope.get("query_string") or b""
    try:
        parsed = parse_qs(qs.decode("latin1", "ignore"))
    except Exception:
        parsed = {}

    def _truthy(value: Optional[str]) -> bool:
        if not value:
            return False
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "y", "on"}

    open_only = _truthy((parsed.get("open") or [None])[0])
    prefix = (parsed.get("prefix") or [None])[0]
    if prefix:
        prefix = prefix.strip() or None

    limit = 50
    raw_limit = (parsed.get("limit") or [None])[0]
    if raw_limit:
        try:
            limit = max(1, min(500, int(raw_limit)))
        except Exception:
            limit = 50

    sessions: List[Dict] = []
    try:
        if EXPORT_ROOT.exists():
            for entry in EXPORT_ROOT.iterdir():
                if not entry.is_dir():
                    continue
                sid = entry.name
                if prefix and not sid.startswith(prefix):
                    continue

                manifest_path = entry / "manifest.json"
                if not manifest_path.exists():
                    continue

                try:
                    manifest = json.loads(manifest_path.read_text("utf-8"))
                except Exception:
                    continue

                is_open = bool(manifest.get("open", False))
                if open_only and not is_open:
                    continue

                sessions.append(
                    {
                        "sid": manifest.get("sid", sid),
                        "open": is_open,
                        "started_ms": manifest.get("started_ms"),
                        "ended_ms": manifest.get("ended_ms"),
                        "events_written": manifest.get("events_written", 0),
                        "by_type": manifest.get("by_type") or {},
                    }
                )
    except Exception:
        sessions = []

    def _sort_key(item: Dict):
        ts_value = item.get("ended_ms") or item.get("started_ms") or 0
        try:
            ts_int = int(ts_value)
        except Exception:
            ts_int = 0
        return (not item.get("open", False), -ts_int)

    sessions.sort(key=_sort_key)
    if limit:
        sessions = sessions[:limit]

    return json_response(ok=True, count=len(sessions), sessions=sessions)


def _method(scope: dict) -> str:
    return scope.get("method", "GET").upper()


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

            if requested_types and not _type_filter_matches(event, requested_types):
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


def _type_filter_matches(event: object, requested_types: set[str]) -> bool:
    if not isinstance(event, dict):
        return False

    event_type = event.get("type")
    if not isinstance(event_type, str):
        return False

    for token in requested_types:
        if _token_matches_event(event, event_type, token):
            return True

    return False


def _token_matches_event(event: dict, event_type: str, token: str) -> bool:
    base, sep, remainder = token.partition(":")
    if sep:
        if "=" in remainder:
            path_expr, _, expected_value = remainder.partition("=")
        else:
            path_expr = "step"
            expected_value = remainder

        if event_type != base:
            return False

        meta = event.get("meta")
        if not isinstance(meta, dict):
            return False

        path = [segment for segment in path_expr.split(".") if segment]
        current: Any = meta
        for segment in path:
            if not isinstance(current, dict) or segment not in current:
                return False
            current = current.get(segment)

        return current == expected_value

    if event_type == token:
        return True

    prefixes = _TYPE_PREFIX_ALIASES.get(token)
    if not prefixes:
        return False

    for prefix in prefixes:
        if event_type.startswith(prefix):
            return True

    return False


__all__ = ["handle_flow_trace", "handle_flow_zip", "handle_flow_sessions"]
