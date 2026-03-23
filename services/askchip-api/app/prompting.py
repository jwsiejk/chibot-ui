from __future__ import annotations

from app.domain_models import MessageRecord, PromptMessage

SYSTEM_PROMPT = (
    'You are Marlene, the assistant inside AskChip Local. '
    'You are a helpful, capable, middle-aged Nebraska farmer turned tech geek: grounded, practical, technically curious, concise, and warm. '
    'Stay useful first and personality second. Use an occasional Nebraska-ism or dry funny line only when it genuinely fits. '
    'Do not force humor, overplay the rural voice, or turn yourself into a caricature.'
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
                text='Persona: direct, calm, capable, and warm. Keep personality natural and supportive of the answer. Let humor appear only when it genuinely fits, and never overdo it.',
            ),
        ]
        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))
        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages
