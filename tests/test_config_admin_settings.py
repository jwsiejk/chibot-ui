import os
import unittest
from unittest.mock import patch

from app import config


class ConfigAdminSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_cache = dict(config._ADMIN_SETTINGS_CACHE)
        self._orig_runtime = dict(config._RUNTIME_FLAGS)
        self._orig_store = config._ADMIN_SETTINGS_STORE
        self._orig_flags = {
            "DIAG_CLIENT_HUD": config.DIAG_CLIENT_HUD,
            "DIAG_AUDIO_GUARD": config.DIAG_AUDIO_GUARD,
            "DIAG_CHUNK_SAMPLE_N": config.DIAG_CHUNK_SAMPLE_N,
            "AUDIO_GUARDRAILS": dict(config.AUDIO_GUARDRAILS),
        }
        self.addCleanup(self._restore_config_state)

        config._ADMIN_SETTINGS_CACHE.clear()
        config._RUNTIME_FLAGS.clear()
        config._ADMIN_SETTINGS_STORE = False

    def _restore_config_state(self) -> None:
        config._ADMIN_SETTINGS_CACHE.clear()
        config._ADMIN_SETTINGS_CACHE.update(self._orig_cache)
        config._RUNTIME_FLAGS.clear()
        config._RUNTIME_FLAGS.update(self._orig_runtime)
        config._ADMIN_SETTINGS_STORE = self._orig_store
        config.DIAG_CLIENT_HUD = self._orig_flags["DIAG_CLIENT_HUD"]
        config.DIAG_AUDIO_GUARD = self._orig_flags["DIAG_AUDIO_GUARD"]
        config.DIAG_CHUNK_SAMPLE_N = self._orig_flags["DIAG_CHUNK_SAMPLE_N"]
        config.AUDIO_GUARDRAILS = dict(self._orig_flags["AUDIO_GUARDRAILS"])
        for key, value in self._orig_runtime.items():
            setattr(config, key, value)

    def test_reload_runtime_flags_emits_snapshot_once(self) -> None:
        updates = {
            "diag_client_hud": True,
            "audio_guardrails": {"enabled": False, "mode": "strict"},
            "diag_audio_guard": False,
            "diag_chunk_sample_n": 7,
        }

        with patch.object(config._log, "info") as mock_info:
            config.update_admin_settings_cache(updates)

        self.assertEqual(mock_info.call_count, 1)
        args = mock_info.call_args.args
        self.assertIn("EVT_ADMIN_SETTINGS_LOAD", args[0])
        snapshot = args[1]
        self.assertTrue(any(item["key"] == "AUDIO_GUARDRAILS" for item in snapshot))
        guardrails_entry = next(item for item in snapshot if item["key"] == "AUDIO_GUARDRAILS")
        self.assertEqual(guardrails_entry["value"], {"enabled": False, "mode": "strict"})
        self.assertEqual(guardrails_entry["source"], "db")
        self.assertEqual(config.AUDIO_GUARDRAILS, {"enabled": False, "mode": "strict"})
        self.assertEqual(config.DIAG_CLIENT_HUD, True)
        self.assertEqual(config.DIAG_AUDIO_GUARD, False)
        self.assertEqual(config.DIAG_CHUNK_SAMPLE_N, 7)

    def test_bool_guardrails_coerces_to_mapping(self) -> None:
        with patch.object(config._log, "info"):
            config.update_admin_settings_cache({"audio_guardrails": True})
        self.assertEqual(config.AUDIO_GUARDRAILS, {"enabled": True})

    def test_env_parse_failure_falls_back_to_default(self) -> None:
        with patch.dict(os.environ, {"AUDIO_GUARDRAILS": "not-json"}, clear=False):
            with patch.object(config._log, "warning") as mock_warning, patch.object(
                config._log, "info"
            ):
                config.reload_runtime_flags()
        self.assertEqual(config.AUDIO_GUARDRAILS, {"enabled": True})
        mock_warning.assert_called_once()


if __name__ == "__main__":  # pragma: no cover - unittest entrypoint
    unittest.main()
