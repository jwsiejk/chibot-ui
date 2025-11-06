import asyncio
import logging
import unittest
from types import MethodType, SimpleNamespace

import pytest

import app.services.streaming_asr.speechmatics_client as speechmatics_module

from app.services.streaming_asr.speechmatics_client import (
    SpeechmaticsClient,
    _coerce_max_delay_seconds,
    _coerce_transcript_text,
    _extract_text,
    _is_fatal_concurrency_notice,
)


class TestSpeechmaticsConcurrencyNotices(unittest.TestCase):
    def test_info_notice_not_fatal(self) -> None:
        payload = {"message": "Info", "type": "concurrent_session_usage"}
        self.assertFalse(_is_fatal_concurrency_notice(payload))

    def test_warning_notice_not_fatal(self) -> None:
        payload = {"message": "Warning", "severity": "warning", "type": "concurrent_session_limit"}
        self.assertFalse(_is_fatal_concurrency_notice(payload))

    def test_error_notice_fatal(self) -> None:
        payload = {"message": "Error", "type": "concurrent_session_usage"}
        self.assertTrue(_is_fatal_concurrency_notice(payload))

    def test_explicit_severity_error_fatal(self) -> None:
        payload = {"severity": "critical", "type": "concurrent_session_usage"}
        self.assertTrue(_is_fatal_concurrency_notice(payload))


class TestSpeechmaticsExtractText(unittest.TestCase):
    def test_extracts_from_results_alternatives(self) -> None:
        payload = {
            "results": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hello there",
                            "confidence": 0.92,
                        }
                    ]
                }
            ]
        }
        self.assertEqual(_extract_text(payload), "Hello there")

    def test_extracts_from_metadata_tokens(self) -> None:
        payload = {
            "message": "AddPartialTranscript",
            "metadata": {
                "content": [
                    {"type": "word", "text": "Pure"},
                    {"type": "word", "text": " "},
                    {"type": "word", "text": "Storage"},
                ]
            },
        }
        self.assertEqual(_extract_text(payload), "Pure Storage")

    def test_extracts_from_metadata_transcript(self) -> None:
        payload = {
            "message": "AddTranscript",
            "metadata": {"transcript": "flasharray"},
        }
        self.assertEqual(_extract_text(payload), "flasharray")

    def test_token_value_preferred_over_text(self) -> None:
        payload = {
            "message": "AddPartialTranscript",
            "metadata": {
                "content": [
                    {"type": "word", "text": "Hello", "value": "Hello"},
                    {"type": "punctuation", "text": "Slash", "value": "/"},
                    {"type": "word", "text": "Pure", "value": "Pure"},
                ]
            },
        }
        self.assertEqual(_extract_text(payload), "Hello/Pure")

    def test_coerce_transcript_text_handles_tokenized_results(self) -> None:
        payload = {
            "message": "AddTranscript",
            "results": [
                {
                    "alternatives": [
                        {
                            "content": [
                                {"type": "word", "text": "By"},
                                {"type": "punctuation", "text": "."},
                            ],
                        }
                    ]
                }
            ],
        }

        self.assertEqual(_coerce_transcript_text(payload), "By.")


class TestSpeechmaticsMaxDelay(unittest.TestCase):
    def test_converts_milliseconds_to_seconds(self) -> None:
        self.assertAlmostEqual(_coerce_max_delay_seconds(1200), 1.2)

    def test_clamps_above_20_seconds(self) -> None:
        self.assertEqual(_coerce_max_delay_seconds(25000), 20)

    def test_negative_returns_none(self) -> None:
        self.assertIsNone(_coerce_max_delay_seconds(-1))


class TestSpeechmaticsCloseStream(unittest.TestCase):
    def test_close_stream_keeps_state_until_finalized(self) -> None:
        class StubBus:
            def publish(self, event: object) -> None:  # pragma: no cover - not exercised
                pass

        class StubQueue:
            def __init__(self) -> None:
                self.items: list[object] = []

            def put_nowait(self, item: object) -> None:
                self.items.append(item)

        class StubLoop:
            def __init__(self) -> None:
                self.tasks: list[tuple[object, str | None]] = []

            def create_task(self, coro: object, name: str | None = None) -> object:
                self.tasks.append((coro, name))
                close = getattr(coro, "close", None)
                if callable(close):
                    close()
                return object()

        client = SpeechmaticsClient("key", "ws://example", StubBus(), logging.getLogger("test"))

        state = SimpleNamespace(closing=False, audio_queue=StubQueue(), loop=StubLoop())
        sid = "sid-123"
        client._streams[sid] = state  # type: ignore[attr-defined]

        client.close_stream(sid)

        self.assertIn(sid, client._streams)
        self.assertTrue(state.closing)


