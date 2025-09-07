from typing import Any, Dict
def emit(kind: str, **fields: Dict[str, Any]) -> None:
    try:
        from .api_v1.admin import _emit as _admin_emit  # type: ignore
    except Exception:
        _admin_emit=None
    if _admin_emit:
        try:
            _admin_emit(kind, **fields)
        except Exception:
            pass
