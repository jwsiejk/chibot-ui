"""Helpers for static asset versioning and build metadata."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

_BUILD_ID: Optional[str] = None
_STATIC_PATH_PREFIXES = ("static/", "admin/ui/")


def _build_static_pattern() -> re.Pattern[str]:
    prefixes = "|".join(re.escape(prefix) for prefix in _STATIC_PATH_PREFIXES)
    pattern = (
        r"(?P<attr>\b(?:src|href))"
        r"(?P<before_eq>\s*)=(?P<after_eq>\s*)"
        r"(?P<quote>['\"])"
        r"(?P<url>/(?:"
        + prefixes
        + r")(?:[^'\"]*))"
        r"(?P=quote)"
    )
    return re.compile(pattern, re.IGNORECASE)


_STATIC_ATTR_PATTERN = _build_static_pattern()


def get_build_id() -> str:
    """Return a stable identifier for the current build."""

    global _BUILD_ID
    if _BUILD_ID is None:
        for env_var in ("BUILD_ID", "RENDER_GIT_COMMIT", "GIT_SHA"):
            value = os.getenv(env_var)
            if value:
                _BUILD_ID = value
                break
        else:
            now = datetime.now(timezone.utc)
            _BUILD_ID = now.strftime("%Y%m%d%H%M%S")
    return _BUILD_ID


def inject_static_version(html: str) -> str:
    """Append a cache-busting query string to static asset references."""

    build_id = get_build_id()

    def _replace(match: re.Match[str]) -> str:
        url = match.group("url")
        if "?" in url:
            return match.group(0)
        attr = match.group("attr")
        before_eq = match.group("before_eq")
        after_eq = match.group("after_eq")
        quote = match.group("quote")
        return f"{attr}{before_eq}={after_eq}{quote}{url}?v={build_id}{quote}"

    return _STATIC_ATTR_PATTERN.sub(_replace, html)
