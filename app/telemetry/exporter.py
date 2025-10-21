"""Minimal NDJSON file exporter for session telemetry."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class _SessionStats:
    """In-memory counters tracked for each active session."""

    directory: Path
    events_path: Path
    manifest_path: Path
    events_written: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    started_ms: Optional[int] = None
    ended_ms: Optional[int] = None


class FileExporter:
    """Write session telemetry to disk in a restart-friendly manner."""

    def __init__(self, root: Path | str = "exports") -> None:
        self._root = Path(root)
        self._sessions: Dict[str, _SessionStats] = {}

    def begin(self, sid: str) -> None:
        """Prepare an export directory for the session."""
        session_dir = self._root / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        stats = _SessionStats(
            directory=session_dir,
            events_path=session_dir / "events.ndjson",
            manifest_path=session_dir / "manifest.json",
        )
        stats.events_path.write_text("", encoding="utf-8")
        self._sessions[sid] = stats

    def write(self, sid: str, event: Dict[str, Any]) -> None:
        """Append a single event line to the NDJSON log."""
        stats = self._sessions.get(sid)
        if not stats:
            return

        event_copy = dict(event)
        event_type = event_copy.get("type", "")
        ts_ms = event_copy.get("ts_ms")
        if isinstance(ts_ms, int):
            if stats.started_ms is None:
                stats.started_ms = ts_ms
            stats.ended_ms = ts_ms

        with stats.events_path.open("a", encoding="utf-8") as handle:
            json.dump(event_copy, handle, separators=(",", ":"), ensure_ascii=False)
            handle.write("\n")

        stats.events_written += 1
        if isinstance(event_type, str) and event_type:
            stats.by_type[event_type] = stats.by_type.get(event_type, 0) + 1

    def end(self, sid: str, summary: Optional[Dict[str, Any]] = None) -> None:
        """Finalize a session by writing manifest metadata."""
        stats = self._sessions.pop(sid, None)
        if not stats:
            return

        manifest = {
            "sid": sid,
            "started_ms": stats.started_ms,
            "ended_ms": stats.ended_ms,
            "events_written": stats.events_written,
            "by_type": dict(stats.by_type),
            "extra": dict(summary or {}),
        }

        stats.manifest_path.write_text(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=False, indent=None),
            encoding="utf-8",
        )


__all__ = ["FileExporter"]
