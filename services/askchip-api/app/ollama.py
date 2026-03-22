from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.domain_models import PromptMessage


class OllamaUnavailableError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout_seconds: float = 60.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def stream_chat(self, messages: list[PromptMessage]) -> AsyncIterator[dict[str, Any]]:
        payload = {
            'model': self.model,
            'messages': [{'role': message.role, 'content': message.text} for message in messages],
            'stream': True,
        }
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, transport=self._transport) as client:
                async with client.stream('POST', '/api/chat', json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        body = json.loads(line)
                        message = body.get('message', {})
                        yield {
                            'text': message.get('content', ''),
                            'done': bool(body.get('done', False)),
                            'done_reason': body.get('done_reason', ''),
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
        }
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.post('/api/chat', json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(str(exc)) from exc
