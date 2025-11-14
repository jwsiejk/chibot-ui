from __future__ import annotations

import asyncio
import json
import os
import unittest
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - prefer real msgpack when available
    import msgpack  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - fallback in limited environments
    from app.utils import msgpack_compat as msgpack  # type: ignore[no-redef]

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.security.jwt_utils import mint_ws_token
import app.ws.adapter as adapter_module
from app.ws.adapter import (
    CHAT_V2_SUBPROTOCOL,
    CHAT_MSGPACK_SUBPROTOCOL,
    ChatV2Adapter,
    _PERMESSAGE_DEFLATE_HEADER,
)


class TestWebSocketMsgpackNegotiation(unittest.TestCase):
    """Integration tests covering msgpack negotiation and compression hints."""

    def setUp(self) -> None:
        self._orig_msgpack = adapter_module.msgpack
        adapter_module.msgpack = msgpack
        bus.reset()

    def tearDown(self) -> None:
        adapter_module.msgpack = self._orig_msgpack
        bus.reset()

    @staticmethod
    def _make_scope(
        *,
        subprotocols: Optional[List[str]] = None,
        headers: Optional[List[tuple[bytes, bytes]]] = None,
    ) -> Dict[str, Any]:
        sid = "sid-test-msgpack"
        token = mint_ws_token("user-1", sid, False)
        header_list = headers[:] if headers else []
        header_list.append((b"authorization", f"Bearer {token}".encode("ascii")))
        return {
            "type": "websocket",
            "subprotocols": subprotocols if subprotocols is not None else [CHAT_V2_SUBPROTOCOL],
            "headers": header_list,
            "client": ("127.0.0.1", 1234),
            "query_string": f"access_token={token}".encode("ascii"),
        }

    async def _run_adapter(
        self,
        adapter: ChatV2Adapter,
        events: List[dict],
        *,
        scope: Optional[Dict[str, Any]] = None,
    ) -> List[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)

        sent: List[dict] = []

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        await adapter(scope or self._make_scope(), receive, send)
        return sent

    def _drive(
        self,
        adapter: ChatV2Adapter,
        events: List[dict],
        *,
        scope: Optional[Dict[str, Any]] = None,
    ) -> List[dict]:
        return asyncio.run(self._run_adapter(adapter, events, scope=scope))

    def test_msgpack_subprotocol_negotiation_and_ping(self) -> None:
        adapter = ChatV2Adapter()
        scope = self._make_scope(subprotocols=[CHAT_MSGPACK_SUBPROTOCOL, CHAT_V2_SUBPROTOCOL])
        ping_payload = msgpack.packb({"type": "ping", "t": 42}, use_bin_type=True)
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "bytes": ping_payload},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        sent = self._drive(adapter, events, scope=scope)

        accept_messages = [m for m in sent if m.get("type") == "websocket.accept"]
        self.assertEqual(len(accept_messages), 1, "Expected a single accept message")
        accept = accept_messages[0]
        self.assertEqual(accept.get("subprotocol"), CHAT_MSGPACK_SUBPROTOCOL)
        self.assertNotIn("headers", accept, "permessage-deflate should not be offered for msgpack")

        send_frames = [m for m in sent if m.get("type") == "websocket.send"]
        self.assertTrue(send_frames, "Expected websocket.send frames to be emitted")
        decoded_frames = [msgpack.unpackb(frame["bytes"], raw=False) for frame in send_frames if "bytes" in frame]
        self.assertTrue(decoded_frames, "Expected binary control frames when msgpack is negotiated")
        banner = next((f for f in decoded_frames if f.get("type") == "server.banner"), None)
        self.assertIsNotNone(banner, "Expected server.banner frame in msgpack session")
        self.assertEqual(banner.get("control_codec"), "msgpack")
        self.assertFalse(banner.get("permessage_deflate"))
        pong = next((f for f in decoded_frames if f.get("type") == "pong"), None)
        self.assertIsNotNone(pong, "Expected pong response to ping payload")
        self.assertEqual(pong.get("t"), 42)

    def test_json_permessage_deflate_negotiation(self) -> None:
        adapter = ChatV2Adapter()
        scope = self._make_scope(
            subprotocols=[CHAT_V2_SUBPROTOCOL],
            headers=[(b"sec-websocket-extensions", b"permessage-deflate")],
        )
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        sent = self._drive(adapter, events, scope=scope)

        accept_messages = [m for m in sent if m.get("type") == "websocket.accept"]
        self.assertEqual(len(accept_messages), 1)
        accept = accept_messages[0]
        self.assertEqual(accept.get("subprotocol"), CHAT_V2_SUBPROTOCOL)
        headers = accept.get("headers")
        self.assertIsInstance(headers, list)
        self.assertIn((b"sec-websocket-extensions", _PERMESSAGE_DEFLATE_HEADER), headers)

        text_frames = [json.loads(m["text"]) for m in sent if m.get("type") == "websocket.send" and m.get("text")]
        banner = next((f for f in text_frames if f.get("type") == "server.banner"), None)
        self.assertIsNotNone(banner)
        self.assertEqual(banner.get("control_codec"), "json")
        self.assertTrue(banner.get("permessage_deflate"))

        info = next((f for f in text_frames if f.get("type") == "info"), None)
        self.assertIsNotNone(info)
        self.assertEqual(info.get("control_codec"), "json")
