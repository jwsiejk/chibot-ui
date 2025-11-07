import importlib

import pytest


def _reload_config(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SPEECHMATICS_REALTIME_URL", raising=False)
    else:
        monkeypatch.setenv("SPEECHMATICS_REALTIME_URL", value)

    module = importlib.import_module("app.config")
    importlib.reload(module)
    return module


def test_default_url_when_missing(monkeypatch):
    module = _reload_config(monkeypatch, None)
    assert module.SPEECHMATICS_REALTIME_URL == "wss://us1.rt.speechmatics.com/v2"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("wss://eu2.rt.speechmatics.com/v2", "wss://eu2.rt.speechmatics.com/v2"),
        ("us1", "wss://us1.rt.speechmatics.com/v2"),
        (
            "eu2.rt.speechmatics.com/v2",
            "wss://eu2.rt.speechmatics.com/v2",
        ),
        (
            "us1.rt.speechmatics.com/custom",
            "wss://us1.rt.speechmatics.com/custom",
        ),
    ],
)
def test_legacy_inputs_are_normalised(monkeypatch, value, expected):
    module = _reload_config(monkeypatch, value)
    assert module.SPEECHMATICS_REALTIME_URL == expected


def test_rejects_non_wss_urls(monkeypatch):
    with pytest.raises(ValueError):
        _reload_config(monkeypatch, "http://us1.rt.speechmatics.com/v2")
