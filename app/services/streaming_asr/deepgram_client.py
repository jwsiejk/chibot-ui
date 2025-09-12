
import asyncio

class FakeDeepgramClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._count = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def send(self, chunk: bytes):
        self._count += 1
        await asyncio.sleep(0)
