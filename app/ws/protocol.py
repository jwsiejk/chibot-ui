# app/ws/protocol.py
"""
WS protocol helpers for Ask Chip.
- Deterministic JSON encoding (compact) for control frames
- Optional orjson for performance when available
- Protocol constants
"""
PROTO_ID = "askchip-ws/1"
DEFAULT_HEARTBEAT_MS = 25000

try:
    import orjson as _orjson
except Exception:
    _orjson = None

import json as _json

def dumps(obj) -> str:
    if _orjson:
        return _orjson.dumps(obj, option=_orjson.OPT_APPEND_NEWLINE | _orjson.OPT_UTC_Z).decode("utf-8").rstrip("\n")
    # Compact JSON with deterministic separators
    return _json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def loads(s: str):
    # Keep it simple; no orjson dependency required for tests
    import json as _json
    return _json.loads(s)
