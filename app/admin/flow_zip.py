"""Utilities for building deterministic flow export archives."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.telemetry import bus as telemetry_bus
from app.telemetry.exporter import compute_sha256
from app.voice_v2 import EVT_TTS_END, EVT_TTS_START


__all__ = ["build_flow_zip"]


_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_README_NAME = "README.txt"
_MANIFEST_NAME = "manifest.json"
_EVENTS_NAME = "events.ndjson"
_EVENTS_REDACTED_NAME = "events.redacted.ndjson"
_TIMELINE_NAME = "flow_timeline.ndjson"
_NLU_NAME = "nlu.ndjson"
_NLG_NAME = "nlg.ndjson"
_LOGS_NAME = "logs.ndjson"

_TIMELINE_TYPES = {
    "EVT_TURN_BEGIN",
    "EVT_TURN_END",
    EVT_TTS_START,
    EVT_TTS_END,
    "EVT_TTS_MASK",
    "EVT_MIC_GATE",
    "EVT_POLICY_APPLIED",
    "EVT_POLICY_SNAPSHOT",
    "EVT_BARGE_IN",
    "EVT_BARGE_DETECTED",
    "EVT_BARGE_CONFIRMED",
    "EVT_BARGE_REJECTED",
    "EVT_ACTION_SAY_END",
    "EVT_AUTH_DISABLED",
    "EVT_DIAG_FIRST_AUDIO_FRAME",
    "EVT_DIAG_NO_AUDIO_FROM_CLIENT",
    "EVT_DIAG_HUD",
    "EVT_VAD",
    "EVT_VAD_DECISION",
    "EVT_CLIENT_LOG",
}
_WS_PREFIX = "EVT_WS_"
_VENDOR_DEBUG_TYPES = {"EVT_VENDOR_DEBUG", "EVT_WS_AUDIO_RECV", "EVT_WS_AUDIO_SEND"}
_PARTIAL_TYPES = {"EVT_ASR_PARTIAL"}

_PRESERVE_META_TYPES = {"EVT_VAD", "EVT_VAD_DECISION", "EVT_CLIENT_LOG"}

_DEFAULT_CAP_BYTES = 25 * 1024 * 1024


class _EventWrapper(dict):
    """Container storing a redacted event and its truncation category."""

    __slots__ = ()

    @property
    def event(self) -> Dict[str, object]:
        return self["event"]

    @property
    def category(self) -> str:
        return self["category"]


def build_flow_zip(sid: str, root: Path = Path("exports"), *, cap_bytes: int = _DEFAULT_CAP_BYTES) -> Path:
    """Package the flow artifacts for ``sid`` into a deterministic ZIP archive."""

    session_dir = Path(root) / sid
    manifest_path = session_dir / _MANIFEST_NAME
    events_path = session_dir / _EVENTS_NAME
    logs_path = session_dir / _LOGS_NAME

    _assert_exists(manifest_path)
    _assert_exists(events_path)

    manifest_data = _load_manifest(manifest_path.read_bytes())
    created_label = _determine_created_label(manifest_data)
    readme_bytes = _build_readme(sid, created_label)

    raw_events = _load_events(events_path)
    wrapped_events = _wrap_events(raw_events)

    drop_counts = {"vendor_debug": 0, "partials": 0, "events_tail": 0}
    archive_bytes: bytes = b""
    payloads: Dict[str, bytes] = {}
    manifest_bytes: bytes = b""
    logs_bytes: bytes | None = None
    if logs_path.is_file():
        logs_bytes = logs_path.read_bytes()

    while True:
        payloads, manifest_bytes = _render_payloads(
            wrapped_events,
            manifest_data,
            readme_bytes,
            drop_counts,
            cap_bytes,
            logs_bytes=logs_bytes,
        )
        entries = dict(payloads)
        entries[_MANIFEST_NAME] = manifest_bytes
        archive_bytes = _render_archive(entries)

        if len(archive_bytes) <= cap_bytes:
            break

        if not _drop_next_event(wrapped_events, drop_counts):
            break

    archive_path = session_dir / "flow.zip"
    archive_path.write_bytes(archive_bytes)
    return archive_path


def _assert_exists(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)


def _load_manifest(raw: bytes) -> Dict[str, object]:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest.json must contain valid UTF-8 encoded JSON") from exc


def _determine_created_label(manifest: Dict[str, object]) -> str:
    candidates = [
        manifest.get("started_ms"),
        manifest.get("created_ms"),
        manifest.get("created_at_ms"),
        manifest.get("created_at"),
    ]

    for value in candidates:
        if isinstance(value, int):
            return _format_epoch_ms(value)
        if isinstance(value, str) and value:
            return value

    return "unknown"


def _format_epoch_ms(value: int) -> str:
    try:
        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "unknown"
    return dt.isoformat().replace("+00:00", "Z")


def _build_readme(sid: str, created_label: str) -> bytes:
    lines = [
        "Flow Export",
        "==========",
        "",
        f"Session ID: {sid}",
        f"Created: {created_label}",
        "",
        "This archive contains privacy-safe flow artifacts:",
        f"- {_MANIFEST_NAME}: session metadata, integrity digests, and truncation notes.",
        f"- {_EVENTS_REDACTED_NAME}: chronological event stream with telemetry redaction re-applied.",
        f"- {_TIMELINE_NAME}: key timeline markers (turns, TTS, mic gate, policy, WS state).",
        f"- {_NLU_NAME}: captured NLU evaluations.",
        f"- {_NLG_NAME}: assistant planning and wording events.",
        "",
        "Files are ordered lexicographically inside the archive. For documentation, see /docs.",
    ]

    content = "\n".join(lines) + "\n"
    return content.encode("utf-8")


def _wrap_events(events: List[Dict[str, object]]) -> List[_EventWrapper]:
    wrapped: List[_EventWrapper] = []
    for event in events:
        redacted = _redact_event(event)
        event_type = redacted.get("type")
        category = _categorize_event(event_type)
        wrapped.append(_EventWrapper(event=redacted, category=category))
    return wrapped


def _categorize_event(event_type: object) -> str:
    if isinstance(event_type, str):
        if event_type in _VENDOR_DEBUG_TYPES:
            return "vendor_debug"
        if event_type in _PARTIAL_TYPES:
            return "partials"
    return "main"


def _render_payloads(
    events: List[_EventWrapper],
    manifest_data: Dict[str, object],
    readme_bytes: bytes,
    drop_counts: Dict[str, int],
    cap_bytes: int,
    *,
    logs_bytes: bytes | None = None,
) -> Tuple[Dict[str, bytes], bytes]:
    redacted_events = [wrapper.event for wrapper in events]

    events_bytes = _dump_ndjson(redacted_events)
    timeline_events = [evt for evt in redacted_events if _is_timeline_event(evt)]
    timeline_bytes = _dump_ndjson(timeline_events)
    nlu_events = [evt for evt in redacted_events if evt.get("type") == "EVT_NLU"]
    nlg_events = [evt for evt in redacted_events if evt.get("type") == "EVT_NLG"]

    payloads = {
        _README_NAME: readme_bytes,
        _EVENTS_REDACTED_NAME: events_bytes,
        _TIMELINE_NAME: timeline_bytes,
        _NLU_NAME: _dump_ndjson(nlu_events),
        _NLG_NAME: _dump_ndjson(nlg_events),
    }

    if logs_bytes is not None:
        payloads[_LOGS_NAME] = logs_bytes

    manifest_bytes = _build_manifest_payload(
        manifest_data,
        payloads,
        drop_counts,
        cap_bytes,
    )

    return payloads, manifest_bytes


def _is_timeline_event(event: Dict[str, object]) -> bool:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return False
    if event_type in _TIMELINE_TYPES:
        return True
    return event_type.startswith(_WS_PREFIX)


def _dump_ndjson(events: Iterable[Dict[str, object]]) -> bytes:
    lines = [json.dumps(event, separators=(",", ":"), ensure_ascii=False) for event in events]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def _build_manifest_payload(
    manifest_data: Dict[str, object],
    payloads: Dict[str, bytes],
    drop_counts: Dict[str, int],
    cap_bytes: int,
) -> bytes:
    manifest_copy = dict(manifest_data)

    existing_sha = {}
    if isinstance(manifest_copy.get("sha256"), dict):
        existing_sha = dict(manifest_copy["sha256"])

    sha_entries = {
        name: compute_sha256(content)
        for name, content in payloads.items()
    }
    existing_sha.update(sha_entries)
    manifest_copy["sha256"] = existing_sha

    file_entries = [
        {"name": name, "size": len(content)}
        for name, content in sorted(payloads.items())
    ]
    if file_entries:
        manifest_copy["files"] = file_entries

    if any(drop_counts.values()):
        manifest_copy["truncated"] = True
        manifest_copy["cap_bytes"] = cap_bytes
        manifest_copy["dropped"] = {
            "vendor_debug": drop_counts.get("vendor_debug", 0),
            "partials": drop_counts.get("partials", 0),
            "events_tail": drop_counts.get("events_tail", 0),
        }
    else:
        manifest_copy.pop("truncated", None)
        manifest_copy.pop("cap_bytes", None)
        manifest_copy.pop("dropped", None)

    return json.dumps(manifest_copy, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _render_archive(entries: Dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, mode="w") as zf:
        for name in sorted(entries):
            info = ZipInfo(filename=name)
            info.date_time = _ARCHIVE_TIMESTAMP
            info.compress_type = ZIP_DEFLATED
            zf.writestr(info, entries[name])
    return buffer.getvalue()


def _drop_next_event(events: List[_EventWrapper], drop_counts: Dict[str, int]) -> bool:
    for category in ("vendor_debug", "partials"):
        idx = _find_last_index(events, category)
        if idx is not None:
            events.pop(idx)
            drop_counts[category] = drop_counts.get(category, 0) + 1
            return True

    if events:
        events.pop()
        drop_counts["events_tail"] = drop_counts.get("events_tail", 0) + 1
        return True

    return False


def _find_last_index(events: List[_EventWrapper], category: str) -> int | None:
    for index in range(len(events) - 1, -1, -1):
        if events[index].category == category:
            return index
    return None


def _redact_event(event: Dict[str, object]) -> Dict[str, object]:
    event_type = event.get("type")
    try:
        redacted = telemetry_bus.redact_payload(event)
        if isinstance(redacted, dict):
            if isinstance(event_type, str) and event_type in _PRESERVE_META_TYPES:
                if "meta" in event:
                    redacted["meta"] = deepcopy(event.get("meta"))
            return redacted
    except Exception:  # pragma: no cover - redaction must not break packaging
        pass

    redacted_copy = dict(event)
    if (
        "meta" in redacted_copy
        and not (isinstance(event_type, str) and event_type in _PRESERVE_META_TYPES)
    ):
        try:
            redacted_copy["meta"] = telemetry_bus._redact_meta(redacted_copy["meta"])
        except Exception:  # pragma: no cover - redaction must not break packaging
            redacted_copy["meta"] = redacted_copy["meta"]
    elif isinstance(event_type, str) and event_type in _PRESERVE_META_TYPES and "meta" in event:
        redacted_copy["meta"] = deepcopy(event.get("meta"))
    return redacted_copy


def _load_events(path: Path) -> List[Dict[str, object]]:
    events: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {idx} of {path.name}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"Event on line {idx} of {path.name} must be a JSON object")
            events.append(parsed)
    return events
