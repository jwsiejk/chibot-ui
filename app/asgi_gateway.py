"""ASGI gateway mounting the chat.v2 adapter and operational HTTP probes."""
from __future__ import annotations

import hashlib
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
from typing import Any, Awaitable, Callable, Dict, Optional

from app import config
from app.logging_config import configure_logging
from app.telemetry import bus as telemetry_bus
from app.telemetry.exporter import FileExporter
from app.versioning import get_build_id, inject_static_version
from app.voice_v2.asr_runtime import ASRRuntime
from app.voice_v2.engine import EngineV2
from app.voice_v2.tts_runtime import TTSRuntime
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter
from app.services.streaming_asr.deepgram_client import DeepgramClient
 

configure_logging()

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
    # Log raw scope for the chat WS route before any routing/validation
    p = (scope.get("path") or "")
    if p.endswith("/ws/v2/chat"):
        # Keep formatting consistent with this module's logging style
        logger.info(
            "evt=ws_entry path=%s type=%s subs=%s query=%s root_path=%s",
            scope.get("path"), scope.get("type"),
            scope.get("subprotocols"), scope.get("query_string"),
            scope.get("root_path"),
        )
    """Dispatch incoming ASGI scopes to HTTP handlers or the chat adapter."""
    scope_type = scope.get("type")
    path = (scope.get("root_path") or "") + scope.get("path", "")

    if scope_type == "websocket":
        if _ws_route_matches(scope, WS_ROUTE):
            # Origin checks removed — accept and hand off to adapter
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
    raw_html = _load_index_html()
    try:
        html_text = raw_html.decode("utf-8")
    except UnicodeDecodeError:
        html_text = raw_html.decode("utf-8", errors="replace")
    rewritten = inject_static_version(html_text)
    body = rewritten.encode("utf-8")
    build_id = get_build_id()
    return _html_response(body, build_id=build_id)


async def _handle_favicon(scope: dict, receive: Callable[[], Awaitable[dict]]) -> Response:
    """Serve the site favicon when available."""

    if not _method_is_get(scope):
        await _drain_request_body(receive)
        return json_response(status=405, error="method_not_allowed")

    await _drain_request_body(receive)
    if_none_match = _get_request_header(scope, b"if-none-match")
    response = _build_static_response(FAVICON_PATH, if_none_match=if_none_match)
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
    if_none_match = _get_request_header(scope, b"if-none-match")
    response = _build_static_response(resolved, if_none_match=if_none_match)
    if response is None:
        return json_response(status=404, error="not_found")
    return response


def _load_index_html() -> bytes:
    try:
        return INDEX_PATH.read_bytes()
    except OSError:
        return _DEFAULT_INDEX_HTML


def _html_response(body: bytes, *, build_id: Optional[str] = None) -> Response:
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"cache-control", b"no-store, no-cache, must-revalidate"),
        (b"pragma", b"no-cache"),
        (b"expires", b"0"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if build_id is not None:
        headers.append((b"x-build-id", build_id.encode("utf-8")))
    return Response(status=200, body=body, headers=tuple(headers))


def _build_static_response(path: Path, *, if_none_match: Optional[str] = None) -> Optional[Response]:
    try:
        data = path.read_bytes()
    except OSError:
        return None

    digest = hashlib.sha256(data).hexdigest()
    etag = f'"{digest}"'

    if if_none_match:
        candidates = {value.strip() for value in if_none_match.split(",") if value.strip()}
        if "*" in candidates or etag in candidates:
            headers = (
                (b"cache-control", b"public, max-age=31536000, immutable"),
                (b"etag", etag.encode("ascii")),
                (b"content-length", b"0"),
            )
            return Response(status=304, body=b"", headers=headers)

    content_type, encoding = mimetypes.guess_type(path.name)
    if content_type is None:
        content_type = "application/octet-stream"
    elif content_type.startswith("text/") and "charset=" not in content_type:
        content_type = f"{content_type}; charset=utf-8"

    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", content_type.encode("latin1")),
        (b"content-length", str(len(data)).encode("ascii")),
        (b"cache-control", b"public, max-age=31536000, immutable"),
        (b"etag", etag.encode("ascii")),
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
        asr_client = DeepgramClient(api_key=config.DEEPGRAM_API_KEY)
        asr_runtime = ASRRuntime(engine=engine, client=asr_client)
        _adapter = ChatV2Adapter(engine=engine, exporter=exporter)
        _adapter.tts_runtime = tts_runtime
        _adapter.asr_runtime = asr_runtime
    return _adapter


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


def _get_request_header(scope: dict, header_name: bytes) -> Optional[str]:
    headers = scope.get("headers") or []
    for name, value in headers:
        if name == header_name:
            try:
                return value.decode("latin1")
            except UnicodeDecodeError:
                return value.decode("latin1", errors="ignore")
    return None
