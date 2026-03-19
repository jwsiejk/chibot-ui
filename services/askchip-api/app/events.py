from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


class EventBus:
    def __init__(self) -> None:
        self._connections: dict[str | None, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, session_id: str | None) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id].add(websocket)

    async def disconnect(self, websocket: WebSocket, session_id: str | None) -> None:
        async with self._lock:
            if session_id in self._connections:
                self._connections[session_id].discard(websocket)

    async def publish(self, event: dict[str, Any], session_id: str | None) -> None:
        targets: set[WebSocket] = set()
        async with self._lock:
            targets.update(self._connections.get(None, set()))
            if session_id is not None:
                targets.update(self._connections.get(session_id, set()))
        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(event)
            except WebSocketDisconnect:
                stale.append(websocket)
            except RuntimeError:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for webs in self._connections.values():
                    for websocket in stale:
                        webs.discard(websocket)
