from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.domain_models import PromptMessage


class OllamaUnavailableError(RuntimeError):
    pass


class OllamaModelUnavailableError(OllamaUnavailableError):
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f'configured Ollama model is not installed locally: {model}. Run `ollama pull {model}`.')


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        keep_alive: str = '30m',
        num_ctx: int = 8192,
        num_parallel: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.num_parallel = num_parallel
        self._transport = transport

    async def stream_chat(
        self,
        messages: list[PromptMessage],
        *,
        think: bool,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            'model': self.model,
            'messages': [{'role': message.role, 'content': message.text} for message in messages],
            'stream': True,
            'think': think,
            'keep_alive': self.keep_alive,
            'options': {'num_ctx': self.num_ctx},
        }
        if options:
            payload['options'].update(options)
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, transport=self._transport) as client:
                async with client.stream('POST', '/api/chat', json=payload) as response:
                    self._raise_for_status_with_model_hint(response)
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        body = json.loads(line)
                        message = body.get('message', {})
                        thinking_text = message.get('thinking')
                        yield {
                            'text': message.get('content', ''),
                            'done': bool(body.get('done', False)),
                            'done_reason': body.get('done_reason', ''),
                            'thinking_present': isinstance(thinking_text, str) and bool(thinking_text.strip()),
                            'metrics': {
                                key: body.get(key)
                                for key in ('total_duration', 'load_duration', 'prompt_eval_count', 'prompt_eval_duration', 'eval_count', 'eval_duration')
                                if body.get(key) is not None
                            },
                        }
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaUnavailableError(str(exc)) from exc

    async def warmup(self) -> None:
        payload = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': 'Reply with OK.'}],
            'stream': False,
            'think': False,
            'keep_alive': self.keep_alive,
            'options': {'num_ctx': self.num_ctx},
        }
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.post('/api/chat', json=payload)
                self._raise_for_status_with_model_hint(response)
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(str(exc)) from exc

    async def ensure_model_available(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.get('/api/tags')
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaUnavailableError(str(exc)) from exc

        models = payload.get('models')
        if not isinstance(models, list):
            raise OllamaUnavailableError('unexpected /api/tags response from Ollama')
        installed_names = {str(item.get('name', '')).strip() for item in models if isinstance(item, dict)}
        if self.model not in installed_names:
            raise OllamaModelUnavailableError(self.model)

    def _raise_for_status_with_model_hint(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = response.text.lower()
        if response.status_code in {400, 404} and ('not found' in body or 'no such model' in body) and self.model.lower() in body:
            raise OllamaModelUnavailableError(self.model)
        response.raise_for_status()
