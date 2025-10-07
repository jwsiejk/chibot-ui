from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from app.config import Settings
from app.obs import nlu_logging


@pytest.fixture
def logged_events(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[str, Dict[str, Any]]]:
    events: List[Tuple[str, Dict[str, Any]]] = []

    def _capture(kind: str, **fields: Any) -> None:
        events.append((kind, fields))

    monkeypatch.setattr(nlu_logging, "_jlog", _capture)
    return events


def _make_context(enable_env: Any, cfg: Dict[str, Any]) -> nlu_logging.NluLoggingContext:
    settings = Settings(enable_nlu_logging=enable_env)
    return nlu_logging.NluLoggingContext(settings=settings, cfg=cfg, turn_id="turn-1")


def test_env_unset_config_true_emits(logged_events: List[Tuple[str, Dict[str, Any]]]) -> None:
    ctx = _make_context(None, {"nlu_logging_enabled": True})
    ctx.log_guardrail(decision="allow")

    assert ctx.enabled is True
    assert logged_events == [("nlu.guardrail", {"turn_id": "turn-1", "decision": "allow"})]


def test_env_true_config_missing_defaults_on(logged_events: List[Tuple[str, Dict[str, Any]]]) -> None:
    ctx = _make_context(True, {})
    ctx.log_guardrail(decision="deny", reason="policy")

    assert ctx.enabled is True
    assert logged_events == [
        ("nlu.guardrail", {"turn_id": "turn-1", "decision": "deny", "reason": "policy"})
    ]


def test_env_false_forces_disable(logged_events: List[Tuple[str, Dict[str, Any]]]) -> None:
    ctx = _make_context(False, {"nlu_logging_enabled": True})
    ctx.log_guardrail(decision="allow")

    assert ctx.enabled is False
    assert logged_events == []


def test_config_false_overrides_env(logged_events: List[Tuple[str, Dict[str, Any]]]) -> None:
    ctx = _make_context(True, {"nlu_logging_enabled": False})
    ctx.log_guardrail(decision="deny")

    assert ctx.enabled is False
    assert logged_events == []


def test_config_absent_with_env_unset_defaults_on(
    logged_events: List[Tuple[str, Dict[str, Any]]]
) -> None:
    ctx = _make_context(None, {})
    ctx.log_guardrail(decision="allow")

    assert ctx.enabled is True
    assert logged_events == [("nlu.guardrail", {"turn_id": "turn-1", "decision": "allow"})]
