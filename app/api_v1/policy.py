from __future__ import annotations

from flask import Blueprint, abort, jsonify, request, session as flask_session

from app.admin_log import emit as admin_log_emit
from app.policy.loader import load_policy, load_policy_layers
from app.security_state import get_user
from app.utils.admin import is_admin_email

bp = Blueprint("policy", __name__)


@bp.get("/policy/effective")
def get_effective_policy():
    def _normalize(value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    def _is_truthy(value: str | None) -> bool:
        if value is None:
            return False
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "on"}

    session_id = _normalize(request.args.get("session_id"))
    persona_id = _normalize(request.args.get("persona_id"))
    tenant_id = _normalize(request.args.get("tenant_id"))
    wants_refresh = _is_truthy(request.args.get("refresh"))

    inspect_mode = any([session_id, persona_id, tenant_id])

    if not inspect_mode:
        policy = load_policy(refresh=wants_refresh)
        return jsonify(policy)

    email = (
        (flask_session.get("user") or {}).get("email")
        or flask_session.get("email")
        or request.headers.get("X-User-Email")
        or (get_user() or "")
    )
    if not is_admin_email((email or "").strip().lower()):
        abort(403)

    payload = load_policy_layers(
        session_id=session_id,
        persona_id=persona_id,
        tenant_id=tenant_id,
        refresh=wants_refresh,
    )

    resolved = payload.get("resolved_context") or {}
    admin_log_emit(
        "EVT_POLICY_VIEW",
        persona_id=resolved.get("persona_id"),
        tenant_id=resolved.get("tenant_id"),
        session_id=resolved.get("session_id"),
        policy_version=payload.get("policy_version"),
    )

    return jsonify(payload)
