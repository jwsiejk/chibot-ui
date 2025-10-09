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
