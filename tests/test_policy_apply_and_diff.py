"""Tests for policy apply/reapply flow in the engine."""
from __future__ import annotations

from typing import Any, Dict, List
import unittest

from app.policy.loader import load_interaction_policy
from app.telemetry.bus import reset, subscribe
from app.voice_v2 import EVT_POLICY_APPLIED, EVT_WS_JSON_SEND
from app.voice_v2.engine import EngineV2


class _FakeExporter:
    """Minimal exporter stub satisfying the EngineV2 contract."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def begin(self, sid: str) -> None:  # pragma: no cover - not used in these tests
        self.events.append({"action": "begin", "sid": sid})

    def write(self, sid: str, event: Dict[str, Any]) -> None:
        self.events.append({"action": "write", "sid": sid, "event": dict(event)})

    def end(self, sid: str, summary: Dict[str, Any] | None = None) -> None:  # pragma: no cover
        self.events.append({"action": "end", "sid": sid, "summary": dict(summary or {})})


class PolicyApplyAndDiffTests(unittest.TestCase):
    """Validate policy frames and diff summaries emitted by the engine."""

    def setUp(self) -> None:
        reset()
        self._events: List[Dict[str, Any]] = []
        subscribe("*", lambda event: self._events.append(event))

    def tearDown(self) -> None:
        reset()

    def _policy_frames(self) -> List[Dict[str, Any]]:
        frames: List[Dict[str, Any]] = []
        for event in self._events:
            if event.get("type") != EVT_WS_JSON_SEND:
                continue
            frame = event.get("frame")
            if isinstance(frame, dict) and frame.get("type") == "policy.interaction":
                frames.append(event)
        return frames

    def _events_of_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [event for event in self._events if event.get("type") == event_type]

    def test_on_open_emits_policy_frame_and_diff(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)

        engine.on_open("sid-1", headers={})

        frames = self._policy_frames()
        self.assertEqual(len(frames), 1)

        applied_events = self._events_of_type(EVT_POLICY_APPLIED)
        self.assertEqual(len(applied_events), 1)

        diff_meta = applied_events[0].get("meta", {}).get("policy", {}).get("diff")
        self.assertIsInstance(diff_meta, dict)

        expected_keys = {
            "mode",
            "allow_auto_vad",
            "barge_in_enabled",
            "auto_commit_when_ready",
            "telemetry",
        }
        self.assertEqual(set(diff_meta.keys()), expected_keys)

        defaults = load_interaction_policy()
        for key in expected_keys:
            before_after = diff_meta[key]
            self.assertEqual(before_after[0], None)
            self.assertEqual(before_after[1], defaults[key])

    def test_reapply_without_changes_is_noop(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)
        engine.on_open("sid-1", headers={})

        baseline_frames = len(self._policy_frames())
        baseline_applied = len(self._events_of_type(EVT_POLICY_APPLIED))

        result = engine.reapply_policy({})
        self.assertIs(result, False)

        self.assertEqual(len(self._policy_frames()), baseline_frames)
        self.assertEqual(len(self._events_of_type(EVT_POLICY_APPLIED)), baseline_applied)

    def test_reapply_with_changes_emits_diff(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)
        engine.on_open("sid-1", headers={})

        baseline_frames = len(self._policy_frames())
        baseline_applied = len(self._events_of_type(EVT_POLICY_APPLIED))

        result = engine.reapply_policy({"barge_in_enabled": False})
        self.assertIs(result, True)

        self.assertEqual(len(self._policy_frames()), baseline_frames + 1)
        self.assertEqual(len(self._events_of_type(EVT_POLICY_APPLIED)), baseline_applied + 1)

        diff_meta = self._events_of_type(EVT_POLICY_APPLIED)[-1]["meta"]["policy"]["diff"]
        self.assertEqual(set(diff_meta.keys()), {"barge_in_enabled"})
        self.assertEqual(diff_meta["barge_in_enabled"], [True, False])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
