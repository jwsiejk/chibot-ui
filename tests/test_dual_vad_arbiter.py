from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional

from app.voice_v2 import (
    EVT_ACTION_SAY_END,
    EVT_BARGE_CONFIRMED,
    EVT_BARGE_DETECTED,
    EVT_MIC_GATE,
    EVT_TTS_END,
    EVT_TTS_MASK,
)
from app.voice_v2.engine import CONFIRMING_BARGE, LISTENING, RESPONDING, EngineV2
from app.voice_v2.vad import VADAggregator


class _FakeBus:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def publish(self, event: Dict[str, Any]) -> None:
        self.events.append(dict(event))


class _FakeExporter:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def write(self, sid: str, event: Dict[str, Any]) -> None:  # pragma: no cover - exercised indirectly
        payload = dict(event)
        payload.setdefault("sid", sid)
        self.events.append(payload)


def _base_policy(mode: str = "or", priority: str = "asr", overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = {
        "barge_in_enabled": True,
        "vad": {
            "mode": mode,
            "priority": priority,
            "min_speech_ms": 200,
            "energy_threshold_dbfs": -45.0,
            "hold_ms": 200,
            "echo_suppression_ms": 350,
            "barge_cooldown_ms": 250,
            "asr_conf_threshold": 0.75,
        },
    }
    if overrides:
        policy["vad"].update(overrides)
    return policy


class DualVADAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _FakeBus()
        self.current_time = 0

    def _create_aggregator(
        self,
        *,
        sid: str = "sid-a",
        mode: str = "or",
        priority: str = "asr",
        overrides: Optional[Dict[str, Any]] = None,
    ) -> tuple[VADAggregator, Dict[str, Any]]:
        snapshot = _base_policy(mode, priority, overrides)

        def supplier() -> Dict[str, Any]:
            return snapshot

        aggregator = VADAggregator(sid, self.bus, supplier)
        aggregator._now_ms = lambda: int(self.current_time)  # type: ignore[attr-defined]
        return aggregator, snapshot

    def _advance(self, delta_ms: int) -> None:
        self.current_time += delta_ms

    def _collect_events(self, event_type: str) -> List[Dict[str, Any]]:
        return [evt for evt in self.bus.events if evt.get("type") == event_type]

    def test_or_mode_grants_from_auto_or_asr(self) -> None:
        aggregator, _ = self._create_aggregator()
        grants: List[tuple[str, Dict[str, Any]]] = []
        aggregator.set_grant_handler(lambda source, info: grants.append((source, dict(info))))
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()

        self.current_time = 400
        for _ in range(12):
            aggregator.feed_auto_energy(-28.0)
            self._advance(20)
        for _ in range(12):
            aggregator.feed_auto_energy(-70.0)
            self._advance(20)

        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0][0], "auto_vad")

        auto_events = self._collect_events("EVT_VAD")
        phases = [evt.get("phase") for evt in auto_events if evt.get("source") == "auto_vad"]
        self.assertIn("start", phases)
        self.assertIn("stop", phases)

        decisions = [
            evt
            for evt in self.bus.events
            if evt.get("type") == "EVT_VAD_DECISION" and evt.get("granted")
        ]
        self.assertTrue(decisions)
        self.bus.events.clear()

        # ASR-only path still grants in OR mode.
        last_grant = aggregator._last_grant_ms  # type: ignore[attr-defined]
        aggregator.on_tts_end()
        self.current_time = last_grant
        aggregator.on_tts_start()
        self.current_time = last_grant + 500
        grants.clear()
        aggregator.feed_asr_evidence("req-1", 0.85, "hi")
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0][0], "asr_evidence")

    def test_and_mode_requires_both_sources(self) -> None:
        aggregator, _ = self._create_aggregator(mode="and")
        grants: List[tuple[str, Dict[str, Any]]] = []
        aggregator.set_grant_handler(lambda source, info: grants.append((source, dict(info))))
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()
        self.current_time = 400

        for _ in range(12):
            aggregator.feed_auto_energy(-28.0)
            self._advance(20)
        for _ in range(12):
            aggregator.feed_auto_energy(-70.0)
            self._advance(20)
        self.assertFalse(grants)

        aggregator.feed_asr_evidence("req-2", 0.9, "hello")
        self.assertFalse(grants)

        for _ in range(12):
            aggregator.feed_auto_energy(-27.0)
            self._advance(20)
        aggregator.feed_auto_energy(-65.0)

        self.assertEqual(len(grants), 1)
        reasons = grants[0][1].get("reasons", [])
        joined = " ".join(reasons)
        self.assertIn("auto_vad_active", joined)
        self.assertIn("asr_evidence_active", joined)

    def test_priority_modes_respect_requested_source(self) -> None:
        asr_priority, _ = self._create_aggregator(mode="priority", priority="asr")
        asr_priority.set_grant_handler(lambda source, info: self.fail("auto path should not grant"))
        asr_priority.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        asr_priority.on_tts_start()
        self.current_time = 400
        for _ in range(12):
            asr_priority.feed_auto_energy(-28.0)
            self._advance(20)
        asr_priority.feed_auto_energy(-70.0)

        asr_grants: List[str] = []
        asr_priority.set_grant_handler(lambda source, info: asr_grants.append(source))
        asr_priority.feed_asr_evidence("req-3", 0.85, "hi")
        self.assertEqual(asr_grants, ["asr_evidence"])

        auto_priority, _ = self._create_aggregator(mode="priority", priority="auto")
        auto_priority.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        auto_priority.on_tts_start()
        self.current_time = 400
        auto_grants: List[str] = []
        auto_priority.set_grant_handler(lambda source, info: auto_grants.append(source))
        auto_priority.feed_asr_evidence("req-4", 0.9, "no grant")
        self.assertFalse(auto_grants)

        for _ in range(12):
            auto_priority.feed_auto_energy(-28.0)
            self._advance(20)
        auto_priority.feed_auto_energy(-55.0)
        self.assertEqual(auto_grants, ["auto_vad"])

    def test_echo_suppression_blocks_initial_energy(self) -> None:
        aggregator, _ = self._create_aggregator()
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()
        grants: List[str] = []
        aggregator.set_grant_handler(lambda source, info: grants.append(source))

        self.current_time = 100
        for _ in range(10):
            aggregator.feed_auto_energy(-26.0)
            self._advance(20)
        self.assertFalse(grants)

        self.current_time = 400
        for _ in range(12):
            aggregator.feed_auto_energy(-28.0)
            self._advance(20)
        aggregator.feed_auto_energy(-60.0)
        self.assertEqual(grants, ["auto_vad"])

    def test_hysteresis_and_cooldown_behavior(self) -> None:
        aggregator, _ = self._create_aggregator()
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()
        grants: List[str] = []
        aggregator.set_grant_handler(lambda source, info: grants.append(source))
        self.current_time = 400

        for _ in range(5):
            aggregator.feed_auto_energy(-28.0)
            self._advance(20)
        self.assertFalse(grants)
        aggregator.feed_auto_energy(-70.0)

        for _ in range(12):
            aggregator.feed_auto_energy(-27.0)
            self._advance(20)
        aggregator.feed_auto_energy(-65.0)
        self.assertEqual(grants, ["auto_vad"])
        last_grant = aggregator._last_grant_ms  # type: ignore[attr-defined]

        aggregator.on_tts_end()
        self.current_time = last_grant
        aggregator.on_tts_start()
        self.current_time = last_grant + 100
        aggregator.feed_asr_evidence("req-5", 0.9, "hi")
        self.assertEqual(len(grants), 1)

        self.current_time = last_grant + 500
        aggregator.feed_asr_evidence("req-6", 0.9, "hi again")
        self.assertEqual(len(grants), 2)

    def test_single_grant_per_tts(self) -> None:
        aggregator, _ = self._create_aggregator()
        grants: List[str] = []
        aggregator.set_grant_handler(lambda source, info: grants.append(source))
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()
        self.current_time = 400
        aggregator.feed_asr_evidence("req-7", 0.9, "hi")
        self.assertEqual(grants, ["asr_evidence"])
        aggregator.feed_auto_energy(-25.0)
        aggregator.feed_auto_energy(-25.0)
        self.assertEqual(grants, ["asr_evidence"])

        aggregator.on_tts_end()
        self.current_time = 0
        aggregator.on_tts_start()
        self.current_time = 1000
        aggregator.feed_auto_energy(-27.0)
        aggregator.feed_auto_energy(-27.0)
        for _ in range(10):
            aggregator.feed_auto_energy(-27.0)
        aggregator.feed_auto_energy(-60.0)
        self.assertEqual(grants[-1], "auto_vad")

    def test_adaptation_adjusts_margin_and_threshold(self) -> None:
        aggregator, _ = self._create_aggregator()
        aggregator.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        aggregator.on_tts_start()
        self.current_time = 400
        aggregator.feed_asr_evidence("req-low", 0.2, "noise")
        initial_margin = aggregator._margin_db  # type: ignore[attr-defined]
        initial_threshold = aggregator.dynamic_threshold_dbfs()

        for _ in range(12):
            aggregator.feed_auto_energy(-28.0)
            self._advance(20)
        aggregator.feed_auto_energy(-60.0)
        self.assertGreaterEqual(aggregator._margin_db, initial_margin)
        self.assertGreaterEqual(aggregator.dynamic_threshold_dbfs(), initial_threshold)

        aggregator.on_tts_end()
        for _ in range(200):
            aggregator.feed_auto_energy(-52.0)

        noisy_nf = aggregator._nf_dbfs  # type: ignore[attr-defined]
        self.assertLess(noisy_nf, -40.0)

        miss_agg, _ = self._create_aggregator(mode="and")
        miss_agg.on_engine_mode_change(RESPONDING)
        self.current_time = 0
        miss_agg.on_tts_start()
        self.current_time = 400
        baseline_margin = miss_agg._margin_db  # type: ignore[attr-defined]
        for _ in range(50):
            miss_agg.feed_auto_energy(-70.0)
        miss_agg.feed_asr_evidence("req-hi", 0.9, "hello")
        self.assertLessEqual(miss_agg._margin_db, baseline_margin)
        miss_agg.feed_auto_energy(-70.0)
        self.assertGreaterEqual(miss_agg.dynamic_threshold_dbfs(), miss_agg._nf_dbfs + miss_agg._margin_db)  # type: ignore[attr-defined]

    def test_per_session_isolation(self) -> None:
        aggregator_a, _ = self._create_aggregator(sid="sid-a")
        aggregator_b, _ = self._create_aggregator(sid="sid-b")
        grants_a: List[str] = []
        grants_b: List[str] = []
        aggregator_a.set_grant_handler(lambda source, info: grants_a.append(source))
        aggregator_b.set_grant_handler(lambda source, info: grants_b.append(source))

        aggregator_a.on_engine_mode_change(RESPONDING)
        aggregator_a.on_tts_start()
        self.current_time = 400
        for _ in range(12):
            aggregator_a.feed_auto_energy(-28.0)
            self._advance(20)
        aggregator_a.feed_auto_energy(-60.0)

        self.assertEqual(grants_a, ["auto_vad"])
        self.assertFalse(grants_b)

    def test_engine_integration_path(self) -> None:
        bus = _FakeBus()
        exporter = _FakeExporter()
        engine = EngineV2(exporter, telemetry_bus=bus)
        sid = "sid-eng"
        engine.policy_snapshot = _base_policy()
        engine._schedule_barge_confirmation = lambda _: None  # type: ignore[assignment]
        engine.reapply_policy = lambda overrides=None: False  # type: ignore[assignment]

        engine._ensure_session(sid)
        aggregator = engine._aggregators[sid]
        self.current_time = 0
        aggregator._now_ms = lambda: int(self.current_time)  # type: ignore[attr-defined]

        engine.on_tts_start(sid, "utt-1")
        session = engine._ensure_session(sid)
        self.assertEqual(session.state, RESPONDING)

        self.current_time = 400
        engine.on_asr_partial(sid, "req-10", 0.85, "go")

        self.assertEqual(session.state, CONFIRMING_BARGE)

        types = [evt.get("type") for evt in bus.events]
        barge_index = types.index(EVT_BARGE_DETECTED)
        tts_end_index = types.index(EVT_TTS_END)
        mask_index = next(
            i
            for i, evt in enumerate(bus.events)
            if evt.get("type") == EVT_TTS_MASK and evt.get("phase") == "off"
        )
        mic_index = next(
            i
            for i, evt in enumerate(bus.events)
            if evt.get("type") == EVT_MIC_GATE
            and not evt.get("meta", {}).get("gate", {}).get("reasons", {}).get("tts_active", True)
        )

        self.assertLess(barge_index, tts_end_index)
        self.assertLess(tts_end_index, mask_index)
        self.assertLess(mask_index, mic_index)

        tts_end_event = bus.events[tts_end_index]
        self.assertEqual(tts_end_event.get("reason"), "canceled")

        say_end_events = [evt for evt in bus.events if evt.get("type") == EVT_ACTION_SAY_END]
        self.assertTrue(say_end_events)
        self.assertEqual(say_end_events[-1].get("reason"), "canceled")

        engine._complete_auto_barge(sid)
        self.assertEqual(session.state, LISTENING)

        confirmed_events = [evt for evt in bus.events if evt.get("type") == EVT_BARGE_CONFIRMED]
        self.assertTrue(confirmed_events)


if __name__ == "__main__":
    unittest.main()
