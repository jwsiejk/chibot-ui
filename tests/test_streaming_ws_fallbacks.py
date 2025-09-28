import types

from app.services import streaming


def _stub_config(monkeypatch):
    monkeypatch.setattr(streaming.db, "get_config", lambda: {})


def test_short_greeting_preserves_sentence():
    text = "What should we clarify so I can help?"
    assert streaming._short_greeting(text) == text


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


def test_run_ws_greet_uses_foundation_frames(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "ENABLE_CHIP_FOUNDATION", True)

    captured = {"called": 0}

    def fake_foundation(seed_text, session_id, meta, cfg, **kwargs):
        captured["called"] += 1
        captured["seed_text"] = seed_text
        turn_id = kwargs.get("force_turn_id") or "tid-foundation"
        frames = [
            {"type": "assistant_chunk", "turn_id": turn_id, "text": "foundation hello", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": turn_id},
        ]
        captured["frames"] = frames
        return turn_id, frames

    monkeypatch.setattr(streaming, "_make_foundation_frames", fake_foundation)

    outage_calls = []
    monkeypatch.setattr(streaming, "_emit_ws_outage", lambda *a, **k: outage_calls.append((a, k)))

    tts_texts = []

    def fake_schedule_tts(session_id, text, turn_id=None, correlation_user_msg_id=None):
        tts_texts.append(text)

    monkeypatch.setattr(streaming, "schedule_tts_audio", fake_schedule_tts)
    monkeypatch.setattr(streaming.bus, "broadcast", lambda *a, **k: None)

    tid = streaming.run_ws_greet("session-foundation")

    assert captured["called"] == 1
    assert captured["seed_text"] == "greet"
    assert tid == captured["frames"][0]["turn_id"]
    assert not outage_calls
    assert tts_texts and tts_texts[0] == "foundation hello"
    assert streaming._WS_PIPELINE_MESSAGE not in captured["frames"][0]["text"]


def test_make_assistant_frames_http_allows_legacy(monkeypatch):
    _stub_config(monkeypatch)

    captured = types.SimpleNamespace(called=False)

    def fake_legacy(seed_text, session_id, meta, cfg, **kwargs):
        captured.called = True
        assert isinstance(meta.get("nlu"), dict)
        assert isinstance(meta.get("dialog_nlu"), dict)
        assert "action" in meta
        assert meta["dialog_action"] == meta["action"]
        assert "verbosity" in meta
        assert meta["dialog_verbosity"] == meta["verbosity"]
        assert "show_suggestions" in meta
        assert meta["dialog_show_suggestions"] == meta["show_suggestions"]
        assert meta["dialog_policy"]["teacher_move"] == kwargs["policy"]["teacher_move"]
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
        assert isinstance(meta.get("dialog_nlu"), dict)
        assert "action" in meta
        assert meta["dialog_action"] == meta["action"]
        assert "verbosity" in meta
        assert meta["dialog_verbosity"] == meta["verbosity"]
        assert "show_suggestions" in meta
        assert meta["dialog_show_suggestions"] == meta["show_suggestions"]
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


def test_prepare_turn_metadata_injects_fields(monkeypatch):
    _stub_config(monkeypatch)

    seed_meta = {"source": "http_chat"}
    prepared, labels, policy = streaming.prepare_turn_metadata("How do I deploy?", seed_meta)

    assert prepared is seed_meta
    assert isinstance(prepared.get("nlu"), dict)
    assert isinstance(prepared.get("dialog_nlu"), dict)
    assert prepared["action"]
    assert prepared["dialog_action"] == prepared["action"]
    assert prepared["verbosity"] in {"brief", "normal", "medium"}
    assert prepared["dialog_verbosity"] == prepared["verbosity"]
    assert prepared["show_suggestions"] is policy["show_suggestions"]
    assert prepared["dialog_show_suggestions"] is policy["show_suggestions"]
    assert prepared["dialog_policy"]["show_suggestions"] is policy["show_suggestions"]
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
    assert payload["nlu"]["intent"] == "broad_topic_help"
    assert payload["policy"]["action"] == "ask_clarify"
    assert payload["policy"]["show_suggestions"] is True


def test_prepare_turn_metadata_respects_policy_show_suggestions(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(
        streaming,
        "pick_dialog_policy",
        lambda labels: {"action": "give_brief_answer", "show_suggestions": False, "teacher_move": "give_brief_answer"},
    )

    prepared, _, policy = streaming.prepare_turn_metadata("Need a short answer", {})

    assert prepared["action"] == "give_brief_answer"
    assert prepared["dialog_action"] == "give_brief_answer"
    assert prepared["show_suggestions"] is False
    assert prepared["dialog_show_suggestions"] is False
    assert policy["show_suggestions"] is False


def test_prepare_turn_metadata_enables_suggestions_for_ask_clarify(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(
        streaming,
        "pick_dialog_policy",
        lambda labels: {"action": "ask_clarify", "show_suggestions": True, "teacher_move": "ask_clarify"},
    )

    prepared, _, policy = streaming.prepare_turn_metadata("Can you clarify?", {})

    assert prepared["action"] == "ask_clarify"
    assert prepared["dialog_action"] == "ask_clarify"
    assert prepared["show_suggestions"] is True
    assert prepared["dialog_show_suggestions"] is True
    assert policy["show_suggestions"] is True


def test_prepare_turn_metadata_enables_suggestions_for_offer_steps(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(
        streaming,
        "pick_dialog_policy",
        lambda labels: {"action": "offer_steps", "show_suggestions": True, "teacher_move": "offer_steps"},
    )

    prepared, _, policy = streaming.prepare_turn_metadata("Walk me through", {})

    assert prepared["action"] == "offer_steps"
    assert prepared["dialog_action"] == "offer_steps"
    assert prepared["show_suggestions"] is True
    assert prepared["dialog_show_suggestions"] is True
    assert policy["show_suggestions"] is True


def test_legacy_frames_emit_suggestions_only_when_allowed(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "kb_search", lambda *a, **k: [])
    monkeypatch.setattr(streaming, "_emit_turn_action_metadata", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "_broadcast_frames", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "_collect_policy_chips", lambda policy: [])
    monkeypatch.setattr(streaming, "hygienic_suggestions", lambda text: ["Next steps"])
    monkeypatch.setattr(streaming.db, "memory", {})

    class DummyProvider:
        def generate_reply(self, *args, **kwargs):
            return "Here you go"

    monkeypatch.setattr(streaming, "get_provider", lambda cfg: DummyProvider())

    cfg = {"suggestions_enabled": True}

    deny_meta = {"nlu": {}, "action": "give_brief_answer"}
    deny_policy = {"teacher_move": "give_brief_answer", "show_suggestions": False}

    _, deny_frames = streaming._make_legacy_frames(
        "Hello",
        "sess-deny",
        deny_meta,
        cfg,
        correlation_user_msg_id=None,
        force_turn_id=None,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        labels={},
        policy=deny_policy,
    )

    assert all(fr.get("type") != "suggestions" for fr in deny_frames)

    allow_meta = {"nlu": {}, "action": "ask_clarify"}
    allow_policy = {"teacher_move": "ask_clarify", "show_suggestions": True}

    _, allow_frames = streaming._make_legacy_frames(
        "Hello",
        "sess-allow",
        allow_meta,
        cfg,
        correlation_user_msg_id=None,
        force_turn_id=None,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        labels={},
        policy=allow_policy,
    )

    assert any(fr.get("type") == "suggestions" for fr in allow_frames)

    offer_meta = {"nlu": {}, "action": "offer_steps"}
    offer_policy = {"teacher_move": "offer_steps", "show_suggestions": True}

    _, offer_frames = streaming._make_legacy_frames(
        "Hello",
        "sess-offer",
        offer_meta,
        cfg,
        correlation_user_msg_id=None,
        force_turn_id=None,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        labels={},
        policy=offer_policy,
    )

    assert any(fr.get("type") == "suggestions" for fr in offer_frames)
