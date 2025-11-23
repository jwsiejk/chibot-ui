"""Helpers for loading the fingerprinted static asset manifest."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from app.versioning import get_build_id

_LOG = logging.getLogger(__name__)

_STATIC_DIST_DIR = Path(__file__).resolve().parent / "static" / "dist"
_MANIFEST_PATH = _STATIC_DIST_DIR / "manifest.json"

_manifest: Optional[Dict[str, str]] = None
_manifest_mtime: Optional[float] = None


def _load_manifest() -> None:
    global _manifest, _manifest_mtime

    try:
        stat_result = _MANIFEST_PATH.stat()
    except FileNotFoundError:
        if _manifest is not None:
            _LOG.info("evt=manifest_missing path=%s", _MANIFEST_PATH)
        _manifest = None
        _manifest_mtime = None
        return
    except OSError as exc:
        _LOG.warning("evt=manifest_stat_error path=%s err=%s", _MANIFEST_PATH, exc)
        _manifest = None
        _manifest_mtime = None
        return

    if (
        _manifest is not None
        and _manifest_mtime is not None
        and abs(stat_result.st_mtime - _manifest_mtime) < 1e-6
    ):
        return

    try:
        text = _MANIFEST_PATH.read_text("utf-8")
    except OSError as exc:
        _LOG.warning("evt=manifest_read_error path=%s err=%s", _MANIFEST_PATH, exc)
        _manifest = None
        _manifest_mtime = None
        return

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _LOG.warning("evt=manifest_parse_error path=%s err=%s", _MANIFEST_PATH, exc)
        _manifest = None
        _manifest_mtime = None
        return

    if not isinstance(data, dict):
        _LOG.warning("evt=manifest_invalid_structure path=%s", _MANIFEST_PATH)
        _manifest = None
        _manifest_mtime = None
        return

    normalized: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        normalized[str(key)] = value

    _manifest = normalized
    _manifest_mtime = stat_result.st_mtime


def get_manifest() -> Optional[Dict[str, str]]:
    """Return the cached manifest dictionary when available."""

    _load_manifest()
    return _manifest.copy() if _manifest is not None else None


def get_main_js_filename() -> Optional[str]:
    """Return the hashed bundle filename when present."""

    manifest = get_manifest()
    if not manifest:
        return None
    main_js = manifest.get("main_js")
    if not main_js or not isinstance(main_js, str):
        return None
    return main_js


def _append_build_id(src: str, build_id: Optional[str]) -> str:
    if not build_id:
        return src
    separator = "&" if "?" in src else "?"
    return f"{src}{separator}v={build_id}"


def get_main_script_src(build_id: Optional[str] = None) -> str:
    """Return the script src for the main client bundle or legacy app.js."""

    build_id = build_id or get_build_id()
    filename = get_main_js_filename()
    if filename:
        return _append_build_id(f"/static/dist/{filename}", build_id)

    return _append_build_id("/static/js/app.js", build_id)
