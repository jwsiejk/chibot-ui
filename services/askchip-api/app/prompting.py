from __future__ import annotations

from typing import Any

from app.api_models import VmwareTriageState
from app.domain_models import MessageRecord, PromptMessage
from app.expert_desk_metadata import build_prompt_context, read_expert_desk_metadata
from app.vmware_conversation_policy import decide_vmware_next_move

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
    'Start conversationally, then move into practical troubleshooting as evidence becomes clear. '
    'Use likely issue paths and concise verification/safety notes only when they are grounded by available evidence. '
    'Do not reveal private/internal reasoning. Give the answer directly.'
)

EXPERT_PERSONA_OVERLAYS_BY_ID: dict[str, str] = {
    'ai-vmware-engineer': (
        'Persona overlay: Act as an AI VMware Engineer focused on vSphere/ESXi/vCenter operations. '
        'Prioritize host/cluster health, datastore and networking dependencies, VM impact, and safe remediation order. '
        'Prefer concrete checks (alarms, logs, service status, HA/DRS state, storage latency) before disruptive actions.'
    ),
    'ai-aws-engineer': (
        'Persona overlay: Act as an AI AWS Engineer focused on cloud architecture and incident response. '
        'Prioritize blast-radius control, IAM/network boundaries, service quotas, regional dependencies, and cost-aware mitigation. '
        'Provide AWS-native diagnostic paths (CloudWatch, CloudTrail, VPC flow logs, service health, runbooks).'
    ),
    'ai-backup-recovery-engineer': (
        'Persona overlay: Act as an AI Backup / Recovery Engineer focused on recoverability and data integrity. '
        'Prioritize RPO/RTO clarity, backup chain validity, immutability/encryption status, restore testability, and staged recovery order. '
        'Call out evidence needed before declaring recovery complete.'
    ),
    'ai-data-center-engineer': (
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

        expert_desk = read_expert_desk_metadata(session_metadata)
        if expert_desk is not None:
            messages.extend(
                self._build_expert_desk_preface(
                    build_prompt_context(expert_desk),
                    transcript,
                    triage_state=expert_desk.vmware_triage,
                )
            )
        else:
            messages.append(PromptMessage(role='system', text=MARLENE_INSTRUCTION_BLOCK))

        for item in recent:
            if item.role == 'assistant' and not item.text:
                continue
            messages.append(PromptMessage(role=item.role, text=item.text))

        if not recent or recent[-1].role != 'user' or recent[-1].text != user_text:
            messages.append(PromptMessage(role='user', text=user_text))
        return messages

    def _build_expert_desk_preface(
        self,
        expert_desk: dict[str, str],
        transcript: list[MessageRecord],
        *,
        triage_state: VmwareTriageState | None = None,
    ) -> list[PromptMessage]:
        persona_id = expert_desk.get('expert_persona_id', '')
        persona_label = expert_desk.get('expert_persona_label', '') or expert_desk.get('expert_persona', '')
        overlay = self._persona_overlay(persona_id=persona_id, persona_label=persona_label)
        is_vmware_persona = persona_id == 'ai-vmware-engineer' or persona_label.lower() == 'ai vmware engineer'
        has_prior_assistant_turn = any(item.role == 'assistant' and item.text.strip() for item in transcript)
        try:
            uploaded_logs_count = int(expert_desk.get('uploaded_logs_count', '0') or '0')
        except ValueError:
            uploaded_logs_count = 0
        uploaded_logs_available = expert_desk.get('uploaded_logs_available', '').lower() in {'true', '1', 'yes'}
        uploaded_log_names = expert_desk.get('uploaded_log_names', '').strip()
        context_lines = [
            f"selected expert persona id: {persona_id or 'general-infrastructure-expert'}",
            f"selected expert persona label: {persona_label or 'General Infrastructure Expert'}",
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
        if uploaded_log_names:
            context_lines.append(f"uploaded log names: {uploaded_log_names}")
        if expert_desk.get('vmware_triage_issue_family'):
            context_lines.append(f"vmware triage issue family: {expert_desk['vmware_triage_issue_family']}")
        if expert_desk.get('vmware_triage_suspected_layer'):
            context_lines.append(f"vmware triage suspected layer: {expert_desk['vmware_triage_suspected_layer']}")
        if expert_desk.get('vmware_triage_impact_scope'):
            context_lines.append(f"vmware triage impact scope: {expert_desk['vmware_triage_impact_scope']}")
        if expert_desk.get('vmware_triage_symptom_summary'):
            context_lines.append(f"vmware triage symptom summary: {expert_desk['vmware_triage_symptom_summary']}")
        if expert_desk.get('vmware_triage_open_questions'):
            context_lines.append(f"vmware triage open questions: {expert_desk['vmware_triage_open_questions']}")
        if expert_desk.get('vmware_triage_conversation_stage'):
            context_lines.append(f"vmware triage conversation stage: {expert_desk['vmware_triage_conversation_stage']}")
        if expert_desk.get('vmware_triage_missing_logs'):
            context_lines.append(f"vmware triage missing logs: {expert_desk['vmware_triage_missing_logs']}")
        if expert_desk.get('vmware_triage_log_sufficiency_status'):
            context_lines.append(f"vmware triage log sufficiency status: {expert_desk['vmware_triage_log_sufficiency_status']}")
        if expert_desk.get('vmware_triage_optional_logs'):
            context_lines.append(f"vmware triage optional logs: {expert_desk['vmware_triage_optional_logs']}")
        if expert_desk.get('vmware_triage_log_guidance_summary'):
            context_lines.append(f"vmware triage log guidance summary: {expert_desk['vmware_triage_log_guidance_summary']}")

        vmware_runtime_guidance = None
        if is_vmware_persona:
            latest_user_feedback = ''
            for item in reversed(transcript):
                if item.role == 'user' and item.text.strip():
                    latest_user_feedback = item.text
                    break
            policy_decision = decide_vmware_next_move(
                triage_state=triage_state,
                latest_user_feedback=latest_user_feedback,
                has_prior_assistant_turn=has_prior_assistant_turn,
            )
            vmware_runtime_guidance_lines = [
                'VMware live-session guidance:',
                '- Keep tone calm, direct, conversational, and professional.',
                '- Keep responses short by default: usually 2-4 sentences unless the user asks for more.',
                '- Ask one focused next question at a time to move triage forward.',
                '- Lead with a working hypothesis when appropriate; do not present unverified causes as facts.',
                f"- Deterministic policy next move: {policy_decision.next_move}.",
                f"- Deterministic policy confidence band: {policy_decision.confidence_band}.",
                f"- Deterministic policy user feedback signal: {policy_decision.user_feedback_signal}.",
                f"- Deterministic policy log sufficiency status: {policy_decision.log_sufficiency_status}.",
                f'- Deterministic policy working hypothesis: {policy_decision.working_hypothesis}',
                f'- Deterministic policy focused next question: {policy_decision.focused_question}',
                '- For follow-up turns, give one or two likely issue paths and one or two short verification steps when grounded by evidence.',
                '- Keep troubleshooting practical and ordered; avoid long lectures or policy-like disclaimers.',
                '- Avoid broad speculation or generic outage declarations without concrete evidence from user context.',
                '- Be honest about logs: you only have uploaded file metadata unless parsed content is explicitly provided.',
                '- Never claim parsed-log findings unless parsed content is explicitly present in context.',
                "- If only metadata is available, say logs were received but not parsed yet, and state what you would check next.",
                "- If vmware triage log sufficiency status is sufficient, you may say you have enough logs for this issue path and continue step-by-step.",
                "- If vmware triage log sufficiency status is partial, say you can proceed with current evidence but explicitly list missing logs.",
                "- If vmware triage log sufficiency status is insufficient or unknown_issue_family, say the right logs are not complete for this path and list what to upload next.",
                f"- Uploaded logs available: {'yes' if (uploaded_logs_available or uploaded_logs_count > 0) else 'no'}.",
                f'- Uploaded logs count: {uploaded_logs_count}.',
            ]
            if uploaded_log_names:
                vmware_runtime_guidance_lines.append(f'- Uploaded log file names: {uploaded_log_names}.')
            if not has_prior_assistant_turn:
                vmware_runtime_guidance_lines.extend([
                    '- This is your first VMware response in the live session.',
                    '- The first response is a conversational opener, not an assessment dump.',
                    '- Keep the first response to 2-3 short sentences by default.',
                    '- Start by acknowledging the issue professionally.',
                    '- For first response flow: state a working hypothesis, ask for confirmation, then ask one focused next question.',
                    '- Do not use headings (for example: initial assessment, likely diagnosis path, immediate next actions).',
                    '- Do not use numbered checklists unless the user explicitly asks for one.',
                    '- Do not declare a likely root cause unless the user already provided enough concrete evidence.',
                    "- Explicitly state whether VMware logs were received.",
                    "- If logs were received, acknowledge receipt, say you can review them, and stay honest that logs are metadata-only unless parsed content is present.",
                    "- If logs were not received, briefly say that, recommend uploading: vCenter logs, ESXi host/support bundle, vmkernel.log, and vpxd.log, and point to the live-session upload control.",
                    '- End with one focused next question.',
                    '- Keep the kickoff brief, natural, and engineer-to-engineer; avoid playbook-style formatting.',
                ])
            else:
                vmware_runtime_guidance_lines.extend([
                    '- If logs were just uploaded during this live session, acknowledge they were received and offer to review them now.',
                    '- If the user corrects your path, explicitly revise your working hypothesis before proposing the next step.',
                    '- Do not fabricate findings from logs that were not parsed.',
                ])
            vmware_runtime_guidance = '\n'.join(vmware_runtime_guidance_lines)

        preface_messages = [
            PromptMessage(role='system', text=EXPERT_DESK_BASE_INSTRUCTION_BLOCK),
            PromptMessage(role='system', text=overlay),
            PromptMessage(role='system', text='Expert Desk session pre-brief:\n' + '\n'.join(f'- {line}' for line in context_lines)),
        ]
        if vmware_runtime_guidance:
            preface_messages.append(PromptMessage(role='system', text=vmware_runtime_guidance))
        return preface_messages

    def _persona_overlay(self, *, persona_id: str, persona_label: str) -> str:
        normalized_id = '-'.join(persona_id.lower().split())
        if normalized_id in EXPERT_PERSONA_OVERLAYS_BY_ID:
            return EXPERT_PERSONA_OVERLAYS_BY_ID[normalized_id]

        normalized_label = ' '.join(persona_label.lower().split())
        legacy_label_to_id = {
            'ai vmware engineer': 'ai-vmware-engineer',
            'ai aws engineer': 'ai-aws-engineer',
            'ai backup / recovery engineer': 'ai-backup-recovery-engineer',
            'ai data center engineer': 'ai-data-center-engineer',
        }
        legacy_id = legacy_label_to_id.get(normalized_label)
        if legacy_id:
            return EXPERT_PERSONA_OVERLAYS_BY_ID[legacy_id]

        return EXPERT_PERSONA_GENERAL_FALLBACK
