"""Utilities for building deterministic flow export archives."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Tuple
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


__all__ = ["build_flow_zip"]


_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_README_NAME = "README.txt"
_MANIFEST_NAME = "manifest.json"
_EVENTS_NAME = "events.ndjson"


def build_flow_zip(sid: str, root: Path = Path("exports")) -> Path:
    """Package the flow artifacts for ``sid`` into a deterministic ZIP archive."""

    session_dir = Path(root) / sid
    manifest_path = session_dir / _MANIFEST_NAME
    events_path = session_dir / _EVENTS_NAME

    _assert_exists(manifest_path)
    _assert_exists(events_path)

    manifest_bytes = manifest_path.read_bytes()
    events_bytes = events_path.read_bytes()

    manifest_data = _load_manifest(manifest_bytes)
    created_label = _determine_created_label(manifest_data)
    readme_bytes = _build_readme(sid, created_label)

    archive_path = session_dir / "flow.zip"
    entries: Iterable[Tuple[str, bytes]] = [
        (_README_NAME, readme_bytes),
        (_EVENTS_NAME, events_bytes),
        (_MANIFEST_NAME, manifest_bytes),
    ]

    with ZipFile(archive_path, mode="w") as zf:
        for name, payload in sorted(entries, key=lambda item: item[0]):
            info = ZipInfo(filename=name)
            info.date_time = _ARCHIVE_TIMESTAMP
            info.compress_type = ZIP_DEFLATED
            zf.writestr(info, payload)

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
        "This archive contains the foundational flow artifacts for the session:",
        f"- {_MANIFEST_NAME}: session metadata as recorded by the exporter.",
        f"- {_EVENTS_NAME}: chronological event stream (unredacted).",
        f"- {_README_NAME}: this overview and caveats.",
        "",
        "Privacy & Roadmap:",
        "- Sensitive data may still be present. Handle with care.",
        "- Additional redaction, SHA-256 mapping, and size caps will arrive in BUILD 06 Task C.",
        "",
        "For documentation, see /docs.",
    ]

    content = "\n".join(lines) + "\n"
    return content.encode("utf-8")
