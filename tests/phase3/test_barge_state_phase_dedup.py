import threading
import time
import uuid

from app.ws.barge import BargeState
from app.ws import ws_asgi


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
