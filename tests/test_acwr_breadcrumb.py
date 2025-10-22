"""Tests for ACWR recompute breadcrumbs emitted by the engine."""
from __future__ import annotations

from typing import Any, Dict, List
import unittest

from app.telemetry.bus import reset, subscribe
from app.voice_v2 import EVT_ACWR_RECOMPUTE
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


class ACWRBreadcrumbTests(unittest.TestCase):
    """Validate Auto-Commit-When-Ready recompute breadcrumbs."""

    def setUp(self) -> None:
        reset()
        self._events: List[Dict[str, Any]] = []
        subscribe("*", lambda event: self._events.append(event))

    def tearDown(self) -> None:
        reset()

    def _acwr_events(self) -> List[Dict[str, Any]]:
        return [event for event in self._events if event.get("type") == EVT_ACWR_RECOMPUTE]

    def test_acwr_emitted_on_open(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)

        engine.on_open("sid-1", headers={})

        acwr_events = self._acwr_events()
        self.assertEqual(len(acwr_events), 1)

        event = acwr_events[0]
        meta = event.get("meta") or {}
        self.assertIs(meta.get("policy_acwr"), True)
        self.assertIs(meta.get("effective"), True)
        self.assertIsNone(meta.get("admin_enabled"))

    def test_acwr_emitted_on_reapply_change(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)
        engine.on_open("sid-1", headers={})

        baseline_count = len(self._acwr_events())

        engine.reapply_policy({"auto_commit_when_ready": False})

        acwr_events = self._acwr_events()
        self.assertEqual(len(acwr_events), baseline_count + 1)

        event = acwr_events[-1]
        meta = event.get("meta") or {}
        self.assertIs(meta.get("policy_acwr"), False)
        self.assertIs(meta.get("effective"), False)

    def test_acwr_no_emit_on_reapply_same_snapshot(self) -> None:
        exporter = _FakeExporter()
        engine = EngineV2(exporter)
        engine.on_open("sid-1", headers={})

        baseline_count = len(self._acwr_events())

        engine.reapply_policy({})

        acwr_events = self._acwr_events()
        self.assertEqual(len(acwr_events), baseline_count)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
