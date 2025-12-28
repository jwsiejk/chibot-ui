import asyncio
import json
import logging
import os
import sys
import types

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
if "jwt" not in sys.modules:
    sys.modules["jwt"] = types.SimpleNamespace(
        encode=lambda *args, **kwargs: "", decode=lambda *args, **kwargs: {}, PyJWTError=Exception
    )

from app.ws.adapter import AdapterContext, ChatV2Adapter


async def _noop_send(_: dict) -> None:
    return None


def _make_v3_ctx() -> AdapterContext:
    ctx = AdapterContext(sid="sid-v3", headers={})
    ctx.v3_enabled = True
    ctx.greet_completed = True
    ctx.asr_stream_id = "stream"
    ctx.ws_send = _noop_send
    return ctx


def test_pcm_before_turn_start_is_idempotent() -> None:
    async def _run() -> tuple[str | None, int]:
        adapter = ChatV2Adapter()
        ctx = _make_v3_ctx()

        await adapter._handle_binary(b"\x00\x01", ctx, _noop_send)
        turn_id = ctx.current_turn_id
        turn_index = ctx.turn_index

        payload = {
            "type": "client.turn_start",
            "turn_id": "client-turn-1",
            "sample_rate_hz": 16000,
        }
        await adapter._handle_text(json.dumps(payload), ctx, _noop_send)
        assert ctx.turn_index == turn_index
        assert ctx.current_turn_id == turn_id
        return turn_id, turn_index

    turn_id, turn_index = asyncio.run(_run())
    assert turn_id
    assert turn_index == 1


def test_turn_start_before_pcm_is_idempotent() -> None:
    async def _run() -> tuple[str | None, int]:
        adapter = ChatV2Adapter()
        ctx = _make_v3_ctx()

        payload = {
            "type": "client.turn_start",
            "turn_id": "client-turn-2",
            "sample_rate_hz": 16000,
        }
        await adapter._handle_text(json.dumps(payload), ctx, _noop_send)
        turn_id = ctx.current_turn_id
        turn_index = ctx.turn_index

        await adapter._handle_binary(b"\x00\x01", ctx, _noop_send)
        assert ctx.turn_index == turn_index
        assert ctx.current_turn_id == turn_id
        return turn_id, turn_index

    turn_id, turn_index = asyncio.run(_run())
    assert turn_id == "client-turn-2"
    assert turn_index == 1


def test_turn_stop_before_final_keeps_lifecycle_clean(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> None:
        adapter = ChatV2Adapter()
        ctx = _make_v3_ctx()

        payload = {"type": "client.turn_start", "turn_id": "client-turn-3"}
        await adapter._handle_text(json.dumps(payload), ctx, _noop_send)
        await adapter._handle_text(
            json.dumps({"type": "client.turn_stop", "turn_id": "client-turn-3", "reason": "vad"}),
            ctx,
            _noop_send,
        )
        await adapter._handle_asr_result(ctx, "hello", True)

    caplog.set_level(logging.INFO)
    asyncio.run(_run())

    summaries = [
        rec.message for rec in caplog.records if "evt=turn_lifecycle_summary" in rec.message
    ]
    assert summaries
    assert "illegal_count=0" in summaries[-1]


def test_duplicate_start_and_stop_are_idempotent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> None:
        adapter = ChatV2Adapter()
        ctx = _make_v3_ctx()

        payload = {"type": "client.turn_start", "turn_id": "client-turn-4"}
        await adapter._handle_text(json.dumps(payload), ctx, _noop_send)
        await adapter._handle_text(json.dumps(payload), ctx, _noop_send)
        assert ctx.turn_index == 1

        stop_payload = {"type": "client.turn_stop", "turn_id": "client-turn-4", "reason": "vad"}
        await adapter._handle_text(json.dumps(stop_payload), ctx, _noop_send)
        await adapter._handle_text(json.dumps(stop_payload), ctx, _noop_send)
        await adapter._handle_asr_result(ctx, "hello again", True)

    caplog.set_level(logging.INFO)
    asyncio.run(_run())

    open_logs = [rec for rec in caplog.records if "evt=turn_open" in rec.message]
    close_logs = [rec for rec in caplog.records if "evt=turn_close" in rec.message]
    assert len(open_logs) == 1
    assert len(close_logs) == 1
