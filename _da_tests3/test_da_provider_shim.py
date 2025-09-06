# tests/test_provider_shim.py
from app.services.providers.openai_provider import OpenAIProvider
def test_shim_import():
    assert hasattr(OpenAIProvider, "__init__")
