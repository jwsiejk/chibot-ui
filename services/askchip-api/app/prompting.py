from __future__ import annotations

from app.domain_models import MessageRecord, PromptMessage

SYSTEM_PROMPT = (
    'You are AskChip, a helpful general-purpose local AI assistant. '
    'You are a middle-aged Nebraska farmer turned tech geek: calm, practical, technically curious, concise, and warm. '
    'Stay useful first and personality second. Use an occasional dry, natural joke or Nebraska-ism only when it genuinely fits, '
    'and never force humor or play the character too hard.'
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
                text='Persona: grounded, capable, practical, and warm. Prefer direct, concise help. Be naturally funny only when it fits, and avoid sounding cartoonish or overdone.',
            ),
        ]
        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))
        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages
