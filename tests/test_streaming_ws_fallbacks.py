import asyncio
import importlib
import json
import types
from collections import deque

from app.services import streaming
from app.ws import ws_asgi


def _test_client():
    mod = importlib.import_module("app")
    if hasattr(mod, "create_app"):
        flask_app = mod.create_app()
    else:
        flask_app = mod.app
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _csrf_token(client):
    resp = client.get("/api/v1/csrf")
    return resp.headers.get("X-CSRF-Token")


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

    def fake_schedule_tts(session_id, text, turn_id=None, correlation_user_msg_id=None, **kwargs):
        tts_texts.append(text)
        on_complete = kwargs.get("on_complete")
        if callable(on_complete):
            on_complete()
        return True

    monkeypatch.setattr(streaming, "schedule_tts_audio", fake_schedule_tts)
    monkeypatch.setattr(streaming.bus, "broadcast", lambda *a, **k: None)

    tid = streaming.run_ws_greet("session-foundation")

    assert captured["called"] == 1
    assert captured["seed_text"] == "greet"
    assert tid == captured["frames"][0]["turn_id"]
    assert not outage_calls
    assert tts_texts and tts_texts[0] == "foundation hello"
    assert streaming._WS_PIPELINE_MESSAGE not in captured["frames"][0]["text"]


def test_run_ws_greet_does_not_emit_clarify_fallback(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "ENABLE_CHIP_FOUNDATION", True)

    captured = {}

    def fake_foundation(seed_text, session_id, meta, cfg, **kwargs):
        action = kwargs.get("action")
        fallback_text = streaming._build_clarify_question(seed_text, meta)
        turn_id = kwargs.get("force_turn_id") or "tid-greet"
        if action == "ask_clarify":
            text = fallback_text
        else:
            text = "foundation hello"
        frames = [
            {"type": "assistant_chunk", "turn_id": turn_id, "text": text, "kb_hits": 0},
            {"type": "assistant_end", "turn_id": turn_id},
        ]
        captured["frames"] = frames
        captured["action"] = action
        captured["fallback_text"] = fallback_text
        return turn_id, frames

    monkeypatch.setattr(streaming, "_make_foundation_frames", fake_foundation)
    monkeypatch.setattr(streaming.bus, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: False)

    tid = streaming.run_ws_greet("session-no-clarify")

    assert tid == captured["frames"][0]["turn_id"]
    assert captured["action"] == "offer_steps"
    assert captured["frames"][0]["text"] != captured["fallback_text"]


def test_run_ws_greet_emits_single_suggestions_frame(monkeypatch):
    _stub_config(monkeypatch)

    # Avoid touching shared greet idempotency state
    monkeypatch.setattr(
        streaming,
        "get_or_create_greet_turn",
        lambda session_id, force=False, ttl_sec=streaming.DEFAULT_TTL_SEC: ("turn-xyz", False),
    )

    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: False)

    emitted = []

    def fake_broadcast(session_id, frame):
        if isinstance(frame, dict):
            emitted.append(frame)

    monkeypatch.setattr(streaming.bus, "broadcast", fake_broadcast)

    def fake_make_frames(seed_text, session_id, meta=None, **kwargs):
        turn_id = kwargs.get("force_turn_id") or "turn-xyz"
        frames = [
            {"type": "assistant_chunk", "turn_id": turn_id, "text": "Hello!", "kb_hits": 0},
            {"type": "state", "phase": "ready"},
            {
                "type": "suggestions",
                "turn_id": turn_id,
                "items": streaming.build_suggestion_items(["First", "Second"]),
            },
            {"type": "assistant_end", "turn_id": turn_id},
        ]
        if kwargs.get("broadcast_immediately", True):
            for fr in frames:
                fake_broadcast(session_id, fr)
        return turn_id, frames

    monkeypatch.setattr(streaming, "make_assistant_frames", fake_make_frames)

    tid = streaming.run_ws_greet("session-one")

    assert tid == "turn-xyz"
    suggestion_frames = [fr for fr in emitted if fr.get("type") == "suggestions"]
    assert len(suggestion_frames) == 1


