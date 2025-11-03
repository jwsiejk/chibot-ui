"""Tests ensuring policy.interaction frames expose only the stable subset."""
from __future__ import annotations

import json
import os

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.ws.adapter import AdapterContext, ChatV2Adapter


def _make_context() -> AdapterContext:
    return AdapterContext(sid="sid-1", headers={})


def test_policy_subset_is_sanitized() -> None:
    adapter = ChatV2Adapter()
    ctx = _make_context()
    payload = {
        "type": "policy.interaction",
        "ts_ms": 1730000000123,
        "level": "info",
        "policy": {
            "mode": "idle",
            "allow_auto_vad": True,
            "barge_in_enabled": False,
            "telemetry": {"enabled": True},
            "auto_commit_when_ready": True,
            "capture": {"start_on_turn_ready": True},
            "policy": {"asr": {"prearm_on_tts_end": True}},
        },
    }

    event = {"payload": payload}

    sanitized = adapter._extract_outbound_payload(ctx, event)
    assert sanitized is not None
    assert sanitized["ts_ms"] == payload["ts_ms"]
    assert sanitized["level"] == "info"
    assert sanitized["policy"] == {
        "mode": "idle",
        "allow_auto_vad": True,
        "barge_in_enabled": False,
        "telemetry": {"enabled": True},
        "auto_commit_when_ready": True,
        "capture": {"start_on_turn_ready": True},
        "policy": {"asr": {"prearm_on_tts_end": True}},
    }
    assert json.loads(json.dumps(sanitized)) == sanitized
