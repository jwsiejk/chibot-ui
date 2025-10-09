import asyncio
import threading
import time
import uuid
from queue import Empty

import pytest

from app.session_state import set_phase, set_recorder_active
from app.ws.barge import BargeState
from app.ws import ws_asgi
from app.ws.bus import bus


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_barge_state_emits_distinct_phases():
    sid = f"test-barge-{uuid.uuid4()}"
    emitted = []

    def capture(phase: str) -> None:
        if ws_asgi._should_emit_barge_phase(sid, phase):
            emitted.append(phase)

    barge = BargeState()

    assert barge.start(confirm_ms=1000, on_commit=lambda: None, send_state=capture) is True
    barge.cancel(capture)
    barge.cancel(capture)

    assert barge.start(confirm_ms=1000, on_commit=lambda: None, send_state=capture) is True
    barge.commit(capture)
    barge.commit(capture)

    assert barge.start(confirm_ms=1000, on_commit=lambda: None, send_state=capture) is True
    barge.cancel(capture)
    barge.cancel(capture)

    assert emitted == [
        "paused",
        "assistant_speaking",
        "paused",
        "ready",
        "paused",
        "assistant_speaking",
    ]


def test_barge_commit_waits_for_ready_gate():
    sid = f"test-barge-wait-{uuid.uuid4()}"
    phases = []

    def capture(phase: str) -> None:
        if ws_asgi._should_emit_barge_phase(sid, phase):
            phases.append(phase)

    ready_evt = threading.Event()

    def on_commit():
        phases.append("commit")
        return ready_evt.wait

    barge = BargeState()

    assert barge.start(confirm_ms=500, on_commit=on_commit, send_state=capture) is True

    worker = threading.Thread(target=barge.commit, args=(capture,))
    worker.daemon = True
    worker.start()

    time.sleep(0.05)
    assert "ready" not in phases

    ready_evt.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert phases[-1] == "ready"


@pytest.mark.anyio
async def test_barge_state_single_broadcast_for_client():
    sid = f"test-barge-subscriber-{uuid.uuid4()}"
    barge = BargeState()
    frames = []
    q = bus.subscribe(sid)

    def capture(phase: str) -> None:
        ws_asgi._broadcast_barge_state_frame(sid, phase)

    try:
        assert barge.start(confirm_ms=1000, on_commit=lambda: None, send_state=capture)
        barge.commit(capture)

        # allow any background tasks to process
        await asyncio.sleep(0)

        while True:
            try:
                frames.append(q.get_nowait())
            except Empty:
                break
    finally:
        bus.unsubscribe(sid, q)
        set_recorder_active(sid, False)
        set_phase(sid, "", emitted=True)

    state_phases = [fr.get("phase") for fr in frames if fr.get("type") == "state"]

    assert state_phases == ["paused", "ready"]
