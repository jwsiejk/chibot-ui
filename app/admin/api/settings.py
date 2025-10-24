"""Admin settings API handlers for runtime configuration."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from app.db.admin_settings import AdminSettingsStore
from app.policy.schema import DEFAULT_WS_AUTH_MODE
from app.policy.service import PolicyService
from app.security.auth import authorize_admin

_LOGGER = logging.getLogger(__name__)

_SETTINGS_KEY_WS_AUTH_MODE = "ws_auth_mode"
_VALID_WS_AUTH_MODES = {"required", "disabled"}


_store = AdminSettingsStore()
_policy_service: PolicyService | None = None


async def handle_admin_settings(scope: dict, receive) -> "Response":
    """Handle GET/PATCH requests for the admin settings endpoint."""

    method = _method(scope)
    if method == "OPTIONS":
        await _drain_body(receive)
        return _options_response()

    if method not in {"GET", "HEAD", "PATCH", "POST"}:
        await _drain_body(receive)
        return _json_response(status=405, error="method_not_allowed")

    headers = _decode_headers(scope.get("headers", ()))
    authorized, reason, _claims = authorize_admin(headers, scope)
    if not authorized:
        await _drain_body(receive)
        return _json_response(status=401, error="unauthorized", detail=reason)

    if method in {"GET", "HEAD"}:
        await _drain_body(receive)
        snapshot = _get_policy_service().get_auth_policy()
        payload = _serialize_settings(snapshot)
        if method == "HEAD":
            return _json_response(status=200, **payload, body_only=True)
        return _json_response(status=200, **payload)

    body_bytes = await _read_body(receive)
    if not body_bytes:
        return _json_response(status=400, error="invalid_payload", detail="empty body")

    try:
        parsed = json.loads(body_bytes.decode("utf-8"))
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return _json_response(status=400, error="invalid_json")

    if not isinstance(parsed, Mapping):
        return _json_response(status=400, error="invalid_payload", detail="object required")

    settings_payload = parsed.get("settings") if isinstance(parsed.get("settings"), Mapping) else parsed

    mode_value = settings_payload.get(_SETTINGS_KEY_WS_AUTH_MODE)
    if mode_value is None and "wsAuthMode" in settings_payload:
        mode_value = settings_payload.get("wsAuthMode")

    normalized = _normalize_mode(mode_value)
    if normalized is None:
        return _json_response(
            status=400,
            error="invalid_ws_auth_mode",
            detail="ws_auth_mode must be one of: required, disabled",
        )

    policy_service = _get_policy_service()
    try:
        snapshot = policy_service.set_ws_auth_mode(normalized)
    except Exception as exc:  # pragma: no cover - defensive logging
        _LOGGER.exception("Failed to persist ws_auth_mode setting", exc_info=exc)
        return _json_response(status=500, error="internal_error")

    payload = _serialize_settings(snapshot)
    return _json_response(status=200, **payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_mode(value: object) -> Optional[str]:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_WS_AUTH_MODES:
            return candidate
    return None


def _serialize_settings(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    mode = snapshot.get(_SETTINGS_KEY_WS_AUTH_MODE)
    normalized = _normalize_mode(mode) or DEFAULT_WS_AUTH_MODE
    return {"settings": {_SETTINGS_KEY_WS_AUTH_MODE: normalized}}


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


def _decode_headers(raw_headers: Iterable[Sequence[bytes]]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for item in raw_headers:
        if not isinstance(item, Sequence) or len(item) != 2:
            continue
        name, value = item
        try:
            name_str = name.decode("latin1").lower()
            value_str = value.decode("latin1")
        except Exception:  # pragma: no cover - defensive
            continue
        headers[name_str] = value_str
    return headers


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


def _get_policy_service() -> PolicyService:
    global _policy_service
    if _policy_service is not None:
        return _policy_service

    try:  # pragma: no cover - adapter import may fail in some contexts
        from app.ws.adapter import _policy_service as ws_policy_service  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - adapter import failures
        ws_policy_service = None

    if isinstance(ws_policy_service, PolicyService):
        _policy_service = ws_policy_service
        return ws_policy_service

    _policy_service = PolicyService(_store)
    return _policy_service


__all__ = ["handle_admin_settings"]
