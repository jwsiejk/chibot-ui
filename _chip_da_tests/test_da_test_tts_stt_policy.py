# tests/test_tts_stt_policy.py
import os, importlib, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def reload_mod(modname):
    if modname in sys.modules:
        del sys.modules[modname]
    return importlib.import_module(modname)

def test_tts_auto_with_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY","k")
    monkeypatch.delenv("APP_ENV", raising=False)
    m = reload_mod("app.services.tts_provider")
    assert m.get_tts_provider_name({}) == "elevenlabs"

def test_tts_auto_no_key_disallowed_in_prod(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV","production")
    m = reload_mod("app.services.tts_provider")
    try:
        m.get_tts_provider_name({})
        assert False
    except RuntimeError as e:
        assert "disallowed" in str(e)

def test_stt_auto_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","k")
    monkeypatch.delenv("APP_ENV", raising=False)
    m = reload_mod("app.services.stt_provider")
    assert m.get_stt_provider_name({}) == "whisper"

def test_stt_auto_no_key_disallowed_in_prod(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV","production")
    m = reload_mod("app.services.stt_provider")
    try:
        m.get_stt_provider_name({})
        assert False
    except RuntimeError as e:
        assert "disallowed" in str(e)
