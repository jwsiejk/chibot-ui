"""Tests for the interaction policy loader utilities."""
from __future__ import annotations

import unittest

from app.policy.loader import load_interaction_policy


class InteractionPolicyLoaderTests(unittest.TestCase):
    """Validate the deterministic snapshot returned by ``load_interaction_policy``."""

    def test_defaults_shape_and_types(self) -> None:
        snapshot = load_interaction_policy()

        expected_keys = {
            "mode",
            "allow_auto_vad",
            "barge_in_enabled",
            "auto_commit_when_ready",
            "telemetry",
        }
        self.assertEqual(set(snapshot.keys()), expected_keys)

        self.assertEqual(snapshot["mode"], "idle")
        self.assertIs(snapshot["allow_auto_vad"], True)
        self.assertIs(snapshot["barge_in_enabled"], True)
        self.assertIs(snapshot["auto_commit_when_ready"], True)

        telemetry = snapshot["telemetry"]
        self.assertIsInstance(telemetry, dict)
        self.assertIs(telemetry["enabled"], True)
        self.assertEqual(telemetry["level"], "debug")

        categories = telemetry["categories"]
        self.assertIsInstance(categories, dict)
        expected_categories = {
            "ws",
            "audio",
            "policy",
            "tts",
            "gate",
            "barge",
            "asr",
            "nlu",
            "nlg",
            "client_ui",
            "provider_debug",
        }
        self.assertEqual(set(categories.keys()), expected_categories)
        for key in expected_categories:
            self.assertIs(categories[key], True)

        redaction = telemetry["redaction"]
        self.assertEqual(redaction, {"pii": True, "secrets": True, "text": False})

        sampling = telemetry["sampling"]
        self.assertEqual(sampling, {"percent": 100})

    def test_overrides_shallow(self) -> None:
        overrides = {"telemetry": {"level": "info"}}
        snapshot = load_interaction_policy(overrides)

        self.assertEqual(snapshot["mode"], "idle")
        self.assertIs(snapshot["allow_auto_vad"], True)
        self.assertIs(snapshot["barge_in_enabled"], True)
        self.assertIs(snapshot["auto_commit_when_ready"], True)

        telemetry = snapshot["telemetry"]
        self.assertEqual(telemetry, {"level": "info"})
        self.assertNotIn("categories", telemetry)

        # Loading defaults again should not be impacted by the overrides call.
        fresh_defaults = load_interaction_policy()
        self.assertIn("categories", fresh_defaults["telemetry"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
