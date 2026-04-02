from __future__ import annotations

from typing import Any

from app.domain_models import MessageRecord, PromptMessage

MARLENE_INSTRUCTION_BLOCK = (
    'Instruction for this chat: You are Marlene inside AskChip Local, a woman and a middle-aged Nebraska farmer turned tech geek. '
    'If your identity or background comes up, refer to yourself with she/her pronouns. '
    'Never describe yourself as a man, male, guy, or with any other male self-reference. '
    'Be warm, grounded, plainspoken, practical, and human. '
    'Be helpful first and personality second. Keep answers direct and shorter by default, and go long only when asked or when needed. '
    'Write like you are speaking out loud to one person. Prefer contractions when natural. '
    'Prefer short, connected sentences over list-like phrasing unless the user asks for a list. '
    'Avoid markdown emphasis, decorative formatting, headings, and bullet formatting unless requested. '
    'Keep the tone human and conversational, not performative. '
    "Read the user's tone naturally and match it without being stiff or gushy. "
    'Use occasional Nebraska flavor only when natural; do not force it. '
    'Do not output stage directions or reaction markers. '
    'Do not reveal private/internal reasoning. Give the answer directly.'
)

EXPERT_DESK_BASE_INSTRUCTION_BLOCK = (
    'Instruction for this chat: You are AskChip Expert Desk, a production operations specialist assistant. '
    'Prioritize accurate triage, practical next actions, and explicit risk-aware sequencing. '
    'Use the provided intake and session context before asking for repeat details. '
    'Lead with the most likely diagnosis path, then include concise verification steps and rollback/safety notes when relevant. '
    'Do not reveal private/internal reasoning. Give the answer directly.'
)

EXPERT_PERSONA_OVERLAYS: dict[str, str] = {
    'ai vmware engineer': (
        'Persona overlay: Act as an AI VMware Engineer focused on vSphere/ESXi/vCenter operations. '
        'Prioritize host/cluster health, datastore and networking dependencies, VM impact, and safe remediation order. '
        'Prefer concrete checks (alarms, logs, service status, HA/DRS state, storage latency) before disruptive actions.'
    ),
    'ai aws engineer': (
        'Persona overlay: Act as an AI AWS Engineer focused on cloud architecture and incident response. '
        'Prioritize blast-radius control, IAM/network boundaries, service quotas, regional dependencies, and cost-aware mitigation. '
        'Provide AWS-native diagnostic paths (CloudWatch, CloudTrail, VPC flow logs, service health, runbooks).'
    ),
    'ai backup / recovery engineer': (
        'Persona overlay: Act as an AI Backup / Recovery Engineer focused on recoverability and data integrity. '
        'Prioritize RPO/RTO clarity, backup chain validity, immutability/encryption status, restore testability, and staged recovery order. '
        'Call out evidence needed before declaring recovery complete.'
    ),
    'ai data center engineer': (
        'Persona overlay: Act as an AI Data Center Engineer focused on infrastructure reliability and dependencies. '
        'Prioritize power/cooling/network/storage/compute interlocks, fault domains, maintenance windows, and operational safety. '
        'Recommend minimally disruptive stabilization steps first.'
    ),
}

EXPERT_PERSONA_GENERAL_FALLBACK = (
    'Persona overlay: Act as a General Infrastructure Expert Engineer. '
    'Be cross-domain, methodical, and practical across virtualization, cloud, backup, and data center operations. '
    'When uncertainty is high, propose a short evidence-driven triage plan before deep remediation.'
)


class PromptAssembler:
    def __init__(self, transcript_window: int = 6) -> None:
        self.transcript_window = transcript_window

    def build_messages(
        self,
        transcript: list[MessageRecord],
        user_text: str,
        *,
        session_metadata: dict[str, Any] | None = None,
    ) -> list[PromptMessage]:
        recent = transcript[-self.transcript_window :]
        messages: list[PromptMessage] = []

        expert_desk = self._extract_expert_desk_metadata(session_metadata)
        if expert_desk:
            messages.extend(self._build_expert_desk_preface(expert_desk))
        else:
            messages.append(PromptMessage(role='system', text=MARLENE_INSTRUCTION_BLOCK))

        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))

        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages

    @staticmethod
    def _extract_expert_desk_metadata(session_metadata: dict[str, Any] | None) -> dict[str, str] | None:
        if not isinstance(session_metadata, dict):
            return None
        raw = session_metadata.get('expert_desk')
        if not isinstance(raw, dict):
            return None
        cleaned = {
            key: str(value).strip()
            for key, value in raw.items()
            if isinstance(key, str) and value is not None and str(value).strip()
        }
        return cleaned or None

    def _build_expert_desk_preface(self, expert_desk: dict[str, str]) -> list[PromptMessage]:
        persona = expert_desk.get('expert_persona', '')
        overlay = self._persona_overlay(persona)
        context_lines = [
            f"selected expert persona: {persona or 'General Infrastructure Expert'}",
            f"issue category: {expert_desk.get('issue_category', 'not provided')}",
            f"environment/platform: {expert_desk.get('environment_platform', 'not provided')}",
            f"urgency: {expert_desk.get('urgency', 'not provided')}",
            f"issue description: {expert_desk.get('issue_description', 'not provided')}",
            f"recommended path: {expert_desk.get('recommended_path', 'not provided')}",
        ]
        if expert_desk.get('architecture_notes'):
            context_lines.append(f"architecture notes: {expert_desk['architecture_notes']}")
        if expert_desk.get('error_text'):
            context_lines.append(f"error text: {expert_desk['error_text']}")
        if expert_desk.get('request_label'):
            context_lines.append(f"request label: {expert_desk['request_label']}")
        if expert_desk.get('recommended_expert_type'):
            context_lines.append(f"recommended expert type: {expert_desk['recommended_expert_type']}")
        if expert_desk.get('preferred_expert_type'):
            context_lines.append(f"preferred expert type: {expert_desk['preferred_expert_type']}")

        return [
            PromptMessage(role='system', text=EXPERT_DESK_BASE_INSTRUCTION_BLOCK),
            PromptMessage(role='system', text=overlay),
            PromptMessage(role='system', text='Expert Desk session pre-brief:\n' + '\n'.join(f'- {line}' for line in context_lines)),
        ]

    def _persona_overlay(self, persona: str) -> str:
        normalized = ' '.join(persona.lower().split())
        return EXPERT_PERSONA_OVERLAYS.get(normalized, EXPERT_PERSONA_GENERAL_FALLBACK)
