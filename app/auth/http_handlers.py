"""HTTP authentication handlers for minting WebSocket tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import types
import uuid
from typing import Any, Mapping, MutableMapping, Optional

import json
import secrets

from app.db.neon import get_user, profile_complete, upsert_user

try:  # pragma: no cover - optional dependency wiring
    from app.security.jwt_utils import mint_ws_token as _mint_ws_token_impl
except ModuleNotFoundError:  # pragma: no cover - fallback when jwt is unavailable
    _mint_ws_token_impl = None

_authlog = logging.getLogger("app.auth.http")

_CSRF_HEADER_NAME = b"x-csrf-token"
_CSRF_COOKIE_CANDIDATES = ("csrf_token", "csrftoken", "askchip_csrf")
_SESSION_COOKIE_NAME = "askchip_session"
_CSRF_COOKIE_NAME = "askchip_csrf"
_SECRET_CACHE: Optional[bytes] = None


async def post_login(scope: Mapping[str, Any], receive) -> "Response":
    """Authenticate a user by email and issue a signed session cookie."""

    from app.asgi_gateway import Response, json_response

    method = _method(scope)
    if method != "POST":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    try:
        payload = await _read_json_body(receive)
    except ValueError:
        return json_response(status=400, error="invalid_json")

    email_raw = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(email_raw, str):
        return json_response(status=400, error="invalid_email")

    email = _normalize_email(email_raw)
    if not email or "@" not in email:
        return json_response(status=400, error="invalid_email")

    try:
        existing = await get_user(email)
        if existing is None:
            user = await upsert_user(email, None, None, None)
        else:
            user = await upsert_user(
                email,
                existing.get("name"),
                existing.get("title"),
                existing.get("region"),
            )
    except Exception:
        _authlog.exception("evt=login_failed email=%s", _safe(email))
        return json_response(status=500, error="server_error")

    complete = profile_complete(user)
    next_step = "ready" if complete else "profile"
    is_admin = _is_admin(user)

    try:
        session_token = _mint_session_token(email)
    except Exception:
        _authlog.exception("evt=login_session_error email=%s", _safe(email))
        return json_response(status=500, error="server_error")
    csrf_token = secrets.token_urlsafe(32)

    response = json_response(
        next=next_step,
        user=_serialize_user(user),
        profile_complete=complete,
        is_admin=is_admin,
    )

    headers = list(response.headers)
    headers.append(_build_cookie_header(_SESSION_COOKIE_NAME, session_token, http_only=True))
    headers.append(_build_cookie_header(_CSRF_COOKIE_NAME, csrf_token, http_only=False))
    response = Response(status=response.status, body=response.body, headers=tuple(headers))

    _authlog.info("evt=login email=%s next=%s", _safe(email), next_step)

    return response


async def get_me(scope: Mapping[str, Any], receive) -> "Response":
    """Return the authenticated user based on the session cookie."""

    from app.asgi_gateway import json_response

    method = _method(scope)
    if method != "GET":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    email = _session_email(scope)
    if not email:
        await _drain_body(receive)
        return json_response(status=401, authenticated=False)

    try:
        user = await get_user(email)
        if user is None:
            user = await upsert_user(email, None, None, None)
    except Exception:
        _authlog.exception("evt=me_failed email=%s", _safe(email))
        await _drain_body(receive)
        return json_response(status=500, error="server_error")

    complete = profile_complete(user)
    is_admin = _is_admin(user)

    await _drain_body(receive)
    return json_response(
        authenticated=True,
        user=_serialize_user(user),
        profile_complete=complete,
        is_admin=is_admin,
    )


async def post_profile(scope: Mapping[str, Any], receive) -> "Response":
    """Update the authenticated user's profile information."""

    from app.asgi_gateway import json_response

    method = _method(scope)
    if method != "POST":
        await _drain_body(receive)
        return json_response(status=405, error="method_not_allowed")

    email = _session_email(scope)
    if not email:
        await _drain_body(receive)
        return json_response(status=401, error="unauthorized")

    if not _validate_csrf(scope):
        await _drain_body(receive)
        return json_response(status=403, error="forbidden")

    try:
        payload = await _read_json_body(receive)
    except ValueError:
        return json_response(status=400, error="invalid_json")

    if not isinstance(payload, dict):
        return json_response(status=400, error="invalid_payload")

    name = _clean_field(payload.get("name"))
    title = _clean_field(payload.get("title"))
    region = _clean_field(payload.get("region"))

    try:
        user = await upsert_user(email, name, title, region)
    except Exception:
        _authlog.exception("evt=profile_update_failed email=%s", _safe(email))
        return json_response(status=500, error="server_error")

    complete = profile_complete(user)

    _authlog.info("evt=profile_update email=%s", _safe(email))

    return json_response(
        user=_serialize_user(user),
        profile_complete=complete,
    )


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

    session_email = _session_email(scope)
    if not session_email:
        await _drain_body(receive)
        return json_response(status=401, error="unauthorized")

    user = _get_authenticated_user(scope)
    if user is None:
        try:
            fetched = await get_user(session_email)
            if fetched is None:
                fetched = await upsert_user(session_email, None, None, None)
        except Exception:
            _authlog.exception("evt=ws_token_user_lookup_failed email=%s", _safe(session_email))
            await _drain_body(receive)
            return json_response(status=500, error="server_error")
        fetched = dict(fetched)
        fetched["profile_complete"] = profile_complete(fetched)
        user = fetched
        state = _state(scope)
        if isinstance(state, dict):
            state.setdefault("user", user)
        elif isinstance(state, types.SimpleNamespace):
            if not hasattr(state, "user"):
                setattr(state, "user", user)

    if user is None:
        await _drain_body(receive)
        return json_response(status=401, error="unauthorized")

    if not _profile_gate(user, scope):
        await _drain_body(receive)
        return json_response(status=409, error="profile_incomplete")

    user_identifier = _user_identifier(user)
    if user_identifier is None:
        await _drain_body(receive)
        _authlog.warning("evt=ws_token_missing_identifier")
        return json_response(status=500, error="server_error")

    await _drain_body(receive)

    sid = uuid.uuid4().hex
    is_admin = _is_admin(user)
    token = _mint_ws_token(user_identifier, sid, is_admin, ttl_s=60)

    _authlog.info("evt=ws_token_mint sid=%s ttl=60", sid)

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
    email = _session_email(scope)
    return bool(email)


async def _read_json_body(receive) -> Any:
    body = await _read_request_body(receive)
    if not body:
        return {}
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_json") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc


async def _read_request_body(receive) -> bytes:
    chunks = bytearray()
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        body = message.get("body") or b""
        if body:
            chunks.extend(body)
        if not message.get("more_body", False):
            break
    return bytes(chunks)


def _build_cookie_header(name: str, value: str, *, http_only: bool) -> tuple[bytes, bytes]:
    safe_value = value.replace("\r", "").replace("\n", "")
    parts = [f"{name}={safe_value}", "Path=/", "Secure", "SameSite=Lax"]
    if http_only:
        parts.append("HttpOnly")
    header_value = "; ".join(parts)
    return (b"set-cookie", header_value.encode("latin1", "ignore"))


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _clean_field(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _mint_session_token(email: str) -> str:
    signature = _session_signature(email)
    return f"{email}|{signature}"


def _session_signature(email: str) -> str:
    key = _secret_key()
    digest = hmac.new(key, email.encode("utf-8"), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    return encoded.rstrip("=")


def _decode_session_token(token: str) -> Optional[str]:
    token = token.strip()
    if not token:
        return None
    try:
        email, provided_sig = token.split("|", 1)
    except ValueError:
        return None
    if not email or not provided_sig:
        return None
    try:
        expected_sig = _session_signature(email)
    except RuntimeError:
        raise
    if hmac.compare_digest(provided_sig, expected_sig):
        return email
    return None


def _session_email(scope: Mapping[str, Any]) -> Optional[str]:
    header_map = _headers(scope)
    cookies = _parse_cookies(header_map.get(b"cookie"))
    token = cookies.get(_SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        email = _decode_session_token(token)
    except RuntimeError:
        _authlog.exception("evt=session_secret_missing")
        return None
    if not email:
        return None
    return _normalize_email(email)


def _safe(value: Optional[str]) -> str:
    if not value:
        return "-"
    cleaned = value.replace("\n", "").replace("\r", "")
    return cleaned


def _serialize_user(user: Any) -> dict[str, Any]:
    if isinstance(user, dict):
        email = user.get("email")
        name = user.get("name")
        title = user.get("title")
        region = user.get("region")
    else:
        email = getattr(user, "email", None)
        name = getattr(user, "name", None)
        title = getattr(user, "title", None)
        region = getattr(user, "region", None)
    return {"email": email, "name": name, "title": title, "region": region}


def _secret_key() -> bytes:
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE
    key = os.getenv("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not configured")
    _SECRET_CACHE = key.encode("utf-8")
    return _SECRET_CACHE


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
        cleaned_value = value.strip()
        if cleaned_value.startswith('"') and cleaned_value.endswith('"') and len(cleaned_value) >= 2:
            cleaned_value = cleaned_value[1:-1]
        cookies[name.strip()] = cleaned_value
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


__all__ = ["post_login", "get_me", "post_profile", "post_ws_token"]


def _mint_ws_token(user_id: str, sid: str, is_admin: bool, *, ttl_s: int) -> str:
    if _mint_ws_token_impl is None:  # pragma: no cover - dependency guard
        raise RuntimeError("JWT support is not configured")
    return _mint_ws_token_impl(user_id, sid, is_admin, ttl_s=ttl_s)
