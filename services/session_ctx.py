# services/session_ctx.py
from __future__ import annotations
from typing import Optional, Dict

"""
Lightweight dict-based context carrier for conversation state.
We avoid Flask imports here; a caller passes a dict-like (e.g., flask.session)
and we read/write under the "chip_ctx" key.
Stored keys: product (str), intent (str)
"""

_CTX_KEY = "chip_ctx"

def get(ctx: Optional[Dict]) -> Dict[str, str]:
    if not isinstance(ctx, dict):
        return {}
    data = ctx.get(_CTX_KEY) or {}
    return dict(data) if isinstance(data, dict) else {}

def set(ctx: Optional[Dict], updates: Dict[str, str]) -> Dict[str, str]:
    if not isinstance(ctx, dict):
        # Return what we would have set, but nothing is persisted
        return {k: v for k, v in (updates or {}).items() if v}
    base = get(ctx)
    for k, v in (updates or {}).items():
        if isinstance(v, str) and v:
            base[k] = v
        elif k in base and (v is None or v == ""):
            # Do not keep empty values
            base.pop(k, None)
    ctx[_CTX_KEY] = base
    return base
