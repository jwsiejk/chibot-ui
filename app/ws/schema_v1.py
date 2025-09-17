
# app/ws/schema_v1.py
"""
Deepgram-aligned WS schema (Phase 1).
Client -> Server:
 - binary audio frames
 - {"type":"KeepAlive"}
 - {"type":"CloseStream"}

Server -> Client (subset for Phase 1):
 - {"type":"Results","channel":{"alternatives":[{"transcript":""}]}, "is_final":true, "turn_id":N}
 - {"type":"UtteranceEnd","turn_id":N}
 - {"type":"KeepAliveAck"} (optional)

No vendor calls here; this is schema + control only.
"""
from typing import Dict, Any

ALLOWED_CLIENT_TYPES = {"KeepAlive", "CloseStream"}

def parse_client_json(payload: str) -> Dict[str, Any]:
    import json
    try:
        obj = json.loads(payload)
    except Exception as e:
        raise ValueError("invalid_json") from e
    t = obj.get("type")
    if t not in ALLOWED_CLIENT_TYPES:
        raise ValueError("unsupported_type")
    return {"type": t, **{k:v for k,v in obj.items() if k != "type"}}

def make_results(turn_id: int, transcript: str) -> Dict[str, Any]:
    return {
        "type": "Results",
        "channel": {"alternatives": [{"transcript": transcript or ""}]},
        "is_final": True,
        "turn_id": turn_id,
    }

def make_utterance_end(turn_id: int) -> Dict[str, Any]:
    return {"type": "UtteranceEnd", "turn_id": turn_id}

def make_keepalive_ack() -> Dict[str, Any]:
    return {"type":"KeepAliveAck"}
