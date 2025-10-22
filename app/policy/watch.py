"""Helpers for detecting interaction policy changes."""
from __future__ import annotations

from typing import Any, Dict

DiffResult = Dict[str, Dict[str, Any]]


def compute_diff(prev: Dict[str, Any] | None, curr: Dict[str, Any]) -> DiffResult:
    """Return a shallow diff between the previous and current snapshots."""

    prev_snapshot: Dict[str, Any] = dict(prev or {})
    curr_snapshot: Dict[str, Any] = dict(curr)

    prev_keys = set(prev_snapshot)
    curr_keys = set(curr_snapshot)

    added = {key: curr_snapshot[key] for key in curr_keys - prev_keys}
    removed = {key: prev_snapshot[key] for key in prev_keys - curr_keys}

    changed = {
        key: curr_snapshot[key]
        for key in curr_keys & prev_keys
        if prev_snapshot[key] != curr_snapshot[key]
    }

    return {"added": added, "changed": changed, "removed": removed}


def should_reapply(prev: Dict[str, Any] | None, curr: Dict[str, Any]) -> bool:
    """Return ``True`` when the snapshot warrants a reapply."""

    if prev is None:
        return True

    diff = compute_diff(prev, curr)
    return any(diff_section for diff_section in diff.values())


__all__ = ["compute_diff", "should_reapply"]
