"""Minimal Engine v2 shell with telemetry hooks and session exporting."""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Dict, Mapping, Optional

from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.voice_v2 import (
    EVT_WS_AUDIO_RECV,
    EVT_WS_CLOSE,
    EVT_WS_JSON_RECV,
    EVT_WS_OPEN,
)


def _now_ms() -> int:
    """Return the current epoch timestamp in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class _Envelope:
    """Normalized telemetry envelope returned by the engine hooks."""

    type: str
    sid: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        data = {"type": self.type, "sid": self.sid}
        data.update(self.payload)
        if "meta" in data and isinstance(data["meta"], Mapping):
            data["meta"] = dict(data["meta"])
        if "ts_ms" not in data or not isinstance(data["ts_ms"], int):
            data["ts_ms"] = _now_ms()
        data.setdefault("who", "server")
        data.setdefault("source", "voice_engine")
        data.setdefault("level", "debug")
        return data


class EngineV2:
    """Engine shell that exposes WS hooks, telemetry taps, and exporting."""

    def __init__(self, exporter: FileExporter, *, telemetry_bus=bus) -> None:
        if exporter is None:
            raise ValueError("exporter is required")
        self._exporter = exporter
        self._bus = telemetry_bus

    def on_open(self, sid: str, headers: Mapping[str, str]) -> None:
        """Capture a successful WebSocket upgrade."""
        meta = {"headers": dict(headers), "dir": "in"}
        event = self._envelope(sid, EVT_WS_OPEN, {"meta": meta})
        self._publish(event)

    def on_json(self, sid: str, frame: Mapping[str, Any]) -> None:
        """Capture a validated JSON frame from the adapter."""
        turn_id: Optional[Any] = None
        meta: Dict[str, Any] = {"dir": "in"}
        if isinstance(frame, Mapping):
            frame_type = frame.get("type")
            if isinstance(frame_type, str):
                meta["frame_type"] = frame_type
            turn_id = frame.get("turn_id")
        else:
            frame = {}
        try:
            serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            serialized = "{}"
        meta["byte_count"] = len(serialized.encode("utf-8"))
        payload: Dict[str, Any] = {"meta": meta}
        if turn_id is not None:
            payload["turn_id"] = turn_id
        event = self._envelope(sid, EVT_WS_JSON_RECV, payload)
        self._publish(event)

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:
        """Capture an incoming audio chunk."""
        byte_count = len(chunk)
        meta = {"dir": "in", "byte_count": byte_count, "seq": seq}
        event = self._envelope(sid, EVT_WS_AUDIO_RECV, {"meta": meta})
        self._publish(event)

    def on_close(self, sid: str, code: int, reason: Optional[str]) -> None:
        """Capture the WebSocket closing handshake."""
        meta = {"code": code, "reason": reason}
        event = self._envelope(sid, EVT_WS_CLOSE, {"meta": meta})
        self._publish(event)

    def _envelope(self, sid: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        envelope = _Envelope(event_type, sid, dict(payload))
        return envelope.to_dict()

    def _publish(self, event: Dict[str, Any]) -> None:
        self._bus.publish(dict(event))
        self._exporter.write(event["sid"], dict(event))


__all__ = ["EngineV2"]
