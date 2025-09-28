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
        assert isinstance(meta.get("nlu"), dict)
        assert "action" in meta
        assert "verbosity" in meta
        assert "show_suggestions" in meta
        assert isinstance(kwargs.get("labels"), dict)
        assert isinstance(kwargs.get("policy"), dict)
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
        assert isinstance(meta.get("nlu"), dict)
        assert "action" in meta
        assert "verbosity" in meta
        assert "show_suggestions" in meta
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


def test_prepare_policy_meta_injects_fields(monkeypatch):
    _stub_config(monkeypatch)

    seed_meta = {"source": "http_chat"}
    prepared, labels, policy = streaming.prepare_policy_meta("How do I deploy?", seed_meta)

    assert prepared is seed_meta
    assert isinstance(prepared.get("nlu"), dict)
    assert prepared["action"]
    assert prepared["verbosity"] == "medium"
    assert prepared["show_suggestions"] is True
    assert isinstance(labels, dict) and labels.get("intent")
    assert isinstance(policy, dict)


def test_run_ws_user_turn_emits_action_metadata_once(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *_: False)
    monkeypatch.setattr(streaming, "kb_search", lambda *a, **k: [])
    monkeypatch.setattr(streaming, "_get_persona_for_session", lambda sid: {"id": "chip"})

    class DummyProvider:
        def generate_reply(self, *args, **kwargs):
            return "Hello!"

    monkeypatch.setattr(streaming, "get_provider", lambda cfg: DummyProvider())
    monkeypatch.setattr(streaming, "_broadcast_frames", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "schedule_frames", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: None)

    captured = []

    def fake_admin_emit(event, **payload):
        captured.append((event, payload))

    monkeypatch.setattr(streaming, "_admin_emit", fake_admin_emit)

    monkeypatch.setattr(streaming, "_should_skip_legacy", lambda *_: False)

    tid = streaming.run_ws_user_turn("session-meta", "Hello there", correlation_user_msg_id="abc123")

    assert tid
    assert len(captured) == 1

    event, payload = captured[0]
    assert event == "turn_action_metadata"
    assert payload["turn_id"] == tid
    assert payload["is_greet"] is False
    assert payload["nlu"]["intent"] == "statement"
    assert payload["policy"]["action"] == "offer_steps"
    assert payload["policy"]["show_suggestions"] is True
