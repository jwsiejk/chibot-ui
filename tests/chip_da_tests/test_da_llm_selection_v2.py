# test_da_llm_selection_v2.py
import os, importlib, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

def reload_llm():
    if "app.services.llm_provider" in sys.modules:
        del sys.modules["app.services.llm_provider"]
    return importlib.import_module("app.services.llm_provider")

def test_auto_with_key_picks_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","x")
    llm = reload_llm()
    assert llm.get_provider_name({}) == "openai"

def test_auto_without_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm = reload_llm()
    try:
        llm.get_provider_name({})  # auto path, no key
        assert False
    except RuntimeError as e:
        assert "No OPENAI_API_KEY" in str(e)

def test_explicit_mock_allowed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm = reload_llm()
    assert llm.get_provider_name({"llm_provider":"mock"}) == "mock"
