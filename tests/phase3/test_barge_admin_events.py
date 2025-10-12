from app.ws.barge import BargeState
from app.ws import ws_asgi
from app.ws.bus import bus
from app.db import db


def _capture_admin_events(monkeypatch):
    events = []

    def _emit(name, **payload):
        events.append((name, payload))

    monkeypatch.setattr(ws_asgi, "_admin_emit", _emit)
    return events


def test_barge_admin_pause_resume_cancel(monkeypatch):
    sid = "test-barge-admin"
    events = _capture_admin_events(monkeypatch)

    tts_tbl = db.memory.setdefault("tts_status", {})
    tts_tbl[sid] = {
        "turn-1": {
            "started": True,
            "first_chunk": True,
            "done": False,
            "error": None,
        }
    }

    bus.note_assistant_turn(sid, "turn-1")

    last_phase = [None]

    def capture(phase: str) -> None:
        ws_asgi._emit_barge_admin_events(sid, phase, last_phase[0])
        last_phase[0] = phase

    barge = BargeState()

    try:
        assert barge.start(confirm_ms=50, on_commit=lambda: None, send_state=capture)
        barge.cancel(capture)

        assert barge.start(confirm_ms=50, on_commit=lambda: None, send_state=capture)
        barge.commit(capture)
    finally:
        bus.note_assistant_turn(sid, None)
        tts_tbl.pop(sid, None)

    event_names = [name for name, _ in events]

    assert event_names == [
        "barge_in",
        "tts_pause",
        "barge_resume",
        "tts_resume",
        "barge_in",
        "tts_pause",
        "barge_commit",
        "tts_cancel",
    ]

    for name, payload in events:
        assert payload.get("session_id") == sid
        assert payload.get("phase") in {"paused", "assistant_speaking", "ready"}
        if name.startswith("tts_"):
            assert payload.get("tts_state")
