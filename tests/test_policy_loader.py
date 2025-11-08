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
            "voice",
            "greet",
            "suggestions",
            "actions",
            "policy",
            "media",
            "capture",
            "audio",
        }
        self.assertEqual(set(snapshot.keys()), expected_keys)

        self.assertEqual(snapshot["mode"], "idle")
        self.assertIs(snapshot["allow_auto_vad"], True)
        self.assertIs(snapshot["barge_in_enabled"], True)
        self.assertIs(snapshot["auto_commit_when_ready"], True)

        voice = snapshot["voice"]
        self.assertEqual(voice, {"voice_id": "alloy-en-US-001", "locale": "en-US"})

        greet = snapshot["greet"]
        self.assertEqual(greet, {"enabled": True, "mode": "persona", "post_hold_ms": 200})

        suggestions = snapshot["suggestions"]
        self.assertEqual(suggestions, {"on_connect": True, "count": 3})

        media = snapshot["media"]
        self.assertIsInstance(media, dict)
        self.assertEqual(
            media,
            {
                "asr_input": "pcm_16k",
                "asr_rate_hz": 16000,
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

        audio = snapshot["audio"]
        self.assertEqual(audio, {"pipeline": {"mode": "pcm16"}})

        actions = snapshot["actions"]
        self.assertEqual(
            actions,
            {
                "allowed": ["answer"],
                "surface_via_suggestions": True,
                "assistant_turn_sequence": [
                    "assistant.say",
                    "assistant.await_user",
                ],
            },
        )

        policy = snapshot["policy"]
        self.assertIsInstance(policy, dict)
        self.assertEqual(set(policy.keys()), {"recorder", "input", "asr", "routing"})
        self.assertEqual(
            policy["recorder"],
            {"stop_on_tts_start": False, "mute_send_during_tts": True},
        )
        self.assertEqual(
            policy["input"],
            {"require_hotword_to_start": False},
        )
        self.assertEqual(
            policy["routing"],
            {"ws_version": "v2"},
        )

        asr = policy["asr"]
        self.assertIsInstance(asr, dict)
        self.assertFalse(asr["prearm_on_tts_end"])
        self.assertEqual(asr["commit_silence_ms"], 900)

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

    def test_overrides_safe_merge(self) -> None:
        overrides = {
            "telemetry": {
                "level": "info",
                "categories": {"audio": False},
            },
            "policy": {"input": {"require_hotword_to_start": True}},
        }
        snapshot = load_interaction_policy(overrides)

        self.assertEqual(snapshot["mode"], "idle")
        self.assertIs(snapshot["allow_auto_vad"], True)
        self.assertIs(snapshot["barge_in_enabled"], True)
        self.assertIs(snapshot["auto_commit_when_ready"], True)
        self.assertIn("media", snapshot)
        self.assertIn("capture", snapshot)

        telemetry = snapshot["telemetry"]
        self.assertEqual(telemetry["level"], "info")
        categories = telemetry["categories"]
        self.assertIsInstance(categories, dict)
        self.assertFalse(categories["audio"])
        default_categories = load_interaction_policy()["telemetry"]["categories"]
        for key, value in default_categories.items():
            if key == "audio":
                continue
            self.assertEqual(categories[key], value)

        policy = snapshot["policy"]
        self.assertTrue(policy["input"]["require_hotword_to_start"])
        self.assertIn("asr", policy)
        self.assertIn("recorder", policy)
        self.assertIn("routing", policy)

        # Loading defaults again should not be impacted by the overrides call.
        fresh_defaults = load_interaction_policy()
        self.assertIn("categories", fresh_defaults["telemetry"])
        self.assertFalse(fresh_defaults["policy"]["input"]["require_hotword_to_start"])

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
            "policy": {
                "input": {"require_hotword_to_start": True},
            },
        }

        with patch("app.policy.loader.config.POLICY_OVERRIDES", overrides):
            snapshot = load_interaction_policy()

        self.assertEqual(snapshot["media"]["asr_input"], "pcm_16k")
        self.assertTrue(snapshot["media"]["fallbacks_allowed"])
        self.assertEqual(snapshot["capture"]["timeslice_ms"], 250)
        self.assertFalse(snapshot["capture"]["start_on_asr_ready"])
        self.assertFalse(snapshot["capture"]["mask_during_tts"])
        self.assertTrue(snapshot["policy"]["input"]["require_hotword_to_start"])
        self.assertIn("asr", snapshot["policy"])
        self.assertIn("recorder", snapshot["policy"])
        self.assertIn("routing", snapshot["policy"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
