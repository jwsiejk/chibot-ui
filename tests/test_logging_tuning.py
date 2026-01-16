from __future__ import annotations

import importlib
import logging
import sys
from unittest import mock

import pytest


def _reset_logger_dict(monkeypatch: pytest.MonkeyPatch) -> dict[str, logging.Logger]:
    logger_dict: dict[str, logging.Logger] = {}
    monkeypatch.setattr(logging.root.manager, "loggerDict", logger_dict)
    return logger_dict


def _import_asgi_gateway(monkeypatch: pytest.MonkeyPatch):
    sys.modules.pop("app.asgi_gateway", None)

    import types

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=object))
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "jwt", types.SimpleNamespace())

    monkeypatch.setenv("SECRET_KEY", "test-secret")

    def _stub_module(name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    _stub_module(
        "app.auth.http_handlers",
        get_me=lambda *_, **__: None,
        post_login=lambda *_, **__: None,
        post_profile=lambda *_, **__: None,
        post_ws_token=lambda *_, **__: None,
        _session_email=lambda *_, **__: None,
        _is_admin=lambda *_, **__: None,
    )
    _stub_module(
        "app.security.jwt_utils",
        mint_ws_token=lambda *_, **__: "token",
        verify_ws_token=lambda *_, **__: True,
    )
    _stub_module(
        "app.ws.adapter",
        CHAT_V2_SUBPROTOCOL="askchip.chat.v2",
        ChatV2Adapter=type("ChatV2Adapter", (), {}),
    )
    _stub_module("app.voice_v2.engine", EngineV2=type("EngineV2", (), {}))
    _stub_module("app.voice_v2.tts_runtime", TTSRuntime=type("TTSRuntime", (), {}))
    _stub_module("app.db.neon", get_user=lambda *_, **__: None, profile_complete=lambda *_, **__: None, upsert_user=lambda *_, **__: None)

    import app.firehose as firehose
    import app.logging_config as logging_config
    import app.logging_setup as logging_setup
    import app.logging_tuning as logging_tuning

    monkeypatch.setattr(logging_config, "configure_logging", lambda *_, **__: None)
    monkeypatch.setattr(logging_tuning, "tune_logging_noise", lambda *_, **__: None)
    monkeypatch.setattr(logging_setup, "install_bus_handler", lambda *_, **__: None)
    monkeypatch.setattr(firehose, "is_firehose_enabled", lambda: False)

    return importlib.import_module("app.asgi_gateway")


def test_tune_logging_noise_silences_websocket_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASKCHIP_WS_TEXT_DEBUG", raising=False)
    _reset_logger_dict(monkeypatch)

    noisy_names = (
        "websockets.server",
        "uvicorn.protocols.websockets.websockets_impl",
        "wsproto.connection",
        "uvicorn.protocols.websockets.wsproto_impl",
    )
    untouched_name = "app.ws.adapter"

    for name in (*noisy_names, untouched_name):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

    import app.logging_tuning as logging_tuning

    logging_tuning.tune_logging_noise()

    for name in noisy_names:
        logger = logging.getLogger(name)
        assert logger.level == logging.WARNING
        assert logger.propagate is False

    clean_logger = logging.getLogger(untouched_name)
    assert clean_logger.level == logging.DEBUG
    assert clean_logger.propagate is True


def test_tune_logging_noise_respects_env_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKCHIP_WS_TEXT_DEBUG", "yes")
    _reset_logger_dict(monkeypatch)

    noisy_name = "websockets.server"
    logger = logging.getLogger(noisy_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True

    import app.logging_tuning as logging_tuning

    logging_tuning.tune_logging_noise()

    logger = logging.getLogger(noisy_name)
    assert logger.level == logging.DEBUG
    assert logger.propagate is True


def test_uvicorn_wsproto_silenced_when_debug_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKCHIP_WS_TEXT_DEBUG", "true")
    _reset_logger_dict(monkeypatch)

    uvicorn_logger = logging.getLogger("uvicorn.protocols.websockets.websockets_impl")
    wsproto_logger = logging.getLogger("uvicorn.protocols.websockets.wsproto_impl")
    extra_logger = logging.getLogger("uvicorn.protocols.websockets.debug_trace")

    for logger in (uvicorn_logger, wsproto_logger, extra_logger):
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

    asgi_gateway = _import_asgi_gateway(monkeypatch)

    monkeypatch.delenv("ASKCHIP_WS_TEXT_DEBUG", raising=False)

    asgi_gateway._silence_uvicorn_ws_frame_debug_logs()

    for logger in (uvicorn_logger, wsproto_logger, extra_logger):
        assert logger.level == logging.WARNING
        assert logger.propagate is False


def test_uvicorn_wsproto_respects_env_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKCHIP_WS_TEXT_DEBUG", "true")
    _reset_logger_dict(monkeypatch)

    asgi_gateway = _import_asgi_gateway(monkeypatch)

    fake_logger = mock.Mock(spec=logging.Logger)
    fake_logger.propagate = True
    get_logger = mock.Mock(return_value=fake_logger)
    monkeypatch.setattr(asgi_gateway.logging, "getLogger", get_logger)

    asgi_gateway._silence_uvicorn_ws_frame_debug_logs()

    get_logger.assert_not_called()
    fake_logger.setLevel.assert_not_called()
