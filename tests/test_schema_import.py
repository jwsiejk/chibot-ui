"""Regression tests for the websocket schema module."""

import importlib


def test_schema_v1_imports_cleanly():
    module = importlib.import_module("app.ws.schema_v1")

    # Spot-check a known type to ensure the module exposes the expected metadata.
    assert "Configure" in module._ALLOWED_TYPES
