"""Tests for the telemetry bus logging handler installation."""

from __future__ import annotations

import logging
from typing import Callable
from unittest import TestCase, mock

from app.logging_setup import TelemetryBusHandler, install_bus_handler


class _DummyHandler(logging.Handler):
    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)


class _DummyLogger:
    def __init__(self) -> None:
        self.handlers: list[logging.Handler] = []

    def addHandler(self, handler: logging.Handler) -> None:  # pragma: no cover - interface shim
        self.handlers.append(handler)

    def setLevel(self, level: int) -> None:  # pragma: no cover - interface shim
        self.level = level


class InstallBusHandlerTests(TestCase):
    """Validate console and telemetry handler installation semantics."""

    def _patch_logging(self, root: _DummyLogger, basic_config: Callable[[int], None]):
        return mock.patch.multiple(
            "app.logging_setup.logging",
            getLogger=mock.Mock(side_effect=lambda name=None: root),
            basicConfig=mock.Mock(side_effect=basic_config),
        )

    def test_basic_config_called_when_no_handlers_exist(self) -> None:
        root = _DummyLogger()

        def _basic(level: int) -> None:
            root.addHandler(_DummyHandler(level))

        with self._patch_logging(root, _basic):
            install_bus_handler(mock.Mock(), level=logging.WARNING)

        # One console handler from basicConfig and one telemetry handler.
        self.assertTrue(
            any(not isinstance(handler, TelemetryBusHandler) for handler in root.handlers),
            "Expected a console handler to be installed via basicConfig",
        )
        telemetry = [handler for handler in root.handlers if isinstance(handler, TelemetryBusHandler)]
        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0].level, logging.WARNING)

    def test_existing_handlers_prevent_duplicate_basic_config(self) -> None:
        root = _DummyLogger()
        root.addHandler(_DummyHandler(logging.INFO))

        with self._patch_logging(root, lambda level: root.addHandler(_DummyHandler(level))):
            install_bus_handler(mock.Mock(), level=logging.ERROR)

        telemetry = [handler for handler in root.handlers if isinstance(handler, TelemetryBusHandler)]
        self.assertEqual(len(telemetry), 1)
        # Existing handler remains untouched and no new non-telemetry handlers were added.
        self.assertEqual(
            sum(1 for handler in root.handlers if not isinstance(handler, TelemetryBusHandler)),
            1,
        )

    def test_second_install_is_a_noop(self) -> None:
        root = _DummyLogger()

        def _basic(level: int) -> None:
            root.addHandler(_DummyHandler(level))

        with self._patch_logging(root, _basic):
            install_bus_handler(mock.Mock())
            # Only telemetry handlers remain would trigger a new stream handler; ensure that
            # calling again does not duplicate the telemetry handler.
            install_bus_handler(mock.Mock())

        telemetry = [handler for handler in root.handlers if isinstance(handler, TelemetryBusHandler)]
        self.assertEqual(len(telemetry), 1)