def test_make_assistant_frames_greet_advertises_welcome_move(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "ENABLE_CHIP_FOUNDATION", True)
    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *a, **k: True)
    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: False)
    monkeypatch.setattr(streaming.bus, "broadcast", lambda *a, **k: None)

    class FakeStore:
        def match_examples(self, persona_id, user_text, limit=4):
            return []

    class FakePersonaManager:
        def __init__(self, store):
            self.store = store

        def get_active(self):
            return {"id": "chip", "config": {}}

    monkeypatch.setattr(streaming, "PersonaStore", lambda: FakeStore())
    monkeypatch.setattr(streaming, "PersonaManager", FakePersonaManager)

    monkeypatch.setattr(
        streaming,
        "build_messages",
        lambda persona, user_text, dialog_meta, examples: (
            [{"role": "user", "content": user_text}],
            dialog_meta.get("teacher_move"),
            "hash",
        ),
    )
    monkeypatch.setattr(streaming, "_call_foundation_with_retry", lambda *a, **k: "Hello!")

    monkeypatch.setattr(streaming._nlu, "infer", lambda *a, **k: {"intent": "greet", "confidence": 0.9})
    monkeypatch.setattr(
        streaming._nlu,
        "policy",
        types.SimpleNamespace(decide=lambda *a, **k: {"action": "offer_steps", "teacher_move": "offer_steps"}),
    )

    _, frames = streaming.make_assistant_frames("greet", "session-greet", meta={"source": "ws_greet"})

    chunk = next(fr for fr in frames if fr.get("type") == "assistant_chunk")
    assert chunk["teacher_move"] == "offer_steps"
    assert chunk.get("intent") == "greet"


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
    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: False)

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


def test_run_ws_user_turn_emits_single_chunk_frame(monkeypatch):
    _stub_config(monkeypatch)

    emitted = []

    def fake_broadcast(session_id, frame):
        emitted.append(frame)

    monkeypatch.setattr(streaming.bus, "broadcast", fake_broadcast)

    def fake_make_frames(text, session_id, meta=None, correlation_user_msg_id=None, **kwargs):
        frames = [
            {"type": "assistant_chunk", "turn_id": "turn-1", "text": "Hello there"},
            {"type": "assistant_end", "turn_id": "turn-1"},
        ]
        for fr in frames:
            fake_broadcast(session_id, fr)
        return "turn-1", frames

    monkeypatch.setattr(streaming, "make_assistant_frames", fake_make_frames)

    def fail_schedule_frames(*args, **kwargs):  # pragma: no cover - guard against regressions
        raise AssertionError("schedule_frames should not run")

    monkeypatch.setattr(streaming, "schedule_frames", fail_schedule_frames)
    monkeypatch.setattr(streaming, "schedule_tts_audio", lambda *a, **k: False)
    monkeypatch.setattr(streaming, "classify_turn", lambda text, meta=None: {"intent": "test"})
    monkeypatch.setattr(
        streaming,
        "pick_dialog_policy",
        lambda nlu: {"action": "ask_clarify", "verbosity": "medium", "show_suggestions": True},
    )

    tid = streaming.run_ws_user_turn("session-single-chunk", "hello")

    assert tid == "turn-1"
    chunk_frames = [fr for fr in emitted if isinstance(fr, dict) and fr.get("type") == "assistant_chunk"]
    assert len(chunk_frames) == 1


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


