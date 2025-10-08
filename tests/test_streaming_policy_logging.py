import pytest

from app.services import streaming


class DummyTelemetry:
    def __init__(self):
        self.turn_id = None

    def log_start(self, *args, **kwargs):
        return None

    def log_entities(self, *args, **kwargs):
        return None

    def log_prompt_summary(self, *args, **kwargs):
        return None

    def mark_fallback(self, *args, **kwargs):
        return None

    def log_llm_response(self, *args, **kwargs):
        return None

    def log_error(self, *args, **kwargs):
        return None


class DummyStore:
    def match_examples(self, persona_id, user_text, limit=4):
        return []


class DummyPersonaManager:
    def __init__(self, store):
        self.store = store

    def get_active(self):
        return {"id": "chip", "name": "Chip", "config": {}}


def _patch_foundation_dependencies(monkeypatch, *, teacher_move="deep_dive"):
    monkeypatch.setattr(streaming, "PersonaStore", lambda: DummyStore())
    monkeypatch.setattr(streaming, "PersonaManager", DummyPersonaManager)
    monkeypatch.setattr(
        streaming._nlu,
        "infer",
        lambda *a, **k: {"intent": "install", "confidence": 0.91, "tags": {}, "entities": []},
    )
    monkeypatch.setattr(
        streaming._nlu.policy,
        "decide",
        lambda *a, **k: {"teacher_move": teacher_move},
    )
    persona_trace = {
        "intensity": 0.25,
        "quote": {
            "text": "Giddy up",
            "quote_id": "q-demo",
            "enabled": True,
            "picked": True,
            "candidate_count": 1,
            "bank": "nebraska",
        },
        "toggles": {"humor": 0.2},
        "guardrail": {"muted": []},
    }
    monkeypatch.setattr(
        streaming,
        "build_messages",
        lambda **kwargs: (
            [{"role": "user", "content": kwargs["user_text"]}],
            teacher_move,
            "hash1234",
            persona_trace,
        ),
    )

    def _fake_foundation(messages, cfg, telemetry=None):
        return "Foundation response", {"preview": "Foundation", "finish_reason": "stop", "output_tokens": 42}

    monkeypatch.setattr(streaming, "_call_foundation_with_retry", _fake_foundation)
    monkeypatch.setattr(streaming, "hygienic_suggestions", lambda text: [])
    monkeypatch.setattr(streaming, "_collect_policy_chips", lambda policy: [])


def test_foundation_policy_decision_logs_kb_snippets(monkeypatch):
    _patch_foundation_dependencies(monkeypatch, teacher_move="deep_dive")

    telemetry = DummyTelemetry()
    meta = {
        "action": "deep_dive",
        "kb_snippets": [
            {"id": "doc-1", "text": "Alpha"},
            "Second document body",
        ],
    }
    cfg = {"suggestions_enabled": False, "nebraska_quotes_enabled": True}

    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    monkeypatch.setattr(streaming, "_jlog", _capture)

    turn_id, frames = streaming._make_foundation_frames(
        "hello",
        "session-1",
        meta,
        cfg,
        correlation_user_msg_id=None,
        turn_id="10",
        telemetry=telemetry,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        action="deep_dive",
        broadcast_immediately=False,
    )

    assert turn_id == "10"
    assert frames and frames[0]["type"] == "assistant_chunk"
    assert captured
    policy_events = [fields for event, fields in captured if event == "policy.decision"]
    assert policy_events
    fields = policy_events[0]
    assert fields["teacher_move"] == "deep_dive"
    assert fields["teacher_move_family"] == "deep_dive"
    assert fields["reason"] == "policy"
    assert fields["nlu_intent"] == "install"
    assert pytest.approx(fields["nlu_confidence"], rel=1e-6) == 0.91
    assert "used_docs" in fields and len(fields["used_docs"]) == 2
    hashes = {entry["hash"] for entry in fields["used_docs"]}
    assert all(len(h) == 8 for h in hashes)

    persona_events = [fields for event, fields in captured if event == "persona_applied"]
    assert persona_events
    persona_fields = persona_events[0]
    assert persona_fields["persona_level"] == pytest.approx(0.25)
    assert persona_fields["persona_elements"] == ["humor:0.20", "quote:q-demo"]
    assert persona_fields["guardrails_suppressed"] == []

    suggestions_events = [fields for event, fields in captured if event == "suggestions_made"]
    assert suggestions_events
    suggestion_fields = suggestions_events[0]
    assert suggestion_fields["turn_id"] == "10"
    assert suggestion_fields["items"] == []


def _patch_legacy_dependencies(monkeypatch, reply_text: str):
    monkeypatch.setattr(streaming, "_get_persona_for_session", lambda session_id: {"id": "chip", "name": "Chip"})

    class Provider:
        def generate_reply(self, prompt, persona=None, teacher_move=None, context=None):
            return reply_text

    monkeypatch.setattr(streaming, "get_provider", lambda cfg: Provider())
    monkeypatch.setattr(streaming, "hygienic_suggestions", lambda text: [])
    monkeypatch.setattr(streaming, "_collect_policy_chips", lambda policy: [])


