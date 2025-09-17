
from __future__ import annotations
import asyncio, json, random
from typing import AsyncGenerator, Dict, Any

class DeepgramClient:
    """Interface for a real Deepgram client (not used in tests)."""
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.connected = False

    async def connect(self):
        # In production, open a real wss connection with headers incl. DEEPGRAM_API_KEY
        self.connected = True

    async def close(self):
        self.connected = False

    async def send(self, chunk: bytes):
        # Send bytes over WS in production
        pass

    async def poll_events(self, timeout: float = 0.01) -> AsyncGenerator[Dict[str, Any], None]:
        if not self.connected:
            return
        await asyncio.sleep(timeout)
        if False:
            yield {"type": "user_partial", "text": "..."}

class FakeDeepgramClient(DeepgramClient):
    """Deterministic fake for tests: emits a couple partials then a final after ~6 slices."""
    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self._count = 0

    async def connect(self):
        await super().connect()

    async def close(self):
        await super().close()

    async def send(self, chunk: bytes):
        self._count += 1

    async def poll_events(self, timeout: float = 0.01):
        await asyncio.sleep(timeout)
        # Emit partials on early sends, final on 6th
        if self._count in (2, 4):
            yield {"type": "user_partial", "text": f"hello {self._count}"}
        if self._count == 6:
            yield {"type": "user_final", "text": "final hello"}
