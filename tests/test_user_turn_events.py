import asyncio
import json
import logging
import os
import sys
import types

import pytest

# Provide a minimal jwt stub so adapter imports succeed under pytest without PyJWT installed.
os.environ.setdefault("SECRET_KEY", "test-secret")
if "jwt" not in sys.modules:
    sys.modules["jwt"] = types.SimpleNamespace(
        encode=lambda *args, **kwargs: "", decode=lambda *args, **kwargs: {}, PyJWTError=Exception
    )

if "google" not in sys.modules:
    google_mod = types.ModuleType("google")
    api_core = types.ModuleType("google.api_core")
    api_core.exceptions = types.SimpleNamespace(OutOfRange=Exception)
    cloud_mod = types.ModuleType("google.cloud")

    class _DummyAudioEncoding:
        LINEAR16 = 1

    class _DummyRecognitionConfig:
        AudioEncoding = _DummyAudioEncoding

        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyStreamingRecognitionConfig:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyStreamingRecognizeRequest:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummySpeechClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def streaming_recognize(self, *args, **kwargs):
            async def _aiter():
                if False:
                    yield None
                return

            class _Wrapper:
                def __aiter__(self_inner):
                    return _aiter()

            return _Wrapper()

    speech_mod = types.ModuleType("google.cloud.speech")
    speech_mod.SpeechClient = _DummySpeechClient
    speech_mod.RecognitionConfig = _DummyRecognitionConfig
    speech_mod.StreamingRecognitionConfig = _DummyStreamingRecognitionConfig
    speech_mod.StreamingRecognizeRequest = _DummyStreamingRecognizeRequest
    speech_mod.RecognitionAudio = object

    sys.modules["google"] = google_mod
    sys.modules["google.api_core"] = api_core
    sys.modules["google.api_core.exceptions"] = api_core.exceptions
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.speech"] = speech_mod

from app.telemetry import bus
from app.ws.adapter import AdapterContext, ChatV2Adapter
from app.ws.state import mark


@pytest.fixture(autouse=True)
def reset_bus() -> None:
    bus.reset()
    yield
    bus.reset()


def test_audio_bridge_alignment_and_user_turn(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> list[dict]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-multi-turn", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True

        for idx in range(2):
            adapter._schedule_asr_open(ctx)
            if ctx.asr_open_task:
                await ctx.asr_open_task
            await adapter._ingest_audio_chunk(ctx, b"\x01\x02" * 50, seq=idx)
            ctx.session.audio_rx_bytes += len(b"\x01\x02" * 50)
            ctx.session.audio_rx_chunks += 1
            await adapter._handle_asr_result(ctx, f"hi {idx + 1}", True)

        return sent

    sent_frames = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    bridge_logs = [rec.message for rec in caplog.records if "audio_bridge_turn_start" in rec.message]
    assert any("turn=1" in msg for msg in bridge_logs)
    assert any("turn=2" in msg for msg in bridge_logs)

    summaries = [rec.message for rec in caplog.records if "audio_bridge_summary" in rec.message]
    assert summaries and all("rx_bytes=0" not in msg for msg in summaries)

    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]
    assert len(turn_summaries) == 2
    assert any("turn_index=1" in msg for msg in turn_summaries)
    assert any("turn_index=2" in msg for msg in turn_summaries)

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert len(user_turns) == 2
    assert {frame.get("turn_index") for frame in user_turns} == {1, 2}
    assert all(frame.get("text") for frame in user_turns)


def test_user_turn_dedup_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> list[dict]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-dedupe", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.turn_req_id = ctx.active_req_id = "req-dedupe"
        ctx.current_turn_id = "turn-dedupe"
        ctx.turn_index = 1
        ctx.asr_open = True
        ctx.asr_stream_id = "stream-dedupe"
        mark(ctx.session, "open")

        await adapter._handle_asr_result(ctx, "repeat me", True)

        ctx.turn_req_id = ctx.active_req_id = "req-dedupe"
        ctx.current_turn_id = "turn-dedupe"
        ctx.asr_open = True
        ctx.asr_final_emitted = False
        ctx.asr_stream_id = "stream-dedupe"
        mark(ctx.session, "open")

        await adapter._handle_asr_result(ctx, "repeat me", True)

        return sent

    sent_frames = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert len(user_turns) == 1

    emit_logs = [rec.message for rec in caplog.records if "evt=user_turn_event " in rec.message]
    dedup_logs = [rec.message for rec in caplog.records if "evt=user_turn_event_dedup" in rec.message]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]

    assert len(emit_logs) == 1
    assert len(dedup_logs) == 1
    assert len(turn_summaries) == 1
