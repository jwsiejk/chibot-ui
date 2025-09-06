# tests/test_openai_http_provider.py
import types
from app.services.providers_real.openai_http_provider import OpenAIHTTPProvider

class _Choice:
    def __init__(self, content):
        self.message = types.SimpleNamespace(content=content)
        self.delta = types.SimpleNamespace(content=None)
        self.finish_reason = "stop"
class _Resp:
    def __init__(self, text):
        self.choices = [ _Choice(text) ]
class _Client:
    class _Chat:
        class _Completions:
            @staticmethod
            def create(**kwargs):
                # no network: return fake response
                return _Resp("Hello from OpenAIHTTPProvider fake")
        completions = _Completions()
    chat = _Chat()

def test_generate_reply_returns_text_without_network():
    p = OpenAIHTTPProvider(client=_Client(), model="gpt-4o-mini-test")
    text = p.generate_reply("Ping?", persona={"id":"Chip"})
    assert "Hello from OpenAIHTTPProvider fake" in text

def test_new_turn_id_is_uuid():
    p = OpenAIHTTPProvider(client=_Client())
    tid = p.new_turn_id()
    import uuid
    uuid.UUID(tid)  # raises if invalid
