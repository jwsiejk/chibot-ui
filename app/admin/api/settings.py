from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping

_LOGGER = logging.getLogger(__name__)

_DEFAULT_SETTINGS: Dict[str, str] = {"authentication": "none"}


async def handle_admin_settings(scope: dict, receive) -> "Response":
    """Serve a static admin settings payload with optional validation."""

    method = _method(scope)
    if method == "OPTIONS":
        await _drain_body(receive)
        return _options_response()

    if method not in {"GET", "HEAD", "PATCH", "POST"}:
        await _drain_body(receive)
        return _json_response(status=405, error="method_not_allowed")

    if method in {"GET", "HEAD"}:
        await _drain_body(receive)
        return _respond_settings(method)

    body_bytes = await _read_body(receive)
    if body_bytes:
        try:
            json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_response(status=400, error="invalid_json")
        except Exception as exc:  # pragma: no cover - defensive logging
            _LOGGER.exception("Failed to parse admin settings payload", exc_info=exc)
            return _json_response(status=400, error="invalid_json")

    return _respond_settings(method)


def _respond_settings(method: str) -> "Response":
    payload = {"settings": dict(_DEFAULT_SETTINGS)}
    if method == "HEAD":
        return _json_response(status=200, **payload, body_only=True)
    return _json_response(status=200, **payload)


async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        body = message.get("body", b"") or b""
        if body:
            chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _drain_body(receive) -> None:
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body", False):
            break


def _method(scope: Mapping[str, Any]) -> str:
    value = scope.get("method")
    if isinstance(value, bytes):
        value = value.decode("latin1", "ignore")
    if not isinstance(value, str):
        return "GET"
    return value.upper()


def _options_response() -> "Response":
    from app.asgi_gateway import Response

    headers = (
        (b"content-length", b"0"),
        (b"allow", b"GET,HEAD,PATCH,POST,OPTIONS"),
    )
    return Response(status=204, body=b"", headers=headers)


def _json_response(*, status: int, body_only: bool = False, **payload: Any) -> "Response":
    from app.asgi_gateway import Response, json_response

    if body_only:
        body = json_response(status=status, **payload).body
        headers = (
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        )
        return Response(status=status, body=b"", headers=headers)
    return json_response(status=status, **payload)


__all__ = ["handle_admin_settings"]
