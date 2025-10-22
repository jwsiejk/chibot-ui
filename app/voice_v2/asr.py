"""Minimal ASR adapter that publishes telemetry for Deepgram.

This adapter intentionally composes the :class:`~app.services.streaming_asr.deepgram_client.DeepgramClient`
from Build 04-G so that the websocket lifecycle and keepalive responsibilities stay
centralized in a single client implementation. The adapter only orchestrates
telemetry emission for Build 05.
"""
from __future__ import annotations

from typing import Any, Callable

from app.services.streaming_asr.deepgram_client import DeepgramClient
from app.telemetry import bus
from app.voice_v2 import EVT_ASR_FINAL, EVT_ASR_PARTIAL, EVT_ASR_READY


class ASRAdapter:
    """Stubbed streaming ASR adapter wired into the telemetry bus."""

    def __init__(
        self,
        *,
        telemetry_bus=bus,
        client_factory: Callable[[], DeepgramClient] | None = None,
        vendor: str = "deepgram",
        partial_confidence: float = 0.55,
        final_confidence: float = 0.9,
        partials_before_final: int = 2,
    ) -> None:
        if partials_before_final < 1:
            raise ValueError("partials_before_final must be >= 1")
        self._bus = telemetry_bus
        self._client_factory = client_factory or DeepgramClient
        self._vendor = vendor
        self._client: DeepgramClient | None = None
        self._started = False
        self._req_seq = 0
        self._current_req_id: str | None = None
        self._partial_confidence = float(partial_confidence)
        self._final_confidence = float(final_confidence)
        self._partials_before_final = partials_before_final
        self._partial_count = 0
        self._buffer: list[str] = []

    async def start(self, websocket: Any) -> None:
        """Attach to the provider websocket and publish readiness."""

        if websocket is None:
            raise ValueError("websocket must not be None")
        if self._started:
            return

        client = self._client_factory()
        await client.connect(websocket)
        self._client = client
        self._started = True
        self._publish_ready()

    async def stop(self) -> None:
        """Tear down the provider client and reset turn state."""

        if not self._started:
            return

        self._started = False
        self._reset_turn()

        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    def feed(self, chunk: bytes, seq: int) -> None:
        """Consume an audio chunk and emit simulated ASR events."""

        if not self._started:
            raise RuntimeError("ASRAdapter.start() must be awaited before feed()")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes-like")
        if not isinstance(seq, int):
            raise TypeError("seq must be an int")

        text_fragment = self._normalize_chunk(chunk, seq)
        self._buffer.append(text_fragment)
        full_text = " ".join(self._buffer).strip()
        if not full_text:
            full_text = f"[seq:{seq}]"

        req_id = self._ensure_req_id()
        partial_index = self._partial_count
        self._publish_partial(req_id, full_text, seq, partial_index)
        self._partial_count += 1

        if self._partial_count >= self._partials_before_final:
            self._publish_final(req_id, full_text, seq)
            self._reset_turn()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _publish_ready(self) -> None:
        event = {
            "type": EVT_ASR_READY,
            "vendor": self._vendor,
            "source": "asr_adapter",
        }
        self._publish(event)

    def _publish_partial(self, req_id: str, text: str, seq: int, partial_index: int) -> None:
        event = {
            "type": EVT_ASR_PARTIAL,
            "req_id": req_id,
            "text": text,
            "confidence": self._partial_confidence,
            "vendor": self._vendor,
            "meta": {
                "seq": seq,
                "partial_index": partial_index,
            },
            "source": "asr_adapter",
        }
        self._publish(event)

    def _publish_final(self, req_id: str, text: str, seq: int) -> None:
        event = {
            "type": EVT_ASR_FINAL,
            "req_id": req_id,
            "text": text,
            "confidence": self._final_confidence,
            "vendor": self._vendor,
            "meta": {
                "seq": seq,
            },
            "source": "asr_adapter",
        }
        self._publish(event)

    def _publish(self, event: dict) -> None:
        self._bus.publish(event)

    def _ensure_req_id(self) -> str:
        req_id = self._current_req_id
        if req_id is None:
            self._req_seq += 1
            req_id = f"dg-{self._req_seq:05d}"
            self._current_req_id = req_id
        return req_id

    def _reset_turn(self) -> None:
        self._current_req_id = None
        self._partial_count = 0
        self._buffer.clear()

    @staticmethod
    def _normalize_chunk(chunk: bytes | bytearray, seq: int) -> str:
        try:
            decoded = bytes(chunk).decode("utf-8", errors="ignore").strip()
        except Exception:  # pragma: no cover - defensive
            decoded = ""
        if decoded:
            return decoded
        return f"[seq:{seq}]"


__all__ = ["ASRAdapter"]
