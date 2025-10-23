"""Deterministic checks for persona quote injections."""

from __future__ import annotations

import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_NLG
from app.voice_v2.llm import LLMAdapter
from app.voice_v2 import persona


class TestPersonaQuotes(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        persona._quote_last_turn_by_sid.clear()

    def tearDown(self) -> None:
        bus.reset()
        persona._quote_last_turn_by_sid.clear()

    def test_persona_quotes_follow_policy(self) -> None:
        data = persona.load_persona()

        # Clarify should always be rejected when forbid_on_clarify policy is set.
        clarify_quote = persona.maybe_pick_quote_for_sid(data, "sid-clarify", "clarify", turn_no=1)
        self.assertIsNone(clarify_quote)

        adapter = LLMAdapter()
        events: list[dict] = []
        token = bus.subscribe("*", events.append)
        try:
            sid = "sid-outline"
            modes = ["outline", "deep_dive"] * 15  # 30 turns total
            for index, mode in enumerate(modes, start=1):
                adapter.generate_persona(
                    sid,
                    f"turn-{index}",
                    f"req-{index}",
                    f"Walk me through topic {index}",
                    {"mode": mode},
                )
        finally:
            bus.unsubscribe(token)

        nlg_events = [evt for evt in events if evt.get("type") == EVT_NLG]
        self.assertEqual(len(nlg_events), 30)

        quote_events = [
            evt
            for evt in nlg_events
            if isinstance(evt.get("metadata"), dict) and evt["metadata"].get("quote_id")
        ]
        quote_rate = len(quote_events) / len(nlg_events)
        self.assertGreaterEqual(quote_rate, 0.05)
        self.assertLessEqual(quote_rate, 0.2)

        for event in quote_events:
            self.assertIn("(As my granddad would say:", event.get("text", ""))
            self.assertIsInstance(event["metadata"]["quote_id"], str)
            self.assertTrue(event["metadata"]["quote_id"])

        # Ensure clarify mode never injects a quote even after other turns.
        events.clear()
        token = bus.subscribe("*", events.append)
        try:
            for index in range(1, 6):
                adapter.generate_persona(
                    "sid-clarify",
                    f"clarify-turn-{index}",
                    f"clarify-req-{index}",
                    "Need more detail?",
                    {"mode": "clarify"},
                )
        finally:
            bus.unsubscribe(token)

        clarify_events = [evt for evt in events if evt.get("type") == EVT_NLG]
        self.assertEqual(len(clarify_events), 5)
        self.assertTrue(
            all(
                not (isinstance(evt.get("metadata"), dict) and evt["metadata"].get("quote_id"))
                for evt in clarify_events
            )
        )


if __name__ == "__main__":
    unittest.main()
