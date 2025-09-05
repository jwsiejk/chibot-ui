# app/services/providers_real/openai_http_provider.py
import os, json, uuid
from ..httputil import http_json

class OpenAIHTTPProvider:
    """Production OpenAI provider using Chat Completions API."""
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIHTTPProvider")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        self.timeout = int(os.environ.get("OPENAI_TIMEOUT_SEC", "30"))

    def new_turn_id(self) -> str:
        return str(uuid.uuid4())

    def generate_reply(self, prompt: str, persona=None, teacher_move=None, context=None) -> str:
        sys = (persona or {}).get("system") or "You are Chip, a helpful virtual systems engineer."
        messages = [{"role":"system","content":sys},{"role":"user","content":(prompt or '').strip()}]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(os.environ.get("OPENAI_TEMPERATURE","0.6")),
        }
        url = f"{self.base_url}/v1/chat/completions"
        out = http_json(
            url, payload=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=self.timeout,
            retries=int(os.environ.get("OPENAI_RETRIES","2")),
            breaker_key="openai.chat",
            breaker_threshold=int(os.environ.get("OPENAI_CB_THRESHOLD","3")),
            breaker_cooldown=float(os.environ.get("OPENAI_CB_COOLDOWN","10"))
        )
        txt = (out.get("choices") or [{}])[0].get("message",{}).get("content","").strip() or "ok"
        return txt
