import asyncio
import logging

import pytest

from tests.test_listen_handoff import ListenHarness, wait_for


def test_mask_jitter_aborts_and_reschedules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        harness = ListenHarness(monkeypatch)
        await harness.start()
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")

            await harness.set_mask("on")
            await harness.set_mask("off")
            await wait_for(lambda: harness.ctx.listen_handoff_task is not None)

            await harness.set_mask("on")
            await asyncio.sleep(0.05)

            assert any(
                "evt=listen_handoff_aborted" in record.message and "reason=mask_on" in record.message
                for record in caplog.records
            )

            await harness.set_mask("off")
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()
            await wait_for(lambda: len(harness.ws.frames_of_type("asr.ready")) == 1)
            await wait_for(lambda: len(harness.ws.frames_of_type("input.start")) == 1)

            frames = [
                frame
                for frame in harness.ws.text_frames
                if frame.get("type") in {"asr.ready", "input.start"}
            ]
            assert [frame.get("type") for frame in frames] == ["asr.ready", "input.start"]
        finally:
            await harness.stop()

    asyncio.run(_run())
