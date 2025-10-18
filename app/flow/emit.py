"""Convenience shim for emitting flow events without importing FlowStore."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.flow.trace import FlowStore as _FS


def emit(
    session_id: str,
    level: str,
    phase: str,
    type: str,
    who: str,
    meta: Optional[dict[str, Any]] = None,
    parent_id: Optional[str] = None,
) -> str:
    """Emit a flow event via the shared FlowStore.

    FlowStore.emit occasionally returns ``None`` (for suppressed events). The
    shim normalizes that to an empty string to maintain a ``str`` return type.
    """

    store = _FS.instance()
    return store.emit(
        session_id=session_id,
        level=level,
        phase=phase,
        type_=type,
        who=who,
        meta=meta,
        parent_id=parent_id,
    ) or ""


def add_batch(parent_id: str, kind: str, items: Iterable[Any]) -> None:
    """Attach a batch payload to an existing event via the FlowStore."""

    store = _FS.instance()
    store.add_batch_for_event(parent_id, kind, items)
