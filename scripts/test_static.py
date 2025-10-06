#!/usr/bin/env python3
"""Static asset smoke tests used by the continuous integration suite."""

from __future__ import annotations

import glob
import sys
from pathlib import Path


ROOT = Path(".")


def _assert_true(condition: bool, message: str) -> bool:
    """Print an assertion-style message and return ``condition``."""

    if condition:
        print("PASS:", message)
        return True

    print("FAIL:", message)
    return False


def _contains_secret(path: Path) -> bool:
    """Return ``True`` if the file contains obvious secret tokens."""

    text = path.read_text(encoding="utf-8", errors="ignore")
    return "sk_live_" in text or "aws_secret_access_key" in text


def main() -> int:
    ok = True

    # Ensure ASGI app entrypoint exists
    ok &= _assert_true(
        (ROOT / "app" / "asgi_gateway.py").exists(),
        "ASGI gateway present",
    )

    # Ensure index template
    ok &= _assert_true(
        (ROOT / "templates" / "index.html").exists(),
        "index.html present",
    )

    # Ensure no obvious secrets committed
    bad: list[Path] = []
    for path_str in glob.glob("app/**/*.py", recursive=True):
        path = Path(path_str)
        if _contains_secret(path):
            bad.append(path)

    ok &= _assert_true(not bad, "No obvious secrets in source")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
