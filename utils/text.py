"""
utils/text.py — safe text coalescing for Ask Chip

`ensure_text(value)` accepts:
- a plain string
- an iterable / generator of string-like chunks
- an async generator (collected synchronously)
- objects with `.get("text")` / `.get("content")` / `.get("delta")`

It returns a single concatenated string. This prevents bugs where a Python
generator would otherwise render as `"<generator object ...>"` and get sent to
the UI or TTS.
"""
from __future__ import annotations
from typing import Any, Iterable, AsyncIterable
import asyncio

def _chunk_to_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, bytes):
        try:
            return chunk.decode("utf-8", "ignore")
        except Exception:
            return ""
    if isinstance(chunk, dict):
        # common stream delta shapes
        for key in ("text", "content", "delta"):
            v = chunk.get(key)
            if isinstance(v, str):
                return v
    return str(chunk)

async def _collect_async(gen: AsyncIterable[Any]) -> str:
    parts = []
    async for chunk in gen:
        parts.append(_chunk_to_text(chunk))
    return "".join(parts)

def ensure_text(value: Any) -> str:
    """Return a plain string for any of the supported input types."""
    # Already a string
    if isinstance(value, str):
        return value

    # Async generator / async iterable
    if hasattr(value, "__aiter__"):
        # in sync Flask routes we can collect synchronously
        try:
            return asyncio.run(_collect_async(value))  # type: ignore[arg-type]
        except RuntimeError:
            # If an event loop is already running (rare in Flask), fall back to
            # manual scheduling
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(_collect_async(value))  # type: ignore[arg-type]

    # Sync iterable (generator/list/tuple/etc.), but not bytes/str
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
        try:
            parts = [_chunk_to_text(c) for c in value]  # type: ignore[iteration-over-optional]
            return "".join(parts)
        except TypeError:
            # Not actually iterable
            pass

    # Mapping-like object containing text
    if isinstance(value, dict):
        for key in ("text", "content", "delta"):
            v = value.get(key)
            if isinstance(v, str):
                return v

    # Fallback
    return str(value)
