diff --git a/app/asgi_gateway.py b/app/asgi_gateway.py
new file mode 100644
index 0000000000000000000000000000000000000000..f441aab1b2027b18a219cdeaafbd04a0edd1233f
--- /dev/null
+++ b/app/asgi_gateway.py
@@ -0,0 +1,89 @@
+"""ASGI gateway mounting the chat.v2 adapter and health probe."""
+from __future__ import annotations
+
+import json
+from typing import Any, Awaitable, Callable, Optional
+
+from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter
+
+WS_ROUTE = "/ws/v2/chat"
+HEALTH_ROUTE = "/api/v1/health"
+
+_adapter: Optional[ChatV2Adapter] = None
+
+
+async def app(scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
+    """Dispatch incoming ASGI scopes to HTTP handlers or the chat adapter."""
+    scope_type = scope.get("type")
+    path = (scope.get("root_path") or "") + scope.get("path", "")
+
+    if scope_type == "websocket":
+        if path == WS_ROUTE:
+            await _get_adapter()(scope, receive, send)
+        else:
+            await _reject_websocket(receive, send)
+        return
+
+    if scope_type == "http":
+        if path == HEALTH_ROUTE:
+            await _handle_health(scope, receive, send)
+        else:
+            await _drain_request_body(receive)
+            await _send_json_response(send, {"error": "not_found"}, status=404)
+        return
+
+    raise RuntimeError(f"Unsupported ASGI scope type: {scope_type}")
+
+
+async def _handle_health(
+    scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]
+) -> None:
+    """Return a static JSON document for health checks."""
+    method = scope.get("method", "GET").upper()
+    if method != "GET":
+        await _drain_request_body(receive)
+        await _send_json_response(send, {"error": "method_not_allowed"}, status=405)
+        return
+
+    await _drain_request_body(receive)
+    payload = {"ok": True, "engine": "v2", "ws_subprotocol": CHAT_V2_SUBPROTOCOL}
+    await _send_json_response(send, payload, status=200)
+
+
+async def _drain_request_body(receive: Callable[[], Awaitable[dict]]) -> None:
+    """Consume and discard the HTTP request body if present."""
+    while True:
+        message = await receive()
+        if message.get("type") != "http.request":
+            break
+        if not message.get("more_body"):
+            break
+
+
+async def _reject_websocket(receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
+    """Politely refuse WebSocket connections on unsupported routes."""
+    message = await receive()
+    if message.get("type") == "websocket.connect":
+        await send({"type": "websocket.close", "code": 1000})
+
+
+async def _send_json_response(send: Callable[[dict], Awaitable[None]], payload: Any, *, status: int) -> None:
+    """Serialize JSON payload and emit a minimal HTTP response."""
+    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
+    headers = [
+        (b"content-type", b"application/json"),
+        (b"content-length", str(len(body)).encode("ascii")),
+    ]
+    await send({"type": "http.response.start", "status": status, "headers": headers})
+    await send({"type": "http.response.body", "body": body, "more_body": False})
+
+
+__all__ = ["app"]
+
+
+def _get_adapter() -> ChatV2Adapter:
+    """Return a lazily instantiated ChatV2Adapter singleton."""
+    global _adapter
+    if _adapter is None:
+        _adapter = ChatV2Adapter()
+    return _adapter
diff --git a/app/ws/adapter.py b/app/ws/adapter.py
new file mode 100644
index 0000000000000000000000000000000000000000..02ba4aa2d300929daef8b5bbe570b998869b7c39
--- /dev/null
+++ b/app/ws/adapter.py
@@ -0,0 +1,257 @@
+"""chat.v2 WebSocket adapter for AskChip."""
+from __future__ import annotations
+
+import inspect
+import json
+import time
+import uuid
+from dataclasses import dataclass
+from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Protocol, runtime_checkable
+
+from app.telemetry import bus
+from app.voice_v2 import (
+    EVT_WS_AUDIO_RECV,
+    EVT_WS_CLOSE,
+    EVT_WS_JSON_RECV,
+    EVT_WS_JSON_SEND,
+    EVT_WS_OPEN,
+)
+
+CHAT_V2_SUBPROTOCOL = "chat.v2"
+TEXT_FRAME_LIMIT_BYTES = 64 * 1024
+BINARY_FRAME_LIMIT_BYTES = 2 * 1024 * 1024
+PING_MIN_INTERVAL_MS = 500
+
+_ALLOWED_TEXT_FRAME_TYPES = {
+    "client.ready",
+    "audio.header",
+    "admin.toggle",
+}
+
+
+@runtime_checkable
+class EngineHooks(Protocol):
+    """Engine surface used by the WebSocket adapter."""
+
+    def on_open(self, sid: str, headers: Dict[str, str]) -> Any:  # pragma: no cover - signature stub
+        """Called when a new WebSocket connection is established."""
+
+    def on_json(self, sid: str, frame: Dict[str, Any]) -> Any:  # pragma: no cover - signature stub
+        """Handle a validated JSON frame from the client."""
+
+    def on_audio(self, sid: str, chunk: bytes, seq: int) -> Any:  # pragma: no cover - signature stub
+        """Handle a binary audio chunk from the client."""
+
+    def on_close(self, sid: str, code: int, reason: Optional[str]) -> Any:  # pragma: no cover - signature stub
+        """Handle the WebSocket closing."""
+
+
+@dataclass
+class AdapterContext:
+    """Per-connection state."""
+
+    sid: str
+    headers: Dict[str, str]
+    audio_seq: int = 0
+    last_pong_sent_ms: int = 0
+
+
+class ChatV2Adapter:
+    """Minimal chat.v2 WebSocket adapter with telemetry taps."""
+
+    def __init__(
+        self,
+        engine: EngineHooks | None = None,
+        *,
+        text_limit_bytes: int = TEXT_FRAME_LIMIT_BYTES,
+        binary_limit_bytes: int = BINARY_FRAME_LIMIT_BYTES,
+    ) -> None:
+        self.engine = engine
+        self.text_limit_bytes = text_limit_bytes
+        self.binary_limit_bytes = binary_limit_bytes
+
+    async def __call__(self, scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
+        if scope.get("type") != "websocket":
+            raise RuntimeError("ChatV2Adapter can only handle websocket scopes")
+
+        connect_event = await receive()
+        if connect_event.get("type") != "websocket.connect":
+            return
+
+        subprotocols = scope.get("subprotocols") or []
+        if CHAT_V2_SUBPROTOCOL not in subprotocols:
+            await self._reject_subprotocol(send)
+            return
+
+        await send({"type": "websocket.accept", "subprotocol": CHAT_V2_SUBPROTOCOL})
+
+        headers = self._decode_headers(scope.get("headers", ()))
+        ctx = AdapterContext(sid=uuid.uuid4().hex, headers=headers)
+
+        await self._publish(EVT_WS_OPEN, ctx.sid, {"headers": dict(headers)})
+        await self._invoke_engine("on_open", ctx.sid, headers)
+
+        close_code = 1000
+        close_reason: Optional[str] = None
+
+        try:
+            while True:
+                message = await receive()
+                msg_type = message.get("type")
+
+                if msg_type == "websocket.receive":
+                    if message.get("text") is not None:
+                        should_continue = await self._handle_text(message["text"], ctx, send)
+                        if not should_continue:
+                            close_code = 1009
+                            close_reason = "frame_too_large"
+                            await send({"type": "websocket.close", "code": close_code, "reason": close_reason})
+                            break
+                    elif message.get("bytes") is not None:
+                        should_continue = await self._handle_binary(message["bytes"], ctx, send)
+                        if not should_continue:
+                            close_code = 1009
+                            close_reason = "frame_too_large"
+                            await send({"type": "websocket.close", "code": close_code, "reason": close_reason})
+                            break
+                elif msg_type == "websocket.disconnect":
+                    close_code = message.get("code", 1000)
+                    close_reason = message.get("reason")
+                    break
+        except Exception:  # pragma: no cover - defensive guard
+            close_code = 1011
+            close_reason = "internal_error"
+            await send({"type": "websocket.close", "code": close_code, "reason": close_reason})
+            raise
+        finally:
+            await self._publish(EVT_WS_CLOSE, ctx.sid, {"code": close_code, "reason": close_reason})
+            await self._invoke_engine("on_close", ctx.sid, close_code, close_reason)
+
+    async def _reject_subprotocol(self, send: Callable[[dict], Awaitable[None]]) -> None:
+        body = json.dumps({"error": "unsupported_subprotocol", "expected": CHAT_V2_SUBPROTOCOL}).encode("utf-8")
+        headers = [
+            (b"content-type", b"application/json"),
+            (b"content-length", str(len(body)).encode("ascii")),
+        ]
+        await send({"type": "websocket.http.response.start", "status": 426, "headers": headers})
+        await send({"type": "websocket.http.response.body", "body": body, "more_body": False})
+
+    async def _handle_text(self, data: str, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]) -> bool:
+        payload_bytes = data.encode("utf-8")
+        byte_count = len(payload_bytes)
+        frame_type: Optional[str] = None
+        meta: Dict[str, Any] = {"byte_count": byte_count, "dir": "in"}
+
+        if byte_count > self.text_limit_bytes:
+            meta["error"] = "frame_too_large"
+            meta["frame_type"] = None
+            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+            await self._send_error(send, ctx.sid, "frame_too_large", "Text frame exceeds limit")
+            return False
+
+        try:
+            frame = json.loads(data)
+            if isinstance(frame, dict):
+                raw_type = frame.get("type")
+                frame_type = raw_type if isinstance(raw_type, str) else None
+            else:
+                frame = {}
+        except json.JSONDecodeError as exc:
+            meta["error"] = "bad_json"
+            meta["frame_type"] = None
+            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+            await self._send_error(send, ctx.sid, "bad_json", f"Invalid JSON payload: {exc.msg}")
+            return True
+
+        if frame_type is None:
+            meta["error"] = "unknown_type"
+            meta["frame_type"] = None
+            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+            await self._send_error(send, ctx.sid, "unknown_type", "Frame missing type field")
+            return True
+
+        meta["frame_type"] = frame_type
+
+        if frame_type == "ping":
+            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+            now_ms = int(time.time() * 1000)
+            if now_ms - ctx.last_pong_sent_ms >= PING_MIN_INTERVAL_MS:
+                ctx.last_pong_sent_ms = now_ms
+                reply_ts = frame.get("t")
+                if not isinstance(reply_ts, int):
+                    reply_ts = now_ms
+                await self._send_json(send, ctx.sid, {"type": "pong", "t": reply_ts})
+            return True
+
+        if frame_type not in _ALLOWED_TEXT_FRAME_TYPES:
+            meta["error"] = "unknown_type"
+            await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+            await self._send_error(send, ctx.sid, "unknown_type", f"Unsupported frame type '{frame_type}'")
+            return True
+
+        await self._publish(EVT_WS_JSON_RECV, ctx.sid, meta)
+        await self._invoke_engine("on_json", ctx.sid, frame)
+        return True
+
+    async def _handle_binary(
+        self, data: bytes, ctx: AdapterContext, send: Callable[[dict], Awaitable[None]]
+    ) -> bool:
+        byte_count = len(data)
+        if byte_count > self.binary_limit_bytes:
+            await self._publish(
+                EVT_WS_AUDIO_RECV,
+                ctx.sid,
+                {"byte_count": byte_count, "error": "frame_too_large", "dir": "in"},
+            )
+            await self._send_error(send, ctx.sid, "frame_too_large", "Binary frame exceeds limit")
+            return False
+
+        ctx.audio_seq += 1
+        meta = {"byte_count": byte_count, "seq": ctx.audio_seq, "dir": "in"}
+        await self._publish(EVT_WS_AUDIO_RECV, ctx.sid, meta)
+        await self._invoke_engine("on_audio", ctx.sid, data, ctx.audio_seq)
+        return True
+
+    async def _send_json(self, send: Callable[[dict], Awaitable[None]], sid: str, payload: Dict[str, Any]) -> None:
+        text = json.dumps(payload, separators=(",", ":"))
+        await send({"type": "websocket.send", "text": text})
+        meta = {
+            "byte_count": len(text.encode("utf-8")),
+            "frame_type": payload.get("type"),
+            "dir": "out",
+        }
+        await self._publish(EVT_WS_JSON_SEND, sid, meta)
+
+    async def _send_error(self, send: Callable[[dict], Awaitable[None]], sid: str, code: str, message: str) -> None:
+        payload = {"type": "error", "code": code, "message": message}
+        await self._send_json(send, sid, payload)
+
+    async def _publish(self, event_type: str, sid: str, meta: Dict[str, Any]) -> None:
+        event = {
+            "type": event_type,
+            "sid": sid,
+            "who": "server",
+            "source": "ws_server",
+            "meta": dict(meta),
+        }
+        bus.publish(event)
+
+    async def _invoke_engine(self, hook: str, *args: Any) -> None:
+        if not self.engine:
+            return
+        handler = getattr(self.engine, hook, None)
+        if handler is None:
+            return
+        result = handler(*args)
+        if inspect.isawaitable(result):
+            await result
+
+    @staticmethod
+    def _decode_headers(headers: Iterable[tuple[bytes, bytes]]) -> Dict[str, str]:
+        decoded: Dict[str, str] = {}
+        for key, value in headers:
+            decoded[key.decode("latin1").lower()] = value.decode("latin1")
+        return decoded
+
+
+__all__ = ["ChatV2Adapter", "CHAT_V2_SUBPROTOCOL"]
