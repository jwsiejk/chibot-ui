"""Interaction policy loader utilities.

Provides helpers to materialise the voice runtime policy along with the
per-layer breakdown used by the Admin Policy panel.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, MutableMapping, Optional

from app.db import db

_DEFAULT_POLICY: Dict[str, Any] = {
    "voice_runtime": {
        "confirm_window": {
            "first_turn": {
                "min_ms": 420,
                "max_ms": 1200,
                "until_asr_ready": True,
            },
            "warm_turn": {
                "min_ms": 420,
                "max_ms": 1020,
                "until_asr_ready": False,
            },
        },
        "snr_threshold_db": {
            "first_turn": 8.0,
            "warm_turn": 8.0,
        },
        "barge_in": {
            "allow_ptt": True,
            "allow_local_vad": True,
            "require_asr_evidence": False,
            "suppress_during_tts": "all",
            "post_tts_hold_ms": 200,
        },
        "auto_commit": {
            "enabled": True,
            "requires_dual_evidence": False,
            "asr_ready_required": False,
        },
    }
}

_POLICY_CACHE: Dict[str, Any] | None = None
_GLOBAL_OVERRIDES_CACHE: Dict[str, Any] | None = None


def _deep_update(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), MutableMapping):
            _deep_update(target[key], value)  # type: ignore[index]
        else:
            target[key] = copy.deepcopy(value)


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return copy.deepcopy(dict(value))


def _load_overrides() -> Mapping[str, Any]:
    try:
        cfg = db.get_config() or {}
    except Exception:
        return {}
    for key in ("interaction_policy_overrides", "interaction_policy"):
        overrides = cfg.get(key)
        if isinstance(overrides, Mapping):
            return overrides
    return {}


def _get_global_overrides(*, refresh: bool = False) -> Dict[str, Any]:
    global _GLOBAL_OVERRIDES_CACHE

    if refresh or _GLOBAL_OVERRIDES_CACHE is None:
        _GLOBAL_OVERRIDES_CACHE = _copy_mapping(_load_overrides())
    return copy.deepcopy(_GLOBAL_OVERRIDES_CACHE)


def _normalize_identifier(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"auto", "__auto__", "any"}:
        return None
    return text


def _fetch_session(session_id: Optional[str]) -> Dict[str, Any]:
    sid = _normalize_identifier(session_id)
    if not sid:
        return {}
    try:
        sessions = db.memory.get("sessions", {})
    except Exception:
        sessions = {}
    session = sessions.get(sid)
    if isinstance(session, Mapping):
        return copy.deepcopy(dict(session))
    return {}


def _extract_policy_from_mapping(payload: Mapping[str, Any]) -> Dict[str, Any]:
    for key in (
        "interaction_policy_overrides",
        "interaction_policy",
        "policy_overrides",
        "policy",
    ):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    return {}


def _extract_persona_policy(persona_id: Optional[str]) -> Dict[str, Any]:
    persona_key = _normalize_identifier(persona_id)
    if not persona_key:
        return {}
    try:
        personas = db.memory.get("personas", {})
    except Exception:
        personas = {}
    persona = personas.get(persona_key)
    if not isinstance(persona, Mapping):
        return {}

    direct = _extract_policy_from_mapping(persona)
    if direct:
        return direct

    for variant in ("published", "draft"):
        pack_info = persona.get(variant)
        if isinstance(pack_info, Mapping):
            pack = pack_info.get("pack")
            if isinstance(pack, Mapping):
                policy = pack.get("policy")
                if isinstance(policy, Mapping):
                    return copy.deepcopy(dict(policy))
    return {}


def _extract_tenant_policy(tenant_id: Optional[str], *, refresh: bool = False) -> Dict[str, Any]:
    tenant_key = _normalize_identifier(tenant_id)
    if not tenant_key:
        return {}
    try:
        tenants = db.memory.get("tenants", {})
    except Exception:
        tenants = {}
    tenant = tenants.get(tenant_key)
    if isinstance(tenant, Mapping):
        policy = _extract_policy_from_mapping(tenant)
        if policy:
            return policy
    if tenant_key == "default":
        return _get_global_overrides(refresh=refresh)
    return {}


def _extract_session_policy(session: Mapping[str, Any]) -> Dict[str, Any]:
    return _extract_policy_from_mapping(session)


def _list_personas() -> list[dict[str, str]]:
    try:
        personas = db.memory.get("personas", {})
    except Exception:
        personas = {}
    items: list[dict[str, str]] = []
    if isinstance(personas, Mapping):
        for pid, payload in personas.items():
            try:
                pid_str = str(pid)
            except Exception:
                pid_str = pid  # type: ignore[assignment]
            label = pid_str
            if isinstance(payload, Mapping):
                name = payload.get("name")
                if isinstance(name, str) and name.strip():
                    label = name.strip()
                else:
                    alt = payload.get("id")
                    if isinstance(alt, str) and alt.strip():
                        label = alt.strip()
            items.append({"id": pid_str, "label": label})
    items.sort(key=lambda item: item["label"].lower())
    return items


def _list_tenants(*, refresh: bool = False) -> list[dict[str, str]]:
    try:
        tenants = db.memory.get("tenants", {})
    except Exception:
        tenants = {}
    items: list[dict[str, str]] = []
    if isinstance(tenants, Mapping):
        for tid, payload in tenants.items():
            try:
                tid_str = str(tid)
            except Exception:
                tid_str = tid  # type: ignore[assignment]
            label = tid_str
            if isinstance(payload, Mapping):
                name = payload.get("name")
                if isinstance(name, str) and name.strip():
                    label = name.strip()
            items.append({"id": tid_str, "label": label})
    # Ensure the default tenant is present when global overrides exist
    if not any(item["id"] == "default" for item in items):
        default_policy = _get_global_overrides(refresh=refresh)
        if default_policy:
            items.append({"id": "default", "label": "Default Tenant"})
    items.sort(key=lambda item: item["label"].lower())
    return items


def _compute_policy_version(policy: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(
            {"policy": policy, "context": context},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        payload = repr(policy).encode("utf-8", "ignore") + repr(context).encode("utf-8", "ignore")
    return hashlib.sha1(payload).hexdigest()[:12]


def load_policy(*, refresh: bool = False) -> Dict[str, Any]:
    """Return the effective interaction policy for runtime usage."""

    global _POLICY_CACHE, _GLOBAL_OVERRIDES_CACHE

    if refresh:
        _GLOBAL_OVERRIDES_CACHE = None

    if refresh or _POLICY_CACHE is None:
        base = copy.deepcopy(_DEFAULT_POLICY)
        overrides = _get_global_overrides(refresh=refresh)
        if overrides:
            _deep_update(base, overrides)
        _POLICY_CACHE = base
    return copy.deepcopy(_POLICY_CACHE)


def load_policy_layers(
    *,
    session_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Return the effective policy plus per-layer breakdown."""

    global _POLICY_CACHE, _GLOBAL_OVERRIDES_CACHE

    if refresh:
        _POLICY_CACHE = None
        _GLOBAL_OVERRIDES_CACHE = None

    defaults = copy.deepcopy(_DEFAULT_POLICY)
    resolved: Dict[str, str] = {}
    session_layer: Dict[str, Any] = {}
    persona_layer: Dict[str, Any] = {}
    tenant_layer: Dict[str, Any] = {}

    session_data = _fetch_session(session_id)
    if session_data:
        sid = _normalize_identifier(session_id)
        if sid:
            resolved["session_id"] = sid
        session_layer = _extract_session_policy(session_data)
        if not persona_id:
            sess_persona = session_data.get("persona_id")
            if isinstance(sess_persona, str) and sess_persona.strip():
                persona_id = sess_persona
        if not tenant_id:
            sess_tenant = session_data.get("tenant_id")
            if isinstance(sess_tenant, str) and sess_tenant.strip():
                tenant_id = sess_tenant

    persona_key = _normalize_identifier(persona_id)
    if persona_key:
        resolved["persona_id"] = persona_key
        persona_layer = _extract_persona_policy(persona_key)

    tenant_key = _normalize_identifier(tenant_id)
    if tenant_key:
        resolved["tenant_id"] = tenant_key
        tenant_layer = _extract_tenant_policy(tenant_key, refresh=refresh)
    else:
        fallback = _get_global_overrides(refresh=refresh)
        if fallback:
            tenant_layer = fallback
            resolved["tenant_id"] = "default"

    effective = copy.deepcopy(defaults)
    if persona_layer:
        _deep_update(effective, persona_layer)
    if tenant_layer:
        _deep_update(effective, tenant_layer)
    if session_layer:
        _deep_update(effective, session_layer)

    meta = {
        "personas": _list_personas(),
        "tenants": _list_tenants(refresh=refresh),
    }

    result = {
        "effective_policy": effective,
        "layers": {
            "defaults": defaults,
            "persona": persona_layer or None,
            "tenant": tenant_layer or None,
            "session": session_layer or None,
        },
        "resolved_context": resolved,
        "policy_version": _compute_policy_version(effective, resolved),
        "meta": meta,
    }
    return result
