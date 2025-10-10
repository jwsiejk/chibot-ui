# app/ws/schema_v1.py
"""
Deepgram-aligned WS schema (Phase 1 → 2).

Client -> Server (text JSON):
 - {"type":"Configure", ...}
 - {"type":"KeepAlive"}
 - {"type":"CloseStream"}
 - {"type":"greet"}                         # WS-only greet alias
 - {"type":"user_msg","text":"..."}         # WS-only user text turn
   # (Also accept legacy aliases: "User","UserText","UserMessage","UserUtterance","UserTextMessage")

Client -> Server (binary):
 - raw Opus frames

Server -> Client:
 - {"type":"Results","channel":{"alternatives":[{"transcript":"","confidence":0.0}],"is_final":true},"turn_id":N}
 - {"type":"UtteranceEnd","turn_id":N}
 - {"type":"KeepAliveAck"}
 - {"type":"Error","code":"...","message":"..."}
"""
from __future__ import annotations
from typing import Any, Dict

_ALLOWED_TYPES = {
    "Configure", "KeepAlive", "CloseStream",
    "greet", "user_msg", "AudioStart",
    # Legacy aliases
    "User", "UserText", "UserMessage", "UserUtterance", "UserTextMessage",
}

def parse_client_json(text: str) -> Dict[str, Any]:
    import json
    try:
        obj = json.loads(text or "{}")
    except Exception:
        raise ValueError("bad_json")
    t = (obj.get("type") or "").strip()
    if t not in _ALLOWED_TYPES:
        raise ValueError("bad_message_type")

    if t == "Configure":
        enc = (obj.get("encoding") or "").lower()
        if enc and enc not in {"opus", "pcm"}:
            raise ValueError("bad_encoding")
        sr = obj.get("sample_rate")
        if sr is not None and (not isinstance(sr, int) or sr <= 0):
            raise ValueError("bad_sample_rate")
        ch = obj.get("channels")
        if ch is not None and (not isinstance(ch, int) or ch <= 0):
            raise ValueError("bad_channels")
    return obj

def make_results(turn_id: int, transcript: str = "", confidence: float = 0.0, is_final: bool = True) -> Dict[str, Any]:
    return {
        "type": "Results",
        "channel": {
            "alternatives": [{"transcript": transcript or "", "confidence": confidence}],
            "is_final": bool(is_final),
        },
        "turn_id": int(turn_id),
    }

def make_utterance_end(turn_id: int) -> Dict[str, Any]:
    return {"type": "UtteranceEnd", "turn_id": int(turn_id)}

def make_keepalive_ack() -> Dict[str, Any]:
    return {"type": "KeepAliveAck"}

def make_error(code: str, message: str) -> Dict[str, Any]:
    return {"type": "Error", "code": str(code), "message": str(message)}
