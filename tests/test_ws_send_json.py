from __future__ import annotations

import asyncio
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.voice_v2 import EVT_WS_JSON_SEND
from app.ws.adapter import ChatV2Adapter


class SendJsonFailureTests(unittest.TestCase):
    """Ensure _send_json publishes telemetry even when the socket is closed."""

    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []

        def _capture(event: dict) -> None:
            if isinstance(event, dict):
                self.events.append(dict(event))

        self.token = bus.subscribe(EVT_WS_JSON_SEND, _capture)

    def tearDown(self) -> None:
        bus.reset()

    def test_send_json_publishes_on_asgi_closed(self) -> None:
        adapter = ChatV2Adapter(engine=None)

        async def run_test() -> None:
            async def failing_send(message: dict) -> None:
                raise RuntimeError("websocket.close: response already completed")

            payload = {"type": "asr.final", "req_id": "req-1", "text": "hello"}
            await adapter._send_json(failing_send, "sid-1", payload)

        asyncio.run(run_test())

        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        self.assertEqual(event.get("type"), EVT_WS_JSON_SEND)
        self.assertEqual(event.get("sid"), "sid-1")
        frame = event.get("frame") or {}
        self.assertEqual(frame.get("type"), "asr.final")
        self.assertEqual(frame.get("text"), "hello")
        meta = event.get("meta") or {}
        ws_meta = meta.get("ws") or {}
        self.assertTrue(ws_meta.get("send_skipped"))
        self.assertEqual(ws_meta.get("skipped_reason"), "asgi_closed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
