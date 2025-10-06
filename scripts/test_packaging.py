#!/usr/bin/env python3
"""Utility for building the packaging zip used in tests.

The original implementation executed on import which caused pytest to fail
with ``SystemExit`` during test collection.  The logic has been wrapped in a
``main`` function so the module can be imported safely while still supporting
command line execution.
"""

from __future__ import annotations

import os
import sys
import zipfile
from typing import Iterable


INCLUDE_DIRS = [
    "artifacts",
    "app",
    "templates",
    "static/css",
    "static/js",
    "scripts",
]


EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".venv",
    "venv",
    "node_modules",
    "site-packages",
    "dist-packages",
    "__pycache__",
    ".pytest_cache",
)


def _should_skip_path(path: str, patterns: Iterable[str]) -> bool:
    """Return ``True`` if any of the exclusion patterns appear in ``path``."""

    return any(pattern in path for pattern in patterns)


def _assert_true(condition: bool, message: str) -> bool:
    """Print an assertion-style message and return ``condition``."""

    if condition:
        print("PASS:", message)
        return True

    print("FAIL:", message)
    return False


def main() -> int:
    """Build the release zip and report success."""

    ok = True
    output_path = "askchip_release.zip"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root in INCLUDE_DIRS:
            if not os.path.exists(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if _should_skip_path(dirpath, EXCLUDE_PATTERNS):
                    continue
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    archive.write(file_path, file_path)

    ok &= _assert_true(
        os.path.exists(output_path) and os.path.getsize(output_path) > 1024,
        "Release zip built and >1KB",
    )

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
