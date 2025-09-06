# tests/test_llm_provider_selection.py
import os, importlib, sys, pathlib

# Ensure repo root on path
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def reload_llm():
    if "app.services.llm_provider" in sys.modules:
        del sys.modules["app.services.llm_provider"]
    return importlib.import_module("app.services.llm_provider")

def test_auto_with_key_picks_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","x")
    monkeypatch.delenv("APP_ENV", raising=False)
    llm = reload_llm()
    assert llm.get_provider_name({}) == "openai"

def test_auto_without_key_disallowed_in_prod(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV","production")
    llm = reload_llm()
    try:
        llm.get_provider_name({})
        assert False, "should have raised"
    except RuntimeError as e:
        assert "disallowed" in str(e)

def test_auto_without_key_allowed_when_flag_set(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS","true")
    llm = reload_llm()
    assert llm.get_provider_name({}) == "mock"
