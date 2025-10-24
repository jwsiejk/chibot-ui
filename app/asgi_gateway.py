"""ASGI gateway mounting the chat.v2 adapter and operational HTTP probes."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

from app.telemetry import bus as telemetry_bus
from app.telemetry.exporter import FileExporter
from app.voice_v2.engine import EngineV2
from app.voice_v2.tts_runtime import TTSRuntime
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
ROOT_ROUTE = "/"
FAVICON_ROUTE = "/favicon.ico"
STATIC_ROUTE_PREFIX = "/static/"
EXPORT_ROOT = Path("exports")
BASE_DIR = Path(__file__).resolve().parent
STATIC_ROOT = BASE_DIR / "static"
TEMPLATES_ROOT = BASE_DIR / "templates"
INDEX_PATH = TEMPLATES_ROOT / "index.html"
FAVICON_PATH = STATIC_ROOT / "favicon.ico"
_DEFAULT_INDEX_HTML = (
    "<!doctype html><title>AskChip</title><div id='app'></div>".encode("utf-8")
)


HttpHandler = Callable[[dict, Callable[[], Awaitable[dict]]], Awaitable[Response]]

_adapter: Optional[ChatV2Adapter] = None


def _ws_route_matches(scope: dict, expected: str) -> bool:
    """
    Return True when the incoming websocket request targets the expected route,
    robust to deployments mounted under a non-empty ASGI root_path.
    """

    raw_path = scope.get("path", "") or ""
    root_path = scope.get("root_path") or ""
    # Direct match (no prefix)
    if raw_path == expected:
        return True
    # Combined path match (some servers expect root_path + path)
    combined = f"{root_path}{raw_path}" if root_path else raw_path
    if combined == expected:
        return True
    # Prefix-mount: accept if the combined path ends with expected
    return combined.endswith(expected)


async def app(scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
    """Dispatch incoming ASGI scopes to HTTP handlers or the chat adapter."""
    scope_type = scope.get("type")
    path = (scope.get("root_path") or "") + scope.get("path", "")

    if scope_type == "websocket":
        if _ws_route_matches(scope, WS_ROUTE):
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
        if handler is None and path.startswith(STATIC_ROUTE_PREFIX):
            handler = partial(_handle_static, raw_path=path)
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


async def _handle_index(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Serve the primary HTML shell for the single-page application."""

    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    body = _load_index_html()
    return _html_response(body)


