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

    probe_logs = [rec.message for rec in caplog.records if "auto_ready_probe" in rec.message]
    assert not probe_logs

    summaries = [rec.message for rec in caplog.records if "audio_bridge_summary" in rec.message]
    assert summaries and all("rx_bytes=0" not in msg for msg in summaries)

    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]
    assert len(turn_summaries) == 2
    assert any("turn_index=1" in msg for msg in turn_summaries)
    assert any("turn_index=2" in msg for msg in turn_summaries)

    turn_start_logs = [
        rec
        for rec in caplog.records
        if "evt=turn_lifecycle" in rec.message and "phase=turn_start" in rec.message
    ]
    assert len(turn_start_logs) == 2

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert len(user_turns) == 2
    assert {frame.get("turn_index") for frame in user_turns} == {1, 2}
    assert all(frame.get("text") for frame in user_turns)


def test_post_greet_auto_ready_probe_timeout(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> list[dict]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-probe-timeout", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True
        ctx.greet_completed = True

        adapter._schedule_asr_open(ctx, as_probe=True)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        await adapter._handle_asr_result(
            ctx, "", True, promoted_final=True, timeout=True
        )
        return sent

    sent_frames = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    assert not [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert not [frame for frame in parsed_frames if frame.get("type") == "turn.empty"]

    probe_logs = [rec.message for rec in caplog.records if "auto_ready_probe_timeout" in rec.message]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]
    assert probe_logs
    assert not turn_summaries


def test_post_greet_probe_then_real_turn(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> list[dict]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-probe-then-real", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True
        ctx.greet_completed = True

        adapter._schedule_asr_open(ctx, as_probe=True)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        await adapter._handle_asr_result(
            ctx, "", True, promoted_final=True, timeout=True
        )

        adapter._schedule_asr_open(ctx)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        await adapter._ingest_audio_chunk(ctx, b"\x01\x02" * 50, seq=0)
        ctx.session.audio_rx_bytes += len(b"\x01\x02" * 50)
        ctx.session.audio_rx_chunks += 1
        await adapter._handle_asr_result(ctx, "hello after greet", True)

        return sent

    sent_frames = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert len(user_turns) == 1
    assert user_turns[0].get("turn_index") == 1
    assert user_turns[0].get("text")

    bridge_logs = [rec.message for rec in caplog.records if "audio_bridge_turn_start" in rec.message]
    lifecycle_logs = [rec.message for rec in caplog.records if "turn_lifecycle" in rec.message]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]

    assert any("turn=1" in msg for msg in bridge_logs)
    assert any("turn_index=1" in msg and "phase=turn_start" in msg for msg in lifecycle_logs)
    assert len(turn_summaries) == 1


def test_post_greet_probe_promoted_by_audio(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> list[dict]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-probe-promoted", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True
        ctx.greet_completed = True

        adapter._schedule_asr_open(ctx, as_probe=True)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        assert ctx.auto_ready_probe_active is True

        audio = b"\x01\x02" * 50
        await adapter._ingest_audio_chunk(ctx, audio, seq=0)
        ctx.session.audio_rx_bytes += len(audio)
        ctx.session.audio_rx_chunks += 1
        ctx.asr_bytes_sent = ctx.asr_bytes_sent or len(audio)
        ctx.bytes_from_client_this_turn = max(ctx.bytes_from_client_this_turn, len(audio))

        await adapter._handle_asr_result(ctx, "hello after greet", True)

        return sent

    sent_frames = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert len(user_turns) == 1
    assert user_turns[0].get("turn_index") == 1
    assert user_turns[0].get("text")

    bridge_logs = [rec.message for rec in caplog.records if "audio_bridge_turn_start" in rec.message]
    lifecycle_logs = [rec.message for rec in caplog.records if "turn_lifecycle" in rec.message]
    probe_logs = [rec.message for rec in caplog.records if "auto_ready_probe_promoted" in rec.message]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]

    assert any("turn=1" in msg for msg in bridge_logs)
    assert any("turn_index=1" in msg and "phase=turn_start" in msg for msg in lifecycle_logs)
    assert probe_logs
    assert len(probe_logs) == 1
    assert len([
        rec
        for rec in caplog.records
        if "evt=turn_lifecycle" in rec.message and "phase=turn_start" in rec.message
    ]) == 1
    assert len(turn_summaries) == 1
    assert any("turn_index=1" in msg and "outcome=ok" in msg for msg in turn_summaries)


def test_post_greet_auto_ready_arm_uses_probe(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> tuple[list[dict], list[bool], AdapterContext]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-post-greet-arm", headers={})

        sent: list[dict] = []

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True
        ctx.greet_completed = True
        ctx.current_turn_open = True

        calls: list[bool] = []
        original = adapter._schedule_asr_open

        def _spy(self: ChatV2Adapter, ctx_param: AdapterContext, *, as_probe: bool = False) -> None:
            calls.append(as_probe)
            return original(ctx_param, as_probe=as_probe)

        adapter._schedule_asr_open = types.MethodType(_spy, adapter)

        await adapter._ensure_asr_ready(ctx.ws_send, ctx, "greet_end")

        if ctx.asr_open_task:
            await ctx.asr_open_task

        return sent, calls, ctx

    sent_frames, calls, ctx = asyncio.run(_run())
    parsed_frames = [json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")]

    assert calls and calls[0] is True
    assert ctx.auto_ready_probe_active is True

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert not user_turns

    early_logs = [
        rec.message
        for rec in caplog.records
        if "turn_lifecycle" in rec.message or "audio_bridge_turn_start" in rec.message
    ]
    assert not early_logs

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


def test_empty_final_then_real_final_same_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    empty_turn_summaries: list[str] = []

    async def _run() -> tuple[list[dict], AdapterContext, int, bool, bool]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-empty-then-real", headers={})

        sent: list[dict] = []

        async def _drain_tasks() -> None:
            await asyncio.sleep(0)
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True

        adapter._schedule_asr_open(ctx)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        engine = ctx.session.asr_engine
        assert engine is not None

        engine._handle_result("", True)
        await _drain_tasks()

        empty_turn_summaries.extend(
            rec.message for rec in caplog.records if "evt=turn_summary" in rec.message
        )

        initial_final_already = len(
            [rec for rec in caplog.records if "final_already_emitted" in rec.message]
        )
        empty_turn_open = ctx.current_turn_open
        empty_final_emitted = ctx.asr_final_emitted

        engine._handle_result("hello world", True)
        await _drain_tasks()

        engine._handle_result("ignored after real final", True)
        await _drain_tasks()

        return sent, ctx, initial_final_already, empty_turn_open, empty_final_emitted

    sent_frames, ctx, initial_final_already, empty_turn_open, empty_final_emitted = asyncio.run(_run())
    parsed_frames = [
        json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")
    ]

    user_turns = [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]
    decision_logs = [
        rec.message for rec in caplog.records if "EVT_LLM_TURN_DECISION" in rec.message
    ]
    final_already_logs = [
        rec.message for rec in caplog.records if "final_already_emitted" in rec.message
    ]

    assert empty_turn_summaries == []
    assert empty_turn_open is True
    assert empty_final_emitted is False
    assert ctx.asr_final_emitted is True
    assert ctx.current_turn_open is False

    assert user_turns and user_turns[0].get("text") == "hello world"
    assert any("decision=llm_turn" in msg for msg in decision_logs)
    assert any("outcome=ok" in msg for msg in turn_summaries)

    assert initial_final_already == 0


def test_empty_final_timeout_flow(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> tuple[list[dict], AdapterContext]:
        caplog.set_level(logging.INFO)
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-empty-timeout", headers={})

        sent: list[dict] = []

        async def _drain_tasks() -> None:
            await asyncio.sleep(0)
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        async def ws_send(message: dict) -> None:
            sent.append(message)

        ctx.ws_send = ws_send
        ctx.client_mic_open = True
        ctx.asr_ready = True

        adapter._schedule_asr_open(ctx)
        if ctx.asr_open_task:
            await ctx.asr_open_task

        engine = ctx.session.asr_engine
        assert engine is not None

        engine._handle_result("", True)
        await _drain_tasks()
        engine._handle_result("", True)
        await _drain_tasks()

        await adapter._handle_asr_result(
            ctx, "", True, promoted_final=True, timeout=True
        )

        return sent, ctx

    sent_frames, ctx = asyncio.run(_run())
    parsed_frames = [
        json.loads(msg.get("text")) for msg in sent_frames if isinstance(msg, dict) and msg.get("text")
    ]

    empty_logs = [
        rec.message for rec in caplog.records if "evt=asr_empty_final_ignored" in rec.message
    ]
    repeat_logs = [
        rec.message for rec in caplog.records if "evt=asr_empty_final_repeat" in rec.message
    ]
    turn_summaries = [rec.message for rec in caplog.records if "evt=turn_summary" in rec.message]
    final_already_logs = [
        rec.message for rec in caplog.records if "final_already_emitted" in rec.message
    ]

    assert len(empty_logs) >= 1
    assert repeat_logs
    assert any("outcome=timeout_no_audio" in msg for msg in turn_summaries)
    assert not final_already_logs

    assert not [frame for frame in parsed_frames if frame.get("type") == "user.turn"]
    assert any(frame.get("type") == "turn.empty" for frame in parsed_frames)
