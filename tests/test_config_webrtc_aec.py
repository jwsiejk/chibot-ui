import importlib

import pytest


def _reload_config(monkeypatch, value):
    module = importlib.import_module("app.config")
    if value is None:
        monkeypatch.delenv("FEATURE_WEBRTC_AEC", raising=False)
    else:
        monkeypatch.setenv("FEATURE_WEBRTC_AEC", value)
    return importlib.reload(module)


def _restore_default(monkeypatch):
    monkeypatch.delenv("FEATURE_WEBRTC_AEC", raising=False)
    module = importlib.import_module("app.config")
    importlib.reload(module)


def test_webrtc_aec_enabled_by_default(monkeypatch):
    module = _reload_config(monkeypatch, None)
    try:
        assert module.FEATURE_WEBRTC_AEC is True

        policy = module.build_session_policy(admin_overrides=None, env=None)
        assert policy.get("capture", {}).get("mode") == "webrtc_aec"
    finally:
        _restore_default(monkeypatch)


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_webrtc_aec_can_be_disabled(monkeypatch, value):
    module = _reload_config(monkeypatch, value)
    try:
        assert module.FEATURE_WEBRTC_AEC is False

        policy = module.build_session_policy(admin_overrides=None, env=None)
        assert policy.get("capture", {}).get("mode") == "pcm"
    finally:
        _restore_default(monkeypatch)
