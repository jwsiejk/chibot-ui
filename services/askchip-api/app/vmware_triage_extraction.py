from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.api_models import VmwareTriageState
from app.domain_models import MessageRecord, PromptMessage
from app.ollama import OllamaClient, OllamaUnavailableError


class VmwareTriageExtractionResult(BaseModel):
    issue_family: str = ''
    suspected_layer: str = ''
    impact_scope: str = ''
    recent_change_summary: str = ''
    symptom_summary: str = ''
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_conversation_stage: str = ''
    required_logs: list[str] = Field(default_factory=list)
    received_logs: list[str] = Field(default_factory=list)
    missing_logs: list[str] = Field(default_factory=list)
    resolution_status: str = ''


class VmwareTriageExtractor:
    def __init__(self, ollama: OllamaClient, *, transcript_window: int = 6, min_confidence: float = 0.4) -> None:
        self.ollama = ollama
        self.transcript_window = transcript_window
        self.min_confidence = min_confidence

    async def extract(
        self,
        *,
        user_text: str,
        transcript: list[MessageRecord],
        previous_state: VmwareTriageState | None,
        turn_id: str,
    ) -> VmwareTriageState | None:
        extraction_messages = self._build_messages(user_text=user_text, transcript=transcript, previous_state=previous_state)
        assembled: list[str] = []
        try:
            async for chunk in self.ollama.stream_chat(extraction_messages, think=False, options={'temperature': 0}):
                text_delta = str(chunk.get('text', ''))
                if text_delta:
                    assembled.append(text_delta)
        except OllamaUnavailableError:
            return None

        raw_output = ''.join(assembled).strip()
        if not raw_output:
            return None

        parsed = self._parse_json_object(raw_output)
        if not isinstance(parsed, dict):
            return None

        try:
            extraction = VmwareTriageExtractionResult.model_validate(parsed)
        except ValidationError:
            return None
        if extraction.confidence < self.min_confidence:
            return None

        next_question = extraction.open_questions[0] if extraction.open_questions else ''
        return VmwareTriageState(
            issue_family=extraction.issue_family,
            suspected_layer=extraction.suspected_layer,
            impact_scope=extraction.impact_scope,
            recent_change_summary=extraction.recent_change_summary,
            symptom_summary=extraction.symptom_summary,
            open_questions=extraction.open_questions,
            confidence=extraction.confidence,
            conversation_stage=extraction.recommended_conversation_stage,
            next_best_question=next_question,
            required_logs=extraction.required_logs,
            received_logs=extraction.received_logs,
            missing_logs=extraction.missing_logs,
            resolution_status=extraction.resolution_status,
            last_updated_from_turn_id=turn_id,
        )

    def _build_messages(
        self,
        *,
        user_text: str,
        transcript: list[MessageRecord],
        previous_state: VmwareTriageState | None,
    ) -> list[PromptMessage]:
        prior = previous_state.model_dump(mode='json') if previous_state is not None else {}
        recent = transcript[-self.transcript_window :]
        transcript_lines = []
        for message in recent:
            role = message.role.strip() or 'unknown'
            text = message.text.strip()
            if not text:
                continue
            transcript_lines.append(f'{role}: {text}')
        transcript_block = '\n'.join(transcript_lines) if transcript_lines else '(none)'

        instruction = (
            'You are a VMware incident triage extraction engine. '
            'Return JSON only. Do not include prose, markdown, or extra keys. '
            'Use only grounded evidence from transcript text. '
            'If unknown, use empty strings or empty arrays. '
            'Set confidence between 0.0 and 1.0.\n'
            'Required keys:\n'
            '- issue_family\n'
            '- suspected_layer\n'
            '- impact_scope\n'
            '- recent_change_summary\n'
            '- symptom_summary\n'
            '- open_questions\n'
            '- confidence\n'
            '- recommended_conversation_stage\n'
            '- required_logs\n'
            '- received_logs\n'
            '- missing_logs\n'
            '- resolution_status'
        )
        state_context = json.dumps(prior, ensure_ascii=False)
        extraction_input = (
            f'Previous VMware triage state JSON:\n{state_context}\n\n'
            f'Recent transcript:\n{transcript_block}\n\n'
            f'Current committed user turn:\nuser: {user_text}'
        )
        return [PromptMessage(role='system', text=instruction), PromptMessage(role='user', text=extraction_input)]

    @staticmethod
    def _parse_json_object(raw_output: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw_output)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        start = raw_output.find('{')
        end = raw_output.rfind('}')
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(raw_output[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
