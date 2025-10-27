import asyncio
import logging
import os
from typing import Optional

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app import config
from app.ws import adapter as adapter_module
from app.voice_v2 import EVT_WS_JSON_SEND

from tests.test_listen_handoff import ListenHarness, wait_for


def attach_no_audio_bridge(harness: ListenHarness) -> None:
    logger = logging.getLogger(adapter_module.__name__)

    def _forward(event: dict) -> None:
        if event.get("type") != "EVT_DIAG_NO_AUDIO_FROM_CLIENT":
            return
        req_id: Optional[str] = harness.last_req_id
        logger.warning(
            "evt=listen_no_audio_watchdog diag=no_audio_after_listen sid=%s req_id=%s",
            harness.ctx.sid,
            req_id,
        )
        frame = {"type": "diag", "key": "no_audio"}
        if req_id:
            frame["req_id"] = req_id
        harness.bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.ctx.sid, "payload": frame})

    harness.bus.subscribe("EVT_DIAG_NO_AUDIO_FROM_CLIENT", _forward)


def _trigger_no_audio(harness: ListenHarness, req_id: str) -> None:
    loop = asyncio.get_running_loop()
    key = harness.adapter._turn_key(harness.ctx, req_id)
    harness.ctx.diag_timer_key = key
    harness.ctx.tts_end_ts = adapter_module.time.monotonic() - 9.0
    harness.ctx.diag_audio_seen = False
    harness.adapter._emit_no_audio_diag(harness.ctx, loop, key)


def test_no_audio_watchdog_emits_diag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(config, "DIAG_AUDIO_GUARD", True)
        harness = ListenHarness(monkeypatch)
        await harness.start()
        attach_no_audio_bridge(harness)
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()

            _trigger_no_audio(harness, "r1")
            await asyncio.sleep(0.05)

            diag_events = [
                evt for evt in harness.bus.published if evt.get("type") == "EVT_DIAG_NO_AUDIO_FROM_CLIENT"
            ]
            assert len(diag_events) == 1

            assert any("diag=no_audio_after_listen" in record.message for record in caplog.records)
        finally:
            await harness.stop()

    asyncio.run(_run())


def test_no_audio_watchdog_cancels_on_audio(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(config, "DIAG_AUDIO_GUARD", True)
        harness = ListenHarness(monkeypatch)
        await harness.start()
        attach_no_audio_bridge(harness)
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()

            loop = asyncio.get_running_loop()
            key = harness.adapter._turn_key(harness.ctx, "r1")
            harness.ctx.diag_timer_key = key
            harness.ctx.tts_end_ts = adapter_module.time.monotonic() - 9.0
            harness.ctx.diag_audio_seen = True
            harness.adapter._emit_no_audio_diag(harness.ctx, loop, key)
            await asyncio.sleep(0.05)

            diag_events = [
                evt for evt in harness.bus.published if evt.get("type") == "EVT_DIAG_NO_AUDIO_FROM_CLIENT"
            ]
            assert diag_events == []
            assert not any("diag=no_audio_after_listen" in record.message for record in caplog.records)
        finally:
            await harness.stop()

    asyncio.run(_run())


def test_no_audio_watchdog_cleared_on_cleanup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        monkeypatch.setattr(config, "DIAG_AUDIO_GUARD", True)
        harness = ListenHarness(monkeypatch)
        await harness.start()
        attach_no_audio_bridge(harness)
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()

            loop = asyncio.get_running_loop()
            key = harness.adapter._turn_key(harness.ctx, "r1")
            harness.ctx.diag_timer_key = key
            harness.ctx.tts_end_ts = adapter_module.time.monotonic() - 9.0

            await harness.stop()

            harness.adapter._emit_no_audio_diag(harness.ctx, loop, key)
            await asyncio.sleep(0.05)

            diag_events = [
                evt for evt in harness.bus.published if evt.get("type") == "EVT_DIAG_NO_AUDIO_FROM_CLIENT"
            ]
            assert diag_events == []
            assert not any("diag=no_audio_after_listen" in record.message for record in caplog.records)
        finally:
            await harness.stop()

    asyncio.run(_run())
