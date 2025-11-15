"""HTTP response middleware helpers."""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

_Header = Tuple[bytes, bytes]


def apply_cache_headers(headers: Iterable[_Header]) -> Tuple[_Header, ...]:
    """Ensure cache-control headers follow the immutable/static policy."""

    normalized = list(headers)
    content_type: Optional[str] = None
    cache_index: Optional[int] = None

    for idx, (name, value) in enumerate(normalized):
        lowered = name.lower()
        if lowered == b"content-type":
            try:
                content_type = value.decode("latin1")
            except UnicodeDecodeError:
                content_type = value.decode("latin1", errors="ignore")
        elif lowered == b"cache-control":
            cache_index = idx

    ct_lower = (content_type or "").lower()
    cache_value: Optional[bytes] = None
    if "text/html" in ct_lower:
        cache_value = b"no-store, must-revalidate"
    elif any(token in ct_lower for token in ("javascript", "text/css", "font", "image")):
        cache_value = b"public, max-age=31536000, immutable"

    if cache_index is not None:
        existing = normalized[cache_index][1]
        if b"no-store" in existing.lower():
            return tuple(normalized)

    if cache_value is None:
        return tuple(normalized)

    if cache_index is None:
        normalized.append((b"cache-control", cache_value))
    else:
        name, _ = normalized[cache_index]
        normalized[cache_index] = (name, cache_value)

    return tuple(normalized)