def test_open_stream_blocks_until_other_open_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubBus:
        def publish(self, event: object) -> None:
            pass

    class StubWebSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False

        async def send(self, data: str) -> None:
            self.sent.append(data)

        async def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return

    async def run_test() -> None:
        client = SpeechmaticsClient("key", "ws://example", StubBus(), logging.getLogger("test"))

        async def sender_stub(self: SpeechmaticsClient, state: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
            await state.ready_event.wait()

        async def receiver_stub(self: SpeechmaticsClient, state: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
            state.ready_event.set()

        async def shutdown_stub(self: SpeechmaticsClient, state: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
            state.closed = True
            state.ready_event.set()
            self._streams.pop(state.sid, None)
            event = self._closing.pop(state.sid, None)
            if event is not None:
                event.set()

        client._sender_loop = MethodType(sender_stub, client)  # type: ignore[assignment]
        client._receiver_loop = MethodType(receiver_stub, client)  # type: ignore[assignment]
        client._shutdown = MethodType(shutdown_stub, client)  # type: ignore[assignment]

        first_sid = "sid-1"
        second_sid = "sid-2"
        first_stream_populated = asyncio.Event()

        class TrackingStreams(dict[str, speechmatics_module._StreamState]):  # type: ignore[name-defined]
            def __setitem__(self, key: str, value: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
                super().__setitem__(key, value)
                if key == first_sid:
                    first_stream_populated.set()

        client._streams = TrackingStreams()

        connect_calls: list[int] = []
        first_connect_started = asyncio.Event()
        allow_first_to_finish = asyncio.Event()
        second_connect_started = asyncio.Event()

        async def fake_connect(url: str, **_: object) -> StubWebSocket:
            call_index = len(connect_calls)
            connect_calls.append(call_index)
            if call_index == 0:
                first_connect_started.set()
                await allow_first_to_finish.wait()
            else:
                second_connect_started.set()
            return StubWebSocket()

        monkeypatch.setattr(speechmatics_module.websockets, "connect", fake_connect)

        async def open_for_sid(sid: str) -> str:
            return await client.open_stream(
                sid=sid,
                stream_id=f"stream-{sid}",
                on_partial=lambda *_: None,
                on_final=lambda *_: None,
                on_error=lambda *_: None,
            )

        first_task = asyncio.create_task(open_for_sid(first_sid))
        await first_connect_started.wait()

        second_task = asyncio.create_task(open_for_sid(second_sid))
        await asyncio.sleep(0.05)

        assert len(connect_calls) == 1
        assert not second_connect_started.is_set()

        allow_first_to_finish.set()
        await asyncio.wait_for(first_stream_populated.wait(), timeout=1.0)
        await asyncio.wait_for(first_task, timeout=1.0)

        client.close_stream(first_sid)
        await asyncio.sleep(0.05)

        await asyncio.wait_for(second_connect_started.wait(), timeout=1.0)
        await asyncio.wait_for(second_task, timeout=1.0)

        assert len(connect_calls) == 2

    asyncio.run(run_test())


def test_reopen_waits_for_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubBus:
        def publish(self, event: object) -> None:
            pass

    class StubWebSocket:
        async def send(self, data: object) -> None:
            return

        async def close(self) -> None:
            return

        async def wait_closed(self) -> None:
            return

    async def run_test() -> None:
        client = SpeechmaticsClient("key", "ws://example", StubBus(), logging.getLogger("test"))

        async def sender_stub(self: SpeechmaticsClient, state: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
            await state.ready_event.wait()

        async def receiver_stub(self: SpeechmaticsClient, state: speechmatics_module._StreamState) -> None:  # type: ignore[attr-defined]
            state.ready_event.set()

        original_finalize_close = client._finalize_close

        finalize_started = asyncio.Event()
        allow_finalize = asyncio.Event()

        async def finalize_stub(
            self: SpeechmaticsClient,
            state: speechmatics_module._StreamState,
            code: int | None,
            reason: str | None,
        ) -> None:  # type: ignore[attr-defined]
            finalize_started.set()
            await allow_finalize.wait()
            await original_finalize_close(state, code, reason)

        client._sender_loop = MethodType(sender_stub, client)  # type: ignore[assignment]
        client._receiver_loop = MethodType(receiver_stub, client)  # type: ignore[assignment]
        client._finalize_close = MethodType(finalize_stub, client)  # type: ignore[assignment]

        connect_calls: list[int] = []
        second_connect_started = asyncio.Event()

        async def fake_connect(url: str, **_: object) -> StubWebSocket:
            call_index = len(connect_calls)
            connect_calls.append(call_index)
            if call_index == 1:
                second_connect_started.set()
            return StubWebSocket()

        monkeypatch.setattr(speechmatics_module.websockets, "connect", fake_connect)

        async def open_for_sid() -> str:
            return await client.open_stream(
                sid="sid-1",
                stream_id="stream-sid-1",
                on_partial=lambda *_: None,
                on_final=lambda *_: None,
                on_error=lambda *_: None,
            )

        first_task = asyncio.create_task(open_for_sid())
        await asyncio.wait_for(first_task, timeout=1.0)

        assert len(connect_calls) == 1

        client.close_stream("sid-1")
        await asyncio.wait_for(finalize_started.wait(), timeout=1.0)

        second_task = asyncio.create_task(open_for_sid())
        await asyncio.sleep(0.05)

        assert len(connect_calls) == 1
        assert not second_connect_started.is_set()

        allow_finalize.set()

        await asyncio.wait_for(second_connect_started.wait(), timeout=1.0)
        await asyncio.wait_for(second_task, timeout=1.0)

        assert len(connect_calls) == 2

        client.close_stream("sid-1")
        await asyncio.sleep(0.05)

    asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
