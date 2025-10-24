"""Tests for the logging configuration helpers."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

import app.logging_config as logging_config


class LoggingConfigurationTests(unittest.TestCase):
    """Validate log level defaults and policy-driven overrides."""

    def tearDown(self) -> None:
        logging_config._LOG_CONFIGURED = False  # type: ignore[attr-defined]
        logging_config._MANAGED_LOGGERS.clear()  # type: ignore[attr-defined]

    def _patch_loggers(self):
        root_logger = mock.Mock(spec=logging.Logger)
        root_logger.handlers = []

        named_loggers: dict[str, mock.Mock] = {}

        def _get_logger(name: str | None = None):
            if name is None:
                return root_logger
            logger = named_loggers.get(name)
            if logger is None:
                logger = mock.Mock(spec=logging.Logger)
                logger.handlers = []
                named_loggers[name] = logger
            return logger

        return root_logger, named_loggers, mock.patch("logging.getLogger", side_effect=_get_logger)

    def _patch_policy(self, telemetry: dict[str, object]):
        snapshot = {"telemetry": telemetry}
        return mock.patch("app.logging_config.load_interaction_policy", return_value=snapshot)

    def test_policy_defaults_drive_debug_logging(self) -> None:
        root_logger, named_loggers, patch_logger = self._patch_loggers()

        telemetry = {"enabled": True, "level": "debug"}

        with patch_logger, self._patch_policy(telemetry), mock.patch("logging.basicConfig") as basic_config:
            logging_config.configure_logging()

        basic_config.assert_called_once_with(level=logging.INFO)
        root_logger.setLevel.assert_called_once_with(logging.DEBUG)
        self.assertIn("uvicorn", named_loggers)
        named_loggers["uvicorn"].setLevel.assert_called_once_with(logging.DEBUG)

    def test_policy_disable_sets_warning_level(self) -> None:
        root_logger, named_loggers, patch_logger = self._patch_loggers()

        telemetry = {"enabled": False}

        with patch_logger, self._patch_policy(telemetry), mock.patch("logging.basicConfig"):
            logging_config.configure_logging()

        root_logger.setLevel.assert_called_once_with(logging.WARNING)
        self.assertIn("uvicorn.error", named_loggers)
        named_loggers["uvicorn.error"].setLevel.assert_called_once_with(logging.WARNING)

    def test_apply_logging_policy_updates_managed_loggers(self) -> None:
        root_logger, named_loggers, patch_logger = self._patch_loggers()

        telemetry = {"enabled": True, "level": "info"}

        with patch_logger, self._patch_policy(telemetry), mock.patch("logging.basicConfig"):
            logging_config.configure_logging()

        root_logger.reset_mock()
        for logger in named_loggers.values():
            logger.reset_mock()

        override = {"telemetry": {"enabled": True, "level": "error"}}
        logging_config.apply_logging_policy(override)

        root_logger.setLevel.assert_called_once_with(logging.ERROR)
        for logger in named_loggers.values():
            logger.setLevel.assert_called_once_with(logging.ERROR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

