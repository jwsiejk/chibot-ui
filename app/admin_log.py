from typing import Any, Dict, Mapping


def admin_log_emit(event: Mapping[str, Any]) -> None:
    try:
        from .api_v1.admin import admin_log_emit as _admin_emit_event  # type: ignore
    except Exception:
        _admin_emit_event = None

    if callable(_admin_emit_event):
        try:
            _admin_emit_event(dict(event))
        except Exception:
            pass


def emit(kind: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"event": kind, "kind": kind, **fields}
    admin_log_emit(payload)


__all__ = ["admin_log_emit", "emit"]