def test_make_assistant_frames_allows_opt_out(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setattr(streaming, "_should_use_foundation", lambda *_: False)

    captured = {}

    def fake_legacy(seed_text, session_id, meta, cfg, **kwargs):
        captured["broadcast_immediately"] = kwargs.get("broadcast_immediately")
        frames = [
            {"type": "assistant_chunk", "turn_id": "abc", "text": "hi", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "abc"},
        ]
        return "abc", frames

    monkeypatch.setattr(streaming, "_make_legacy_frames", fake_legacy)

    tid, frames = streaming.make_assistant_frames(
        "hello",
        "sess-opt",
        meta={"source": "user_http"},
        broadcast_immediately=False,
    )

    assert tid == "abc"
    assert captured.get("broadcast_immediately") is False
    assert sum(1 for fr in frames if fr.get("type") == "assistant_chunk") == 1


def test_post_chat_emits_single_chunk(monkeypatch):
    _stub_config(monkeypatch)

    import app.api_v1.chat as chat_api

    monkeypatch.setattr(chat_api, "check_now", lambda *a, **k: None)
    monkeypatch.setattr(chat_api, "classify_turn", lambda *a, **k: {"intent": "ask", "verbosity": "normal", "show_suggestions": False})
    monkeypatch.setattr(chat_api, "pick_dialog_policy", lambda *a, **k: {"action": "answer", "verbosity": "normal", "show_suggestions": False})

    calls = {}

    def fake_make_frames(text, sid, **kwargs):
        calls["broadcast_immediately"] = kwargs.get("broadcast_immediately")
        return "tid-chat", [
            {"type": "assistant_chunk", "turn_id": "tid-chat", "text": "ok", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "tid-chat"},
        ]

    emitted = []

    def fake_schedule_frames(session_id, frames, **kwargs):
        emitted.extend(frames)

    monkeypatch.setattr(chat_api, "make_assistant_frames", fake_make_frames)
    monkeypatch.setattr(chat_api, "schedule_frames", fake_schedule_frames)

    client = _test_client()
    token = _csrf_token(client)

    resp = client.post(
        "/api/v1/chat",
        json={"text": "Hello", "session_id": "sess-post"},
        headers={"Idempotency-Key": "abc", "X-CSRF-Token": token},
    )

    assert resp.status_code == 200
    assert calls.get("broadcast_immediately") is False
    assert sum(1 for fr in emitted if fr.get("type") == "assistant_chunk") == 1


def test_chat_entry_emits_single_chunk(monkeypatch):
    _stub_config(monkeypatch)

    import app.api_v1.chat as chat_api

    monkeypatch.setattr(chat_api, "check_now", lambda *a, **k: None)
    monkeypatch.setattr(chat_api, "classify_turn", lambda *a, **k: {"intent": "ask", "verbosity": "normal", "show_suggestions": False})
    monkeypatch.setattr(chat_api, "pick_dialog_policy", lambda *a, **k: {"action": "answer", "verbosity": "normal", "show_suggestions": False})

    calls = {}

    def fake_make_frames(text, sid, **kwargs):
        calls.setdefault("broadcast_immediately", []).append(kwargs.get("broadcast_immediately"))
        return "tid-chat-entry", [
            {"type": "assistant_chunk", "turn_id": "tid-chat-entry", "text": "ok", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "tid-chat-entry"},
        ]

    emitted = []

    def fake_schedule_frames(session_id, frames, **kwargs):
        emitted.extend(frames)

    monkeypatch.setattr(chat_api, "make_assistant_frames", fake_make_frames)
    monkeypatch.setattr(chat_api, "schedule_frames", fake_schedule_frames)

    client = _test_client()
    token = _csrf_token(client)

    resp = client.post(
        "/api/v1/chat/",
        json={"text": "Hello", "session_id": "sess-entry"},
        headers={"Idempotency-Key": "entry", "X-CSRF-Token": token},
    )

    assert resp.status_code == 200
    assert calls.get("broadcast_immediately") == [False]
    assert sum(1 for fr in emitted if fr.get("type") == "assistant_chunk") == 1


def test_greet_emits_single_chunk(monkeypatch):
    _stub_config(monkeypatch)

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    emitted = []
    flags = []

    def fake_prepare(seed_text, meta, cfg=None):
        return {"source": meta.get("source")}, {}, {}

    def fake_make_frames(seed_text, sid, **kwargs):
        flags.append(kwargs.get("broadcast_immediately"))
        return "tid-greet", [
            {"type": "assistant_chunk", "turn_id": "tid-greet", "text": "hello", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "tid-greet"},
        ]

    def fake_schedule(session_id, frames, **kwargs):
        emitted.extend(frames)

    monkeypatch.setattr(streaming, "prepare_turn_metadata", fake_prepare)
    monkeypatch.setattr(streaming, "make_assistant_frames", fake_make_frames)
    monkeypatch.setattr(streaming, "schedule_frames", fake_schedule)

    client = _test_client()

    resp = client.get("/api/v1/greet?session_id=sess-greet")

    assert resp.status_code == 200
    assert flags == [False]
    assert sum(1 for fr in emitted if fr.get("type") == "assistant_chunk") == 1


def test_nudge_emits_single_chunk(monkeypatch):
    _stub_config(monkeypatch)

    import app.policy.nudges as nudges

    emitted = []
    flags = []

    def fake_make_frames(text, sid, **kwargs):
        flags.append(kwargs.get("broadcast_immediately"))
        return "tid-nudge", [
            {"type": "assistant_chunk", "turn_id": "tid-nudge", "text": "ping", "kb_hits": 0},
            {"type": "assistant_end", "turn_id": "tid-nudge"},
        ]

    class InstantTimer:
        def __init__(self, delay, func):
            self.func = func

        def start(self):
            self.func()

        def cancel(self):
            pass

    monkeypatch.setattr(nudges, "make_assistant_frames", fake_make_frames)
    monkeypatch.setattr(nudges.bus, "broadcast", lambda sid, frame: emitted.append(frame))
    monkeypatch.setattr(nudges.threading, "Timer", InstantTimer)

    nudges.arm_nudge("sess-nudge")

    assert flags == [False]
    assert sum(1 for fr in emitted if fr.get("type") == "assistant_chunk") == 1


def test_ws_no_audio_watchdog_emits_diagnostics(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("WS_NO_AUDIO_DETECT_WINDOW_S", "0.01")
    monkeypatch.setenv("WS_NO_AUDIO_NUDGE", "1")

    admin_events = []

    def fake_admin(event, **payload):
        admin_events.append((event, payload))

    monkeypatch.setattr(ws_asgi, "_admin_emit", fake_admin)

    broadcast_frames = []

    def fake_broadcast(session_id, frame):
        broadcast_frames.append((session_id, frame))

    monkeypatch.setattr(ws_asgi.bus, "broadcast", fake_broadcast)

    class _FailingDeepgram:
        def __init__(self, cfg):
            self.cfg = cfg

        async def connect(self):
            raise RuntimeError("connect_fail")

        async def send(self, data):
            raise RuntimeError("send_fail")

        async def close(self, wait_for_final=True):
            return

        def events(self):
            async def _gen():
                if False:  # pragma: no cover - generator stub
                    yield None
            return _gen()

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _FailingDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00" * 2},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    async def _receive():
        if not events:
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}
        nxt = events[0]
        if nxt.get("type") == "websocket.disconnect":
            await asyncio.sleep(0.05)
        return events.popleft()

    async def _send(msg):
        return None

    scope = {
        "type": "websocket",
        "path": "/ws/v1/chat",
        "query_string": b"session_id=silent",
        "headers": [],
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(asyncio.sleep(0.06))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert any(evt == "no_audio_detected" for evt, _ in admin_events)

    no_audio_frames = [fr for _, fr in broadcast_frames if fr.get("type") == "no_audio_detected"]
    assert no_audio_frames, "bus should broadcast a no_audio_detected frame"
    assert any(fr.get("reason") in ("timeout", "close_stream") for fr in no_audio_frames)


def test_ws_emits_nlu_admin_event_before_llm(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    admin_events = []
    order = []

    def fake_admin(event, **payload):
        order.append(("admin", event))
        admin_events.append((event, payload))

    monkeypatch.setattr(ws_asgi, "_admin_emit", fake_admin)

    final_text = "I need help with Widget"
    meta_calls = []

    def fake_prepare(text, meta=None, **kwargs):
        meta_calls.append((text, dict(meta or {})))
        meta_out = dict(meta or {})
        meta_out.setdefault("nlu", {})
        return (
            meta_out,
            {
                "intent": "ask",
                "confidence": 0.42,
                "entities": {"product": "Widget"},
                "products": ["Widget"],
            },
            {},
        )

    monkeypatch.setattr(ws_asgi, "prepare_turn_metadata", fake_prepare)

    llm_calls = []

    def fake_run_ws_user_turn(session_id, text, corr_id=None):
        llm_calls.append((session_id, text, corr_id))
        order.append(("llm", text))

    monkeypatch.setattr(ws_asgi, "run_ws_user_turn", fake_run_ws_user_turn)

    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ws_asgi.asyncio, "to_thread", immediate_to_thread)

    class _ImmediateFinalDeepgram:
        def __init__(self, cfg):
            self.cfg = cfg
            self._queue: asyncio.Queue = asyncio.Queue()

        async def connect(self):
            await self._queue.put({"type": "asr_open"})
            await self._queue.put({"type": "user_final", "text": final_text})
            await self._queue.put(None)

        async def events(self):
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev

        async def send(self, _chunk: bytes):
            return

        async def close(self, wait_for_final=True):
            return

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _ImmediateFinalDeepgram)
    monkeypatch.setattr(ws_asgi.bus, "broadcast", lambda *a, **k: None)

    captured_nlu = []

    def fake_emit_nlu(text, session_id):
        payload = {
            "event": "nlu",
            "intent": "ask",
            "confidence": 0.42,
            "slots": {
                "entities": {"product": "Widget"},
                "products": ["Widget"],
            },
            "text": text,
            "session_id": session_id,
        }
        captured_nlu.append(payload)
        order.append(("admin", "nlu"))

    monkeypatch.setattr(ws_asgi, "_emit_admin_nlu_event", fake_emit_nlu)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    async def _receive():
        if not events:
            return {"type": "websocket.disconnect"}
        return events.popleft()

    sent = []

    async def _send(msg):
        sent.append(msg)

    scope = {
        "type": "websocket",
        "path": "/ws/v1/chat",
        "query_string": b"session_id=test-nlu",
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert any(event == "asr:final" for event, _ in admin_events)

    assert captured_nlu == [
        {
            "event": "nlu",
            "intent": "ask",
            "confidence": 0.42,
            "slots": {
                "entities": {"product": "Widget"},
                "products": ["Widget"],
            },
            "text": final_text,
            "session_id": "test-nlu",
        }
    ]

    assert meta_calls == [] or meta_calls[0][1] == {
        "source": "user_ws",
        "channel": "ws",
    }

    assert llm_calls and llm_calls[0][0] == "test-nlu"

    nlu_index = next(i for i, entry in enumerate(order) if entry == ("admin", "nlu"))
    llm_index = next(i for i, entry in enumerate(order) if entry[0] == "llm")
    assert nlu_index < llm_index, "nlu admin event should precede llm turn"