async def _handle_favicon(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Serve the site favicon when available."""

    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    response = _build_static_response(FAVICON_PATH)
    if response is None:
        headers = ((b"content-length", b"0"),)
        return Response(status=204, body=b"", headers=headers)
    return response


async def _handle_static(
    scope: dict,
    receive: Callable[[], Awaitable[dict]],
    *,
    raw_path: str,
) -> Response:
    """Serve files from the bundled static asset directory."""

    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    resolved = _resolve_static_path(raw_path)
    if resolved is None:
        return json_response(status=404, error="not_found")
    response = _build_static_response(resolved)
    if response is None:
        return json_response(status=404, error="not_found")
    return response


def _load_index_html() -> bytes:
    try:
        return INDEX_PATH.read_bytes()
    except OSError:
        return _DEFAULT_INDEX_HTML


def _html_response(body: bytes) -> Response:
    headers = (
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    )
    return Response(status=200, body=body, headers=headers)


def _build_static_response(path: Path) -> Optional[Response]:
    try:
        data = path.read_bytes()
    except OSError:
        return None

    content_type, encoding = mimetypes.guess_type(path.name)
    if content_type is None:
        content_type = "application/octet-stream"
    elif content_type.startswith("text/") and "charset=" not in content_type:
        content_type = f"{content_type}; charset=utf-8"

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", content_type.encode("latin1")),
        (b"content-length", str(len(data)).encode("ascii")),
    ]
    if encoding:
        headers.append((b"content-encoding", encoding.encode("latin1")))

    return Response(status=200, body=data, headers=tuple(headers))


def _resolve_static_path(raw_path: str) -> Optional[Path]:
    if not raw_path.startswith(STATIC_ROUTE_PREFIX):
        return None

    relative = raw_path[len(STATIC_ROUTE_PREFIX) :]
    if not relative:
        return None

    target = STATIC_ROOT / relative
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return None

    try:
        resolved.relative_to(STATIC_ROOT)
    except ValueError:
        return None

    if not resolved.is_file():
        return None

    return resolved


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


async def _send_ws_response(send: Callable[[dict], Awaitable[None]], response: Response) -> None:
    """Emit an HTTP response while still in the WebSocket handshake."""

    headers = list(response.headers)
    await send(
        {
            "type": "websocket.http.response.start",
            "status": response.status,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "websocket.http.response.body",
            "body": response.body,
            "more_body": False,
        }
    )


def _method_is_get(scope: dict) -> bool:
    """Return True when the incoming HTTP scope uses the GET method."""

    return scope.get("method", "GET").upper() == "GET"


def _get_adapter() -> ChatV2Adapter:
    """Return a lazily instantiated ChatV2Adapter singleton."""
    global _adapter
    if _adapter is None:
        exporter = FileExporter(EXPORT_ROOT)
        engine = EngineV2(exporter=exporter)
        tts_runtime = TTSRuntime(engine=engine)
        _adapter = ChatV2Adapter(engine=engine, exporter=exporter)
        _adapter.tts_runtime = tts_runtime
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
    await _send_ws_response(send, json_response(status=403, **payload))


def _decode_header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    for header, value in headers:
        if header == name:
            try:
                return value.decode("latin1").strip()
            except UnicodeDecodeError:  # pragma: no cover - defensive
                return None
    return None


def _first_forwarded_value(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    value = _decode_header(headers, name)
    if not value:
        return None

    for part in value.split(","):
        candidate = part.strip()
        if candidate:
            return candidate
    return None


def _parse_forwarded_header(headers: Iterable[tuple[bytes, bytes]]) -> tuple[Optional[str], Optional[str]]:
    raw = _decode_header(headers, b"forwarded")
    if not raw:
        return None, None

    first_element = raw.split(",", 1)[0].strip()
    if not first_element:
        return None, None

    proto: Optional[str] = None
    host: Optional[str] = None
    for part in first_element.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        raw_value = value.strip()
        if not raw_value:
            continue
        if raw_value.startswith('"') and raw_value.endswith('"') and len(raw_value) >= 2:
            raw_value = raw_value[1:-1]
        if key == "proto" and not proto:
            proto = raw_value
        elif key == "host" and not host:
            host = raw_value

    if proto is None and host is None:
        return None, None
    return proto, host


def _derive_scope_origin(scope: dict) -> Optional[str]:
    """Best-effort reconstruction of the request origin from an ASGI scope."""

    headers = scope.get("headers", ())

    forwarded_proto, forwarded_host = _parse_forwarded_header(headers)
    fallback_proto = _first_forwarded_value(headers, b"x-forwarded-proto")
    forwarded_port = _first_forwarded_value(headers, b"x-forwarded-port")
    header_host = forwarded_host or _first_forwarded_value(headers, b"x-forwarded-host")

    raw_scheme = (forwarded_proto or fallback_proto or scope.get("scheme") or "").lower()
    if raw_scheme in {"https", "wss"}:
        scheme = "https"
    else:
        scheme = "http"

    # Security note: Forwarded/X-Forwarded headers are used only to reconstruct the effective
    # origin for same-origin equivalence behind proxies. They do not expand accepted origins
    # beyond same-origin or the explicit allow-list.
    host = header_host or _decode_header(headers, b"host")
    if host:
        normalized_host = host.strip()
        if forwarded_port and ":" not in normalized_host:
            port_candidate = forwarded_port.strip()
            if port_candidate and not _is_default_port(scheme, port_candidate):
                normalized_host = f"{normalized_host}:{port_candidate}"
    else:
        normalized_host = ""

    if not normalized_host:
        server = scope.get("server")
        if isinstance(server, (list, tuple)) and server:
            hostname = (server[0] or "").strip()
            port = server[1] if len(server) > 1 else None
            if hostname:
                normalized_host = hostname
                if port and not _is_default_port(scheme, port):
                    normalized_host = f"{hostname}:{port}"

    normalized_host = normalized_host.strip()
    if not normalized_host:
        return None

    return f"{scheme}://{normalized_host.lower()}"


def _is_default_port(scheme: str, port: Any) -> bool:
    try:
        value = int(port)
    except (TypeError, ValueError):
        return False
    if scheme == "https":
        return value == 443
    if scheme == "http":
        return value == 80
    return False


def _resolve_origin_policy() -> "_OriginPolicy":
    raw = os.getenv("ASKCHIP_WS_ALLOWED_ORIGINS")
    if raw is not None:
        entries = tuple(filter(None, (_normalize_origin(item) for item in raw.split(","))))
        return _OriginPolicy(mode="explicit", values=entries)

    return _OriginPolicy(mode="explicit", values=())


def _is_origin_allowed(origin: str, policy: "_OriginPolicy") -> bool:
    if policy.mode == "explicit":
        normalized = _normalize_origin(origin)
        if normalized is None:
            return False
        return normalized in policy.values

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
    if policy.mode == "explicit" and not policy.values:
        derived = _derive_scope_origin(scope)
        if derived is not None and _normalize_origin(origin) == _normalize_origin(derived):
            return True, None
    if _is_origin_allowed(origin, policy):
        return True, None

    return False, origin


_HTTP_ROUTES: Dict[str, HttpHandler] = {
    ROOT_ROUTE: _handle_index,
    FAVICON_ROUTE: _handle_favicon,
    HEALTH_ROUTE: _handle_health,
    LIVE_ROUTE: _handle_live,
    READY_ROUTE: _handle_ready,
    INFO_ROUTE: _handle_info,
}


async def _enforce_admin_auth(
    handler: HttpHandler, scope: dict, receive: Callable[[], Awaitable[dict]]
) -> Response:
    return await handler(scope, receive)


asgi = app

__all__ = ["app", "asgi"]
