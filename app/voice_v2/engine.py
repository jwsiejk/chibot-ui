"""Minimal Engine v2 shell with telemetry hooks and session exporting."""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Dict, Mapping, Optional

from app.policy.loader import load_interaction_policy
from app.policy.watch import compute_diff, should_reapply
from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.voice_v2 import (
    EVT_POLICY_APPLIED,
    EVT_WS_AUDIO_RECV,
    EVT_WS_CLOSE,
    EVT_WS_JSON_RECV,
    EVT_WS_JSON_SEND,
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
        self._policy_snapshot: Dict[str, Any] | None = None
        self._last_sid: Optional[str] = None

    def on_open(self, sid: str, headers: Mapping[str, str]) -> None:
        """Capture a successful WebSocket upgrade."""
        meta = {"headers": dict(headers), "dir": "in"}
        event = self._envelope(sid, EVT_WS_OPEN, {"meta": meta})
        self._publish(event)

        self._last_sid = sid
        self._policy_snapshot = None
        self.reapply_policy()

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
        if self._last_sid == sid:
            self._last_sid = None
            self._policy_snapshot = None

    def reapply_policy(self, overrides: Dict[str, Any] | None = None) -> bool:
        """Reload and re-emit the interaction policy when it changes."""

        if not self._last_sid:
            return False

        sid = self._last_sid
        snapshot = load_interaction_policy(overrides)
        previous = self._policy_snapshot

        if not should_reapply(previous, snapshot):
            return False

        diff = compute_diff(previous, snapshot)
        self._policy_snapshot = dict(snapshot)

        self._emit_policy_frame(sid, snapshot)
        self._emit_policy_applied(sid, previous, diff)
        return True

    def _envelope(self, sid: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        envelope = _Envelope(event_type, sid, dict(payload))
        return envelope.to_dict()

    def _publish(self, event: Dict[str, Any]) -> None:
        self._bus.publish(dict(event))
        self._exporter.write(event["sid"], dict(event))

    def _emit_policy_frame(self, sid: str, snapshot: Dict[str, Any]) -> None:
        frame = {"type": "policy.interaction", "policy": snapshot}
        preview = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        meta = {"ws": {"dir": "out", "size": len(preview.encode("utf-8")), "preview": preview}}
        payload = {"meta": meta, "frame": frame}
        event = self._envelope(sid, EVT_WS_JSON_SEND, payload)
        self._publish(event)

    def _emit_policy_applied(
        self,
        sid: str,
        previous: Dict[str, Any] | None,
        diff: Dict[str, Dict[str, Any]],
    ) -> None:
        meta = {"policy": {"diff": self._summarize_policy_diff(previous, diff)}}
        event = self._envelope(sid, EVT_POLICY_APPLIED, {"meta": meta})
        self._publish(event)

    @staticmethod
    def _summarize_policy_diff(
        previous: Dict[str, Any] | None,
        diff: Dict[str, Dict[str, Any]],
    ) -> Dict[str, list[Any]]:
        summary: Dict[str, list[Any]] = {}
        before = dict(previous or {})

        for key, value in diff.get("added", {}).items():
            summary[key] = [before.get(key), value]

        for key, value in diff.get("changed", {}).items():
            summary[key] = [before.get(key), value]

        for key, value in diff.get("removed", {}).items():
            summary[key] = [value, None]

        return summary


__all__ = ["EngineV2"]
