"""Utilities for tracking per-session chat history buffers."""
from __future__ import annotations

import copy
from collections import deque
from typing import Any, Deque, Dict, List, Mapping


class ConversationBuffer:
    """Maintain a bounded, per-session history of chat messages."""

    def __init__(self, max_messages: int = 100) -> None:
        if not isinstance(max_messages, int):
            raise TypeError("max_messages must be an integer")
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        self._max_messages = max_messages
        self._buffers: Dict[str, Deque[Dict[str, Any]]] = {}

    def append(self, sid: str, message: Mapping[str, Any]) -> None:
        """Record a chat.message for the provided session identifier."""

        if not isinstance(sid, str) or not sid:
            return
        if not isinstance(message, Mapping):
            return

        buffer = self._buffers.setdefault(sid, deque(maxlen=self._max_messages))
        buffer.append(copy.deepcopy(dict(message)))

    def messages(self, sid: str) -> List[Dict[str, Any]]:
        """Return a copy of the buffered messages for the session."""

        buffer = self._buffers.get(sid)
        if not buffer:
            return []
        return [copy.deepcopy(item) for item in buffer]


__all__ = ["ConversationBuffer"]