def test_legacy_policy_decision_logs_fallback(monkeypatch):
    _patch_legacy_dependencies(monkeypatch, reply_text="")

    telemetry = DummyTelemetry()
    meta = {"action": "offer_steps"}
    cfg = {"suggestions_enabled": False, "nebraska_persona_level": 0.13, "nebraska_quotes_enabled": False}
    labels = {"intent": "install", "confidence": 0.5}
    policy = {"teacher_move": "offer_steps"}

    captured = []
    monkeypatch.setattr(streaming, "_jlog", lambda event, **fields: captured.append((event, fields)))

    turn_id, frames = streaming._make_legacy_frames(
        "hi",
        "session-legacy",
        meta,
        cfg,
        correlation_user_msg_id=None,
        turn_id="22",
        telemetry=telemetry,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        action="offer_steps",
        labels=labels,
        policy=policy,
        broadcast_immediately=False,
    )

    assert turn_id == "22"
    assert frames and frames[0]["type"] == "assistant_chunk"
    assert captured
    policy_events = [fields for event, fields in captured if event == "policy.decision"]
    assert policy_events
    fields = policy_events[0]
    assert fields["teacher_move"] == "offer_steps"
    assert fields["teacher_move_family"] == "answer"
    assert fields["reason"] == "fallback"
    assert fields["fallback_fired"] is True
    assert fields["fallback_reason"] == "empty"

    persona_events = [fields for event, fields in captured if event == "persona_applied"]
    assert persona_events
    persona_fields = persona_events[0]
    assert persona_fields["persona_level"] == pytest.approx(0.13)
    assert persona_fields["persona_elements"] == []
    assert persona_fields["guardrails_suppressed"] == ["quote"]

    suggestions_events = [fields for event, fields in captured if event == "suggestions_made"]
    assert suggestions_events
    legacy_fields = suggestions_events[0]
    assert legacy_fields["turn_id"] == "22"
    assert legacy_fields["items"] == []


def test_legacy_policy_decision_meta_override(monkeypatch):
    _patch_legacy_dependencies(monkeypatch, reply_text="All good")

    telemetry = DummyTelemetry()
    meta = {"action": "offer_steps"}
    cfg = {"suggestions_enabled": False, "nebraska_persona_level": 0.13, "nebraska_quotes_enabled": False}
    labels = {"intent": "install", "confidence": 0.42}
    policy = {"teacher_move": "ask_clarify", "action": "ask_clarify"}

    captured = []
    monkeypatch.setattr(streaming, "_jlog", lambda event, **fields: captured.append((event, fields)))

    turn_id, frames = streaming._make_legacy_frames(
        "Need help",
        "session-override",
        meta,
        cfg,
        correlation_user_msg_id=None,
        turn_id="33",
        telemetry=telemetry,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        action="offer_steps",
        labels=labels,
        policy=policy,
        broadcast_immediately=False,
    )

    assert turn_id == "33"
    assert frames and frames[0]["type"] == "assistant_chunk"
    assert captured
    policy_events = [fields for event, fields in captured if event == "policy.decision"]
    assert policy_events
    fields = policy_events[0]
    assert fields["teacher_move"] == "ask_clarify"
    assert fields["teacher_move_family"] == "clarify"
    assert fields["reason"] == "meta_override"
    assert fields["meta_action"] == "offer_steps"
    assert fields["policy_move"] == "ask_clarify"
    assert fields["nlu_intent"] == "install"
    assert pytest.approx(fields["nlu_confidence"], rel=1e-6) == 0.42
    persona_events = [fields for event, fields in captured if event == "persona_applied"]
    assert persona_events
    persona_fields = persona_events[0]
    assert persona_fields["persona_level"] == pytest.approx(0.13)
    assert persona_fields["persona_elements"] == []
    assert persona_fields["guardrails_suppressed"] == ["quote"]

    suggestions_events = [fields for event, fields in captured if event == "suggestions_made"]
    assert suggestions_events
    override_fields = suggestions_events[0]
    assert override_fields["turn_id"] == "33"


def test_suggestions_made_classifies_sources(monkeypatch):
    _patch_foundation_dependencies(monkeypatch, teacher_move="respond")

    telemetry = DummyTelemetry()
    meta = {"action": "respond"}
    cfg = {
        "suggestions_enabled": True,
        "suggestions_max_items": 3,
        "suggestions_max_words": 5,
        "nebraska_quotes_enabled": True,
    }

    policy_items = ["Use GUI", "Check cables"]
    retrieval_items = ["Check cables", "Consult manual"]

    monkeypatch.setattr(streaming, "_collect_policy_chips", lambda policy: policy_items)
    monkeypatch.setattr(streaming, "hygienic_suggestions", lambda text: retrieval_items)

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(streaming, "_jlog", lambda event, **fields: captured.append((event, fields)))

    turn_id, _ = streaming._make_foundation_frames(
        "Need help",
        "session-mixed",
        meta,
        cfg,
        correlation_user_msg_id=None,
        turn_id="44",
        telemetry=telemetry,
        is_greet=False,
        fallback_line="Fallback",
        fallback_on_empty=True,
        fallback_on_error=True,
        fallback_emit_event=False,
        action="respond",
        broadcast_immediately=False,
    )

    assert turn_id == "44"

    suggestions_events = [fields for event, fields in captured if event == "suggestions_made"]
    assert suggestions_events
    fields = suggestions_events[0]
    assert fields["turn_id"] == "44"
    assert fields["max_items"] == 3
    assert fields["max_words_per_item"] == 5
    assert fields["items"] == [
        {"text": "Use GUI", "source": "policy"},
        {"text": "Check cables", "source": "policy"},
        {"text": "Consult manual", "source": "retrieval"},
    ]
