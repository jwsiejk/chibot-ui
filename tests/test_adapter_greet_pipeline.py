import asyncio
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.ws.adapter import ChatV2Adapter, AdapterContext


def test_greet_turn_initializes_and_logs_once(monkeypatch):
    async def _run() -> None:
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-greet", headers={})

        # Prevent telemetry helpers from running real side effects during the unit test
        async def _noop_send_json(*args, **kwargs):
            return None

        monkeypatch.setattr(adapter, "_emit_session_step", lambda *args, **kwargs: None)
        monkeypatch.setattr(adapter, "_bus", lambda *args, **kwargs: None)
        monkeypatch.setattr(adapter, "_send_json", _noop_send_json)

        logged: list[tuple[str, int | None]] = []

        def _capture_tts_timeline(
            sid: str, turn_index: int | None, *args, **kwargs
        ) -> None:
            logged.append((sid, turn_index))

        monkeypatch.setattr(
            "app.ws.adapter._log_tts_timeline_event", _capture_tts_timeline, raising=True
        )

        frame = {"utt_id": "utt-1", "meta": {"is_greet": True, "provider": "elevenlabs"}}

        await adapter._handle_tts_start(None, ctx, frame)
        await adapter._handle_tts_end(None, ctx, frame)
        await adapter._handle_tts_end(None, ctx, frame)

        assert ctx.metrics.get("turn_index") == 0
        assert ctx.session.turn_index == 0
        assert logged == [(ctx.sid, 0)]

    asyncio.run(_run())
