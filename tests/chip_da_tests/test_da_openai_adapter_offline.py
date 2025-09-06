# test_da_openai_adapter_offline.py
import types, sys, pathlib
from app.services.providers.openai_provider import OpenAIProvider

class _Choice:
    def __init__(self, content):
        self.message = types.SimpleNamespace(content=content)
        self.delta = types.SimpleNamespace(content=None)
        self.finish_reason = "stop"
class _Resp:
    def __init__(self, text): self.choices = [ _Choice(text) ]
class _Client:
    class _Chat:
        class _Completions:
            @staticmethod
            def create(**kwargs):
                return _Resp("Hello (adapter fake)")
        completions = _Completions()
    chat = _Chat()

def test_adapter_reads_model_from_cfg_and_uses_injected_client():
    p = OpenAIProvider({"openai_model":"gpt-4o-mini-test"}, client_factory=lambda: _Client())
    out = p.generate_reply("Ping?", persona={"id":"Chip"})
    assert "adapter fake" in out
