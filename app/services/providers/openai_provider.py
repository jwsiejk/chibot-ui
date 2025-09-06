# app/services/providers/openai_provider.py
# Shim that preserves the historical import path while ensuring we use the real provider.
from ..providers_real.openai_http_provider import OpenAIHTTPProvider as OpenAIProvider
