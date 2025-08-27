# utils/call_log.py
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Any, List, Set
import queue
import threading

class CallLog:
    """Thread-safe in-memory event log with pub/sub for SSE."""
    def __init__(self, maxlen: int = 500):
        self.entries: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self.listeners: Set[queue.Queue] = set()
        self.lock = threading.Lock()

    def add(self, kind: str, msg: str, **extra) -> Dict[str, Any]:
        """Append an event and notify any live subscribers."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": str(kind),
            "msg": str(msg),
            **({k: v for k, v in extra.items() if v is not None})
        }
        with self.lock:
            self.entries.append(entry)
            # Fan-out to subscribers without blocking the request thread
            for q in list(self.listeners):
                try:
                    q.put_nowait(entry)
                except Exception:
                    # Drop slow/broken subscribers
                    try:
                        self.listeners.discard(q)
                    except Exception:
                        pass
        return entry

    def recent(self, n: int = 200) -> List[Dict[str, Any]]:
        """Return up to the last n events (oldest first)."""
        with self.lock:
            items = list(self.entries)[-int(max(0, n)):]
        return items

    def clear(self) -> None:
        """Clear all events and notify listeners (no special event)."""
        with self.lock:
            self.entries.clear()

    def tail(self, n: int = 50) -> List[Dict[str, Any]]:
        """Alias for recent(); kept for compatibility."""
        return self.recent(n)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.listeners.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.listeners.discard(q)

# Singleton
call_log = CallLog()
