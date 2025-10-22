"""ASGI gateway mounting the chat.v2 adapter and operational HTTP probes."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

from app.admin.flow_api import handle_flow_trace, handle_flow_zip
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Response:
    """Immutable container describing a serialized HTTP response."""

    status: int
    body: bytes
    headers: tuple[tuple[bytes, bytes], ...]


def json_response(*, status: int = 200, **payload: Any) -> Response:
    """Serialize a payload to JSON using a compact representation."""

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = (
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    )
    return Response(status=status, body=body, headers=headers)


WS_ROUTE = "/ws/v2/chat"
HEALTH_ROUTE = "/api/v1/health"
LIVE_ROUTE = "/api/v1/live"
READY_ROUTE = "/api/v1/ready"
INFO_ROUTE = "/api/v1/info"
EXPORT_ROOT = Path("exports")
_ADMIN_FLOW_PREFIX = "/api/v1/admin/flow/"
_TRACE_SEGMENT = "trace"
_ZIP_SEGMENT = "zip"


HttpHandler = Callable[[dict, Callable[[], Awaitable[dict]]], Awaitable[Response]]

_adapter: Optional[ChatV2Adapter] = None


async def app(scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
    """Dispatch incoming ASGI scopes to HTTP handlers or the chat adapter."""
    scope_type = scope.get("type")
    path = (scope.get("root_path") or "") + scope.get("path", "")

    if scope_type == "websocket":
        if path == WS_ROUTE:
            allowed, blocked_origin = _validate_ws_origin(scope)
            if not allowed:
                await _reject_origin(scope, blocked_origin, receive, send)
                return
            await _get_adapter()(scope, receive, send)
        else:
            await _reject_websocket(receive, send)
        return

    if scope_type == "http":
        handler = _HTTP_ROUTES.get(path)
        if handler is None:
            handler = _resolve_admin_route(path)
        if handler is None:
            await _drain_request_body(receive)
            await _send_response(send, json_response(status=404, error="not_found"))
        else:
            response = await handler(scope, receive)
            await _send_response(send, response)
        return

    raise RuntimeError(f"Unsupported ASGI scope type: {scope_type}")


async def _handle_health(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Return a static JSON document for health checks."""
    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    return json_response(ok=True, engine="v2", ws_subprotocol=CHAT_V2_SUBPROTOCOL)


async def _handle_live(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Expose a lightweight pulse indicating the event loop is responsive."""
    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    ts_ms = int(time.time() * 1000)
    return json_response(ok=True, ts_ms=ts_ms)


async def _handle_ready(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Report readiness by verifying the export directory is writable."""
    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    try:
        EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=EXPORT_ROOT, prefix=".ready-", delete=True) as tmp:
            tmp.write(b"ok")
            tmp.flush()
        logger.debug("Readiness probe succeeded for %s", EXPORT_ROOT)
        return json_response(ok=True, export_path=str(EXPORT_ROOT))
    except OSError as exc:
        logger.warning("Readiness probe failed: %s", exc)
        return json_response(
            status=503,
            ok=False,
            reason="export_path_unwritable",
            export_path=str(EXPORT_ROOT),
        )


