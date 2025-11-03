"""Minimal NDJSON file exporter for session telemetry."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from app.telemetry import bus as telemetry_bus
from app.voice_v2 import (
    EVT_ASR_CLOSED,
    EVT_ASR_FINAL,
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_TTS_END,
    EVT_TTS_START,
)


EVT_ASR_OPEN_ATTEMPT = "asr.open_attempt"
EVT_ASR_OPEN_FAILED = "asr.open_failed"
EVT_WS_JSON_SEND_SUMMARY = "ws.json.send_summary"
EVT_WS_JSON_RECV_SUMMARY = "ws.json.recv_summary"
EVT_WS_AUDIO_FIRST_CHUNK = "ws.audio.first_chunk"

_ASR_LOG_TYPES = {
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_ASR_FINAL,
    EVT_ASR_CLOSED,
    EVT_ASR_OPEN_ATTEMPT,
    EVT_ASR_OPEN_FAILED,
}

_WS_LOG_TYPES = {
    EVT_WS_JSON_SEND_SUMMARY,
    EVT_WS_JSON_RECV_SUMMARY,
    EVT_WS_AUDIO_FIRST_CHUNK,
}

_TTS_LOG_TYPES = {EVT_TTS_START, EVT_TTS_END}


def _now_ms() -> int:
    """Return the current epoch timestamp in milliseconds."""

    return int(time.time() * 1000)


@dataclass
class _SessionStats:
    """In-memory counters tracked for each active session."""

    directory: Path
    events_path: Path
    manifest_path: Path
    logs_path: Path
    logs_asr_path: Path
    logs_ws_path: Path
    logs_tts_path: Path
    events_written: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    started_ms: Optional[int] = None
    ended_ms: Optional[int] = None
    subscription_token: Optional[str] = None


class FileExporter:
    """Write session telemetry to disk in a restart-friendly manner."""

    def __init__(self, root: Path | str = "exports", *, bus=telemetry_bus) -> None:
        self._root = Path(root)
        self._bus = bus
        self._sessions: Dict[str, _SessionStats] = {}

    def begin(self, sid: str) -> None:
        """Prepare an export directory and subscribe to telemetry for the session."""

        if sid in self._sessions:
            # Duplicate begin calls should not create additional subscriptions.
            return

        session_dir = self._root / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = session_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        stats = _SessionStats(
            directory=session_dir,
            events_path=session_dir / "events.ndjson",
            manifest_path=session_dir / "manifest.json",
            logs_path=session_dir / "logs.ndjson",
            logs_asr_path=logs_dir / "asr.jsonl",
            logs_ws_path=logs_dir / "ws.jsonl",
            logs_tts_path=logs_dir / "tts.jsonl",
            started_ms=_now_ms(),
        )

        # Reset the NDJSON log for the session.
        stats.events_path.write_text("", encoding="utf-8")
        stats.logs_path.write_text("", encoding="utf-8")
        stats.logs_asr_path.write_text("", encoding="utf-8")
        stats.logs_ws_path.write_text("", encoding="utf-8")
        stats.logs_tts_path.write_text("", encoding="utf-8")

        def _callback(event: Dict[str, Any]) -> None:
            if event.get("sid") != sid:
                return
            self._handle_event(sid, event)

        token = self._bus.subscribe("*", _callback)
        stats.subscription_token = token
        self._sessions[sid] = stats

        manifest = {
            "sid": sid,
            "schema_version": "1",
            "started_ms": stats.started_ms,
            "open": True,
            "events_written": 0,
            "by_type": {},
        }
        self._write_manifest(stats, manifest)

    def end(self, sid: str, summary: Optional[Dict[str, Any]] = None) -> None:
        """Finalize a session by writing manifest metadata and unsubscribing."""

        stats = self._sessions.pop(sid, None)
        if not stats:
            return

        token = stats.subscription_token
        if token:
            self._bus.unsubscribe(token)

        ended_ms = stats.ended_ms
        if ended_ms is None:
            base = stats.started_ms if stats.started_ms is not None else _now_ms()
            ended_ms = base

        manifest = {
            "sid": sid,
            "schema_version": "1",
            "started_ms": stats.started_ms,
            "open": False,
            "events_written": stats.events_written,
            "by_type": dict(stats.by_type),
            "ended_ms": ended_ms,
        }

        if summary is not None:
            manifest["summary"] = dict(summary)

        self._write_manifest(stats, manifest)

    def _handle_event(self, sid: str, event: Dict[str, Any]) -> None:
        stats = self._sessions.get(sid)
        if not stats:
            return

        with stats.events_path.open("a", encoding="utf-8") as handle:
            json.dump(event, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")

        stats.events_written += 1

        event_type = event.get("type")
        if isinstance(event_type, str) and event_type:
            stats.by_type[event_type] = stats.by_type.get(event_type, 0) + 1

            if event_type == "EVT_LOG":
                log_entry: Dict[str, Any]
                if isinstance(event, dict):
                    log_entry = dict(event)
                else:
                    log_entry = {"type": event_type}
                with stats.logs_path.open("a", encoding="utf-8") as log_handle:
                    json.dump(log_entry, log_handle, separators=(",", ":"), ensure_ascii=False)
                    log_handle.write("\n")

            if event_type in _ASR_LOG_TYPES:
                with stats.logs_asr_path.open("a", encoding="utf-8") as log_handle:
                    json.dump(event, log_handle, separators=(",", ":"), ensure_ascii=False)
                    log_handle.write("\n")

            if event_type in _WS_LOG_TYPES:
                with stats.logs_ws_path.open("a", encoding="utf-8") as log_handle:
                    json.dump(event, log_handle, separators=(",", ":"), ensure_ascii=False)
                    log_handle.write("\n")

            if event_type in _TTS_LOG_TYPES:
                with stats.logs_tts_path.open("a", encoding="utf-8") as log_handle:
                    json.dump(event, log_handle, separators=(",", ":"), ensure_ascii=False)
                    log_handle.write("\n")

        ts_ms = event.get("ts_ms")
        if isinstance(ts_ms, int):
            candidate = ts_ms
            if stats.started_ms is not None and candidate < stats.started_ms:
                candidate = stats.started_ms
            stats.ended_ms = candidate

        manifest = {
            "sid": sid,
            "schema_version": "1",
            "started_ms": stats.started_ms,
            "open": True,
            "events_written": stats.events_written,
            "by_type": dict(stats.by_type),
        }
        self._write_manifest(stats, manifest)

    def _write_manifest(self, stats: _SessionStats, manifest: Dict[str, Any]) -> None:
        tmp_path = stats.manifest_path.with_name(stats.manifest_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, stats.manifest_path)


def compute_sha256(payload: bytes | bytearray | memoryview | str | Path) -> str:
    """Return the hexadecimal SHA-256 digest for the given payload or file."""

    digest = hashlib.sha256()

    if isinstance(payload, (bytes, bytearray, memoryview)):
        digest.update(bytes(payload))
        return digest.hexdigest()

    path = Path(payload)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["FileExporter", "compute_sha256"]
