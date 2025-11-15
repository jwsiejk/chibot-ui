import datetime
import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable

_BUILD_ID: str | None = None
_BUILD_FINGERPRINT_KEY: tuple[str | None, str] | None = None
_BUILD_LOCK = threading.Lock()

_FINGERPRINT_CACHE: str | None = None
_FINGERPRINT_LAST_CHECK: float = 0.0
_GIT_SHA_CACHE: str | None = None
_GIT_SHA_LAST_CHECK: float = 0.0

_BASE_DIR = Path(__file__).resolve().parent
_FINGERPRINT_PATHS: tuple[Path, ...] = (
    _BASE_DIR / "static",
    _BASE_DIR / "templates",
    _BASE_DIR / "admin" / "ui",
)

_REFRESH_INTERVAL_SECONDS = max(
    float(os.environ.get("BUILD_ID_REFRESH_INTERVAL", "2.0")), 0.1
)


def _git_sha_short() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def _git_sha_short_cached() -> str | None:
    global _GIT_SHA_CACHE, _GIT_SHA_LAST_CHECK
    now = time.monotonic()
    if (
        _GIT_SHA_CACHE is not None
        and now - _GIT_SHA_LAST_CHECK < _REFRESH_INTERVAL_SECONDS
    ):
        return _GIT_SHA_CACHE
    sha = _git_sha_short()
    _GIT_SHA_CACHE = sha
    _GIT_SHA_LAST_CHECK = now
    return sha


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file():
                    yield candidate
        elif path.is_file():
            yield path


def _compute_paths_fingerprint(paths: Iterable[Path]) -> str:
    digest = hashlib.blake2s(digest_size=8)
    has_data = False
    for file_path in _iter_files(paths):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        has_data = True
        try:
            relative = file_path.relative_to(_BASE_DIR)
        except ValueError:
            relative = file_path
        digest.update(str(relative).encode("utf-8", errors="ignore"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    if not has_data:
        return ""
    return digest.hexdigest()


def _get_paths_fingerprint() -> str:
    global _FINGERPRINT_CACHE, _FINGERPRINT_LAST_CHECK
    now = time.monotonic()
    if (
        _FINGERPRINT_CACHE is not None
        and now - _FINGERPRINT_LAST_CHECK < _REFRESH_INTERVAL_SECONDS
    ):
        return _FINGERPRINT_CACHE
    fingerprint = _compute_paths_fingerprint(_FINGERPRINT_PATHS)
    _FINGERPRINT_CACHE = fingerprint
    _FINGERPRINT_LAST_CHECK = now
    return fingerprint


def current_build_id() -> str:
    global _BUILD_ID, _BUILD_FINGERPRINT_KEY
    env = os.environ.get("BUILD_ID")
    if env and env.strip():
        trimmed = env.strip()
        with _BUILD_LOCK:
            _BUILD_ID = trimmed
            _BUILD_FINGERPRINT_KEY = None
        return trimmed

    with _BUILD_LOCK:
        base_sha = _git_sha_short_cached()
        fingerprint = _get_paths_fingerprint()
        cache_key = (base_sha, fingerprint)
        if _BUILD_ID and _BUILD_FINGERPRINT_KEY == cache_key:
            return _BUILD_ID

        if base_sha:
            candidate = f"{base_sha}.{fingerprint}" if fingerprint else base_sha
        else:
            timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
            candidate = (
                f"{timestamp}.{fingerprint}" if fingerprint else timestamp
            )

        _BUILD_ID = candidate
        _BUILD_FINGERPRINT_KEY = cache_key
        return candidate