async def _handle_info(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Return build metadata derived from environment configuration."""
    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)

    version = os.getenv("APP_VERSION") or "0.0.0+dev"
    raw_sha = os.getenv("GIT_SHA") or ""
    git_sha = raw_sha[:7] if raw_sha else "unknown"
    built_at = os.getenv("BUILD_TIME") or datetime.now(timezone.utc).isoformat()

    payload = {"version": version, "git_sha": git_sha, "built_at": built_at}
    return json_response(**payload)


async def _drain_request_body(receive: Callable[[], Awaitable[dict]]) -> None:
    """Consume and discard the HTTP request body if present."""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body"):
            break


async def _reject_websocket(receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
    """Politely refuse WebSocket connections on unsupported routes."""
    message = await receive()
    if message.get("type") == "websocket.connect":
        await send({"type": "websocket.close", "code": 1000})


async def _send_response(send: Callable[[dict], Awaitable[None]], response: Response) -> None:
    """Emit a prepared HTTP response through the ASGI channel."""

    headers = list(response.headers)
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": response.body, "more_body": False})


def _method_is_get(scope: dict) -> bool:
    """Return True when the incoming HTTP scope uses the GET method."""

    return scope.get("method", "GET").upper() == "GET"


__all__ = ["app"]


def _get_adapter() -> ChatV2Adapter:
    """Return a lazily instantiated ChatV2Adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = ChatV2Adapter()
    return _adapter


async def _reject_origin(
    scope: dict,
    origin: Optional[str],
    receive: Callable[[], Awaitable[dict]],
    send: Callable[[dict], Awaitable[None]],
) -> None:
    """Reject a WebSocket handshake because the Origin is not allowed."""

    message = await receive()
    if message.get("type") != "websocket.connect":
        return

    logger.warning("Blocked WebSocket origin", extra={"origin": origin, "path": scope.get("path")})
    payload: Dict[str, Any] = {"code": "origin_blocked", "error": "origin_blocked"}
    if origin:
        payload["origin"] = origin
    await _send_response(send, json_response(status=403, **payload))


def _decode_header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    for header, value in headers:
        if header == name:
            try:
                return value.decode("latin1").strip()
            except UnicodeDecodeError:  # pragma: no cover - defensive
                return None
    return None


def _resolve_origin_policy() -> "_OriginPolicy":
    raw = os.getenv("ASKCHIP_WS_ALLOWED_ORIGINS")
    if raw is not None:
        entries = tuple(filter(None, (_normalize_origin(item) for item in raw.split(","))))
        return _OriginPolicy(mode="explicit", values=entries)

    env = (os.getenv("ASKCHIP_ENV") or "development").strip().lower()
    if env in {"production", "prod"}:
        return _OriginPolicy(mode="explicit", values=())

    return _OriginPolicy(mode="localhost", values=("localhost", "127.0.0.1"))


def _is_origin_allowed(origin: str, policy: "_OriginPolicy") -> bool:
    if policy.mode == "explicit":
        normalized = _normalize_origin(origin)
        if normalized is None or not policy.values:
            return normalized in policy.values
        return normalized in policy.values

    if policy.mode == "localhost":
        host = _extract_hostname(origin)
        if host is None:
            return False
        return host in policy.values

    return False


def _normalize_origin(value: str) -> Optional[str]:
    raw = value.strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        return f"{scheme}://{netloc}"

    lowered = raw.lower()
    if lowered == "*":
        return None
    return lowered


def _extract_hostname(value: str) -> Optional[str]:
    parsed = urlparse(value)
    if not parsed.hostname:
        return None
    return parsed.hostname.lower()


@dataclass(frozen=True)
class _OriginPolicy:
    mode: str
    values: tuple[str, ...]


def _validate_ws_origin(scope: dict) -> tuple[bool, Optional[str]]:
    """Validate the Origin header for incoming WebSocket handshakes."""

    origin = _decode_header(scope.get("headers", ()), b"origin")
    if origin is None:
        return True, None

    policy = _resolve_origin_policy()
    if _is_origin_allowed(origin, policy):
        return True, None

    return False, origin


_HTTP_ROUTES: Dict[str, HttpHandler] = {
    HEALTH_ROUTE: _handle_health,
    LIVE_ROUTE: _handle_live,
    READY_ROUTE: _handle_ready,
    INFO_ROUTE: _handle_info,
}


def _resolve_admin_route(path: str) -> Optional[HttpHandler]:
    if not path.startswith(_ADMIN_FLOW_PREFIX):
        return None

    suffix = path[len(_ADMIN_FLOW_PREFIX) :]
    if not suffix:
        return None

    segments = suffix.split("/")
    if len(segments) != 2:
        return None

    sid, action = segments
    if not sid:
        return None

    handler: Optional[HttpHandler]
    if action == _TRACE_SEGMENT:
        handler = partial(handle_flow_trace, sid=sid)
    elif action == _ZIP_SEGMENT:
        handler = partial(handle_flow_zip, sid=sid)
    else:
        handler = None

    if handler is None:
        return None

    if _admin_cors_enabled():
        return partial(_apply_admin_cors, handler)

    return handler


async def _apply_admin_cors(
    handler: HttpHandler,
    scope: dict,
    receive: Callable[[], Awaitable[dict]],
) -> Response:
    response = await handler(scope, receive)
    allow_origin = _resolve_admin_cors_origin(scope)
    if allow_origin is None:
        return response

    encoded_origin = _encode_header_value(allow_origin)
    if encoded_origin is None:
        return response

    headers = tuple(response.headers) + (
        (b"access-control-allow-origin", encoded_origin),
        (b"access-control-allow-methods", b"GET"),
    )
    return Response(status=response.status, body=response.body, headers=headers)


def _admin_cors_enabled() -> bool:
    env = (os.getenv("ASKCHIP_ENV") or "").strip().lower()
    return env in {"dev", "staging"}


def _resolve_admin_cors_origin(scope: dict) -> Optional[str]:
    allowed = _parse_admin_cors_origins()
    if not allowed:
        return None

    if "*" in allowed:
        return "*"

    origin = _decode_header(scope.get("headers", ()), b"origin")
    if origin is None:
        return None

    normalized = origin.strip()
    for entry in allowed:
        if normalized == entry:
            return normalized
    return None


def _parse_admin_cors_origins() -> tuple[str, ...]:
    raw = os.getenv("ASKCHIP_ADMIN_CORS_ORIGINS")
    if not raw:
        return ()

    entries = []
    for item in raw.split(","):
        value = item.strip()
        if value:
            entries.append(value)
    return tuple(entries)


def _encode_header_value(value: str) -> Optional[bytes]:
    try:
        return value.encode("latin1")
    except UnicodeEncodeError:  # pragma: no cover - defensive
        return None
