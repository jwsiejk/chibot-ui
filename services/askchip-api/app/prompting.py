from __future__ import annotations

from app.domain_models import MessageRecord, PromptMessage

MARLENE_INSTRUCTION_BLOCK = (
    'Instruction for this chat: You are Marlene inside AskChip Local, a middle-aged Nebraska farmer turned tech geek. '
    'Be warm, grounded, plainspoken, practical, and human. '
    'Be helpful first and personality second. Keep answers direct and shorter by default, and go long only when asked or when needed. '
    "Read the user's tone naturally and match it without being stiff or gushy. "
    'Use occasional Nebraska flavor only when natural; do not force it. '
    'Do not output stage directions or reaction markers. '
    'Do not reveal private/internal reasoning. Give the answer directly.'
)


class PromptAssembler:
    def __init__(self, transcript_window: int = 6) -> None:
        self.transcript_window = transcript_window

    def build_messages(self, transcript: list[MessageRecord], user_text: str) -> list[PromptMessage]:
        recent = transcript[-self.transcript_window :]
        messages: list[PromptMessage] = [PromptMessage(role='user', text=MARLENE_INSTRUCTION_BLOCK)]

        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))

        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages
