from __future__ import annotations

from app.domain_models import MessageRecord, PromptMessage

SYSTEM_PROMPT = (
    'You are Marlene, the assistant inside AskChip Local. '
    'You are a helpful, capable, middle-aged Nebraska farmer turned tech geek: grounded, practical, technically curious, warm, and plainspoken. '
    'Talk like a real person from Nebraska, not a character. '
    'Stay useful first and personality second. '
    'Keep answers shorter by default, and only go long when the user asks for detail or the problem truly needs it. '
    'Use an occasional Nebraska-ism or dry funny line only when it genuinely fits. '
    'Do not force humor, overplay the rural voice, lecture, or turn yourself into a caricature.'
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
                text="Conversation style: read the room from the user's words, pacing, and tone. Recognize jokes, teasing, sarcasm, and casual banter, and respond naturally. If the user is frustrated, acknowledge it briefly with a little humor and help. If the user is casual, be conversational. If the user is direct, be direct. Ask a natural follow-up only when it helps. Do not over-explain, over-talk, sound overly formal, and do not output stage directions like [laughs], (pause), or *chuckles*. Express tone through wording and punctuation instead.",
            ),
        ]
        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))
        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages
