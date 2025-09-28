import types

from app.services import streaming


def _stub_config(monkeypatch):
    monkeypatch.setattr(streaming.db, "get_config", lambda: {})


def test_make_assistant_frames_ws_skips_legacy(monkeypatch):
    _stub_config(monkeypatch)

    legacy_called = False

    def fake_legacy(*args, **kwargs):  # pragma: no cover - should never be invoked
        nonlocal legacy_called
        legacy_called = True
        return "legacy", [{"type": "assistant_end", "turn_id": "legacy"}]

    monkeypatch.setattr(streaming, "_make_legacy_frames", fake_legacy)
    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *_: False)

    tid, frames = streaming.make_assistant_frames(
        "hello",
        "session-ws",
        meta={"source": "user_ws"},
    )

    assert tid is None
    assert frames == []
    assert legacy_called is False


def test_make_assistant_frames_http_allows_legacy(monkeypatch):
    _stub_config(monkeypatch)

    captured = types.SimpleNamespace(called=False)

    def fake_legacy(seed_text, session_id, meta, cfg, **kwargs):
        captured.called = True
        return "tid-123", [
            {"type": "assistant_chunk", "turn_id": "tid-123", "text": "hi", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "tid-123"},
        ]

    monkeypatch.setattr(streaming, "_make_legacy_frames", fake_legacy)
    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *_: False)

    tid, frames = streaming.make_assistant_frames(
        "hello",
        "session-http",
        meta={"source": "http_chat"},
    )

    assert tid == "tid-123"
    assert frames and frames[-1]["type"] == "assistant_end"
    assert captured.called is True


def test_make_assistant_frames_health_check_allows_legacy(monkeypatch):
    _stub_config(monkeypatch)

    legacy_calls = []

    def fake_legacy(seed_text, session_id, meta, cfg, **kwargs):
        legacy_calls.append((seed_text, meta))
        return "health", [
            {"type": "assistant_chunk", "turn_id": "health", "text": "ok", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "health"},
        ]

    monkeypatch.setattr(streaming, "_make_legacy_frames", fake_legacy)
    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *_: False)

    tid, _ = streaming.make_assistant_frames(
        "status",
        "session-health",
        meta={"source": "health_check"},
    )

    assert tid == "health"
    assert legacy_calls and legacy_calls[0][0] == "status"
