"""Tests for the interaction policy loader utilities."""
from __future__ import annotations

import unittest
from unittest.mock import patch

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
            "media",
            "capture",
        }
        self.assertEqual(set(snapshot.keys()), expected_keys)

        self.assertEqual(snapshot["mode"], "idle")
        self.assertIs(snapshot["allow_auto_vad"], True)
        self.assertIs(snapshot["barge_in_enabled"], True)
        self.assertIs(snapshot["auto_commit_when_ready"], True)

        media = snapshot["media"]
        self.assertIsInstance(media, dict)
        self.assertEqual(
            media,
            {
                "asr_input": "webm_opus",
                "asr_rate_hz": 48000,
                "asr_channels": 1,
                "fallbacks_allowed": False,
            },
        )

        capture = snapshot["capture"]
        self.assertIsInstance(capture, dict)
        self.assertEqual(
            capture,
            {
                "start_on_asr_ready": True,
                "start_on_turn_ready": True,
                "timeslice_ms": 200,
                "mask_during_tts": True,
            },
        )

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
        self.assertIn("media", snapshot)
        self.assertIn("capture", snapshot)

        telemetry = snapshot["telemetry"]
        self.assertEqual(telemetry, {"level": "info"})
        self.assertNotIn("categories", telemetry)

        # Loading defaults again should not be impacted by the overrides call.
        fresh_defaults = load_interaction_policy()
        self.assertIn("categories", fresh_defaults["telemetry"])

    def test_config_policy_overrides_applied(self) -> None:
        overrides = {
            "media": {
                "asr_input": "pcm_16k",
                "asr_rate_hz": 16000,
                "asr_channels": 1,
                "fallbacks_allowed": True,
            },
            "capture": {
                "start_on_asr_ready": False,
                "start_on_turn_ready": True,
                "timeslice_ms": 250,
                "mask_during_tts": False,
            },
        }

        with patch("app.policy.loader.config.POLICY_OVERRIDES", overrides):
            snapshot = load_interaction_policy()

        self.assertEqual(snapshot["media"]["asr_input"], "pcm_16k")
        self.assertTrue(snapshot["media"]["fallbacks_allowed"])
        self.assertEqual(snapshot["capture"]["timeslice_ms"], 250)
        self.assertFalse(snapshot["capture"]["start_on_asr_ready"])
        self.assertFalse(snapshot["capture"]["mask_during_tts"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
