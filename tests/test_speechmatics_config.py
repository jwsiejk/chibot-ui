import importlib
import os

from app import config as config_module


def test_speechmatics_realtime_url_expands_env(monkeypatch):
    original_env_url = os.getenv("SPEECHMATICS_REALTIME_URL")
    original_region = os.getenv("SM_RT_REGION")
    original_module_url = config_module.SPEECHMATICS_REALTIME_URL

    monkeypatch.setenv("SM_RT_REGION", "eu2")
    monkeypatch.setenv(
        "SPEECHMATICS_REALTIME_URL",
        "wss://$SM_RT_REGION.rt.speechmatics.com/v2",
    )

    importlib.reload(config_module)
    try:
        assert (
            config_module.SPEECHMATICS_REALTIME_URL
            == "wss://eu2.rt.speechmatics.com/v2"
        )
    finally:
        if original_env_url is None:
            monkeypatch.delenv("SPEECHMATICS_REALTIME_URL", raising=False)
        else:
            monkeypatch.setenv("SPEECHMATICS_REALTIME_URL", original_env_url)
        if original_region is None:
            monkeypatch.delenv("SM_RT_REGION", raising=False)
        else:
            monkeypatch.setenv("SM_RT_REGION", original_region or "")
        importlib.reload(config_module)
        assert config_module.SPEECHMATICS_REALTIME_URL == original_module_url
