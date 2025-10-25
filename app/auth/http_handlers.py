"""HTTP authentication handlers for minting WebSocket tokens."""
from __future__ import annotations

import logging
import os
import types
import uuid
from typing import Any, Mapping, MutableMapping, Optional

try:  # pragma: no cover - optional dependency wiring
    from app.security.jwt_utils import mint_ws_token as _mint_ws_token_impl
except ModuleNotFoundError:  # pragma: no cover - fallback when jwt is unavailable
    _mint_ws_token_impl = None

_log = logging.getLogger("app.auth.http")

_CSRF_HEADER_NAME = b"x-csrf-token"
_CSRF_COOKIE_CANDIDATES = ("csrf_token", "csrftoken", "askchip_csrf")


async def post_ws_token(scope: Mapping[str, Any], receive) -> "Response":
    """Mint a short-lived WebSocket token for the authenticated session."""

    from app.asgi_gateway import Response, json_response

    method = _method(scope)
    if method != "POST":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    if not _has_cookie_session(scope):
        await _drain_body(receive)
        return json_response(status=401, error="unauthorized")

    if not _validate_csrf(scope):
        await _drain_body(receive)
        return json_response(status=403, error="forbidden")

    user = _get_authenticated_user(scope)
    if user is None:
        await _drain_body(receive)
        return json_response(status=401, error="unauthorized")

    if not _profile_gate(user, scope):
        await _drain_body(receive)
        return json_response(status=409, error="profile_incomplete")

    user_identifier = _user_identifier(user)
    if user_identifier is None:
        await _drain_body(receive)
        _log.warning("evt=ws_token_missing_identifier")
        return json_response(status=500, error="server_error")

    await _drain_body(receive)

    sid = uuid.uuid4().hex
    is_admin = _is_admin(user)
    token = _mint_ws_token(user_identifier, sid, is_admin, ttl_s=60)

    _log.info("evt=ws_token_mint sid=%s ttl=60", sid)

    ttl_ms = 60 * 1000
    return json_response(access_token=token, sid=sid, ttl_ms=ttl_ms)


def _method(scope: Mapping[str, Any]) -> str:
    value = scope.get("method")
    if isinstance(value, bytes):
        value = value.decode("latin1", "ignore")
    if not isinstance(value, str):
        return "GET"
    return value.upper()


async def _drain_body(receive) -> None:
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body", False):
            break


def _headers(scope: Mapping[str, Any]) -> MutableMapping[bytes, bytes]:
    headers = scope.get("headers") or []
    collected: dict[bytes, bytes] = {}
    for name, value in headers:
        if isinstance(name, str):
            name = name.encode("latin1")
        if isinstance(value, str):
            value = value.encode("latin1")
        collected[name.lower()] = value
    return collected


def _has_cookie_session(scope: Mapping[str, Any]) -> bool:
    header_map = _headers(scope)
    cookie_header = header_map.get(b"cookie")
    if not cookie_header:
        return False
    return bool(cookie_header.strip())


def _validate_csrf(scope: Mapping[str, Any]) -> bool:
    header_map = _headers(scope)
    provided = header_map.get(_CSRF_HEADER_NAME)
    if not provided:
        return False

    provided_str = provided.decode("utf-8", "ignore")
    cookies = _parse_cookies(header_map.get(b"cookie"))

    for name in _CSRF_COOKIE_CANDIDATES:
        expected = cookies.get(name)
        if expected is None:
            continue
        return expected == provided_str

    state = _state(scope)
    expected = None
    if isinstance(state, dict):
        expected = state.get("csrf_token") or state.get("csrf")
    else:
        expected = getattr(state, "csrf_token", None) or getattr(state, "csrf", None)
    if expected is None:
        return False
    if isinstance(expected, bytes):
        expected = expected.decode("utf-8", "ignore")
    return str(expected) == provided_str


def _parse_cookies(raw: Optional[bytes]) -> dict[str, str]:
    if not raw:
        return {}
    try:
        text = raw.decode("latin1")
    except UnicodeDecodeError:
        text = raw.decode("latin1", "ignore")
    cookies: dict[str, str] = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def _get_authenticated_user(scope: Mapping[str, Any]) -> Any | None:
    state = _state(scope)
    if state is not None:
        if isinstance(state, dict) and "user" in state:
            return state.get("user")
        if hasattr(state, "user"):
            return getattr(state, "user")
    if "user" in scope:
        return scope.get("user")
    return None


def _profile_gate(user: Any, scope: Mapping[str, Any]) -> bool:
    candidate_keys = ("profile_complete", "profile_completed", "has_profile", "profile_ready")

    for key in candidate_keys:
        value = None
        if isinstance(user, dict):
            value = user.get(key)
        else:
            value = getattr(user, key, None)
        if value is not None:
            return bool(value)

    state = _state(scope)
    for key in candidate_keys:
        if isinstance(state, dict) and key in state:
            return bool(state[key])
        if not isinstance(state, dict) and hasattr(state, key):
            return bool(getattr(state, key))

    return True


def _user_identifier(user: Any) -> Optional[str]:
    for attr in ("id_or_email", "id", "user_id", "email", "sub"):
        value = None
        if isinstance(user, dict):
            value = user.get(attr)
        else:
            value = getattr(user, attr, None)
        if value:
            return str(value)
    return None


def _user_email(user: Any) -> Optional[str]:
    if isinstance(user, dict):
        value = user.get("email") or user.get("user_email")
    else:
        value = getattr(user, "email", None) or getattr(user, "user_email", None)
    if value is None:
        return None
    return str(value).strip().lower()


def _is_admin(user: Any) -> bool:
    admin_raw = os.getenv("ADMIN_EMAILS", "")
    if not admin_raw:
        return False
    user_email = _user_email(user)
    if not user_email:
        return False
    admin_candidates = [item.strip().lower() for item in admin_raw.split(",") if item.strip()]
    return user_email in admin_candidates


def _state(scope: Mapping[str, Any]) -> Any:
    state = scope.get("state")
    if state is None:
        return None
    if isinstance(state, types.SimpleNamespace):
        return state
    return state


__all__ = ["post_ws_token"]


def _mint_ws_token(user_id: str, sid: str, is_admin: bool, *, ttl_s: int) -> str:
    if _mint_ws_token_impl is None:  # pragma: no cover - dependency guard
        raise RuntimeError("JWT support is not configured")
    return _mint_ws_token_impl(user_id, sid, is_admin, ttl_s=ttl_s)
