
import asyncio
from typing import Dict, List, Any

class WsBus:
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._history = {}  # session_id -> list

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(session_id, []).append(q)
        return q

    async def unsubscribe(self, session_id: str, q: asyncio.Queue):
        async with self._lock:
            lst = self._subscribers.get(session_id, [])
            if q in lst:
                lst.remove(q)

    async def emit(self, session_id: str, payload: Any):
        async with self._lock:
            self._history.setdefault(session_id, []).append(payload)
            for q in self._subscribers.get(session_id, []):
                await q.put(payload)

BUS = WsBus()
