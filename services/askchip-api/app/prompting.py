from __future__ import annotations

from app.domain_models import MessageRecord, PromptMessage

SYSTEM_PROMPT = (
    'You are AskChip, a helpful general-purpose local AI assistant. '
    'You are a Nebraska ex-farmer turned techy who sounds grounded and capable. '
    'Use an occasional dry one-liner when it naturally fits, but do not become cartoonish.'
)


class PromptAssembler:
    def __init__(self, transcript_window: int = 6) -> None:
        self.transcript_window = transcript_window

    def build_messages(self, transcript: list[MessageRecord], user_text: str) -> list[PromptMessage]:
        recent = transcript[-self.transcript_window :]
        messages: list[PromptMessage] = [
            PromptMessage(role='system', text=SYSTEM_PROMPT),
            PromptMessage(
                role='system',
                text='Persona: practical, calm, technically curious, and concise. Prefer direct answers with light warmth.',
            ),
        ]
        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))
        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages
