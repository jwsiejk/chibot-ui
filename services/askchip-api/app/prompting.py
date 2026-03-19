from __future__ import annotations

from app.domain_models import MessageRecord

SYSTEM_PROMPT = (
    'You are AskChip, a helpful general-purpose local AI assistant. '
    'You are a Nebraska ex-farmer turned techy who sounds grounded and capable. '
    'Use an occasional dry one-liner when it naturally fits, but do not become cartoonish.'
)


class PromptAssembler:
    def __init__(self, transcript_window: int = 6) -> None:
        self.transcript_window = transcript_window

    def build_messages(self, transcript: list[MessageRecord], user_text: str) -> list[dict[str, str]]:
        recent = transcript[-self.transcript_window :]
        messages: list[dict[str, str]] = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {
                'role': 'system',
                'content': 'Persona: practical, calm, technically curious, and concise. Prefer direct answers with light warmth.',
            },
        ]
        for item in recent:
            if item.role == 'assistant' and not item.content:
                continue
            messages.append({'role': item.role, 'content': item.content})
        if not recent or recent[-1].role != 'user' or recent[-1].content != user_text:
            messages.append({'role': 'user', 'content': user_text})
        return messages
