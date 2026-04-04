from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.api_models import ExpertDeskSessionMetadata, VmwareHandoffPacket, VmwareTriageState
from app.vmware_conversation_policy import VMWARE_NEXT_MOVES, decide_vmware_next_move, stage_for_vmware_next_move
from app.vmware_log_sufficiency import evaluate_vmware_log_sufficiency

VMWARE_RESOLUTION_STATUSES = {
    'unresolved',
    'monitoring',
    'resolved',
    'blocked_waiting_on_logs',
    'blocked_waiting_on_user_action',
    'needs_human_handoff',
}


def safe_partial_vmware_triage(raw_vmware_triage: Any) -> VmwareTriageState | None:
    if isinstance(raw_vmware_triage, VmwareTriageState):
        return raw_vmware_triage
    if not isinstance(raw_vmware_triage, dict):
        return None
    cleaned: dict[str, Any] = {}
    for field_name, raw_value in raw_vmware_triage.items():
        if field_name not in VmwareTriageState.model_fields:
            continue
        try:
            parsed = VmwareTriageState.model_validate({field_name: raw_value})
        except ValidationError:
            continue
        cleaned[field_name] = getattr(parsed, field_name)
    if not cleaned:
        return None
    try:
        return VmwareTriageState.model_validate(cleaned)
    except ValidationError:
        return None


def safe_partial_vmware_handoff(raw_vmware_handoff: Any) -> VmwareHandoffPacket | None:
    if isinstance(raw_vmware_handoff, VmwareHandoffPacket):
        return raw_vmware_handoff
    if not isinstance(raw_vmware_handoff, dict):
        return None
    cleaned: dict[str, Any] = {}
    for field_name, raw_value in raw_vmware_handoff.items():
        if field_name not in VmwareHandoffPacket.model_fields:
            continue
        try:
            parsed = VmwareHandoffPacket.model_validate({field_name: raw_value})
        except ValidationError:
            continue
        cleaned[field_name] = getattr(parsed, field_name)
    if not cleaned:
        return None
    try:
        return VmwareHandoffPacket.model_validate(cleaned)
    except ValidationError:
        return None


def read_expert_desk_metadata(session_metadata: dict[str, Any] | None) -> ExpertDeskSessionMetadata | None:
    if not isinstance(session_metadata, dict):
        return None
    raw = session_metadata.get('expert_desk')
    if not isinstance(raw, dict):
        return None
    raw_expert_desk = dict(raw)
    raw_vmware_triage = raw_expert_desk.pop('vmware_triage', None)
    raw_vmware_handoff = raw_expert_desk.pop('vmware_handoff', None)
    try:
        expert_desk = ExpertDeskSessionMetadata.model_validate(
            {**raw_expert_desk, 'vmware_triage': None, 'vmware_handoff': None}
        )
    except ValidationError:
        return None
    if raw_vmware_triage is not None:
        try:
            expert_desk.vmware_triage = safe_partial_vmware_triage(raw_vmware_triage)
        except ValidationError:
            expert_desk.vmware_triage = None
    if raw_vmware_handoff is not None:
        try:
            expert_desk.vmware_handoff = safe_partial_vmware_handoff(raw_vmware_handoff)
        except ValidationError:
            expert_desk.vmware_handoff = None
    return expert_desk


def read_vmware_triage_state(session_metadata: dict[str, Any] | None) -> VmwareTriageState | None:
    expert_desk = read_expert_desk_metadata(session_metadata)
    if expert_desk is None:
        return None
    return expert_desk.vmware_triage


def update_vmware_triage_state(
    session_metadata: dict[str, Any] | None,
    triage_state: VmwareTriageState,
    *,
    transcript_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base = dict(session_metadata) if isinstance(session_metadata, dict) else {}
    expert_desk = read_expert_desk_metadata(base)
    if expert_desk is None:
        return base
    triage_state.resolution_status = normalize_vmware_resolution_status(
        triage_state.resolution_status,
        log_sufficiency_status=triage_state.log_sufficiency_status,
    )
    expert_desk.vmware_triage = triage_state
    expert_desk.vmware_handoff = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=transcript_messages)
    base['expert_desk'] = expert_desk.model_dump(mode='json')
    return base


def refresh_vmware_triage_log_sufficiency(session_metadata: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(session_metadata) if isinstance(session_metadata, dict) else {}
    expert_desk = read_expert_desk_metadata(base)
    if expert_desk is None:
        return base
    is_vmware_persona = expert_desk.expert_persona_id == 'ai-vmware-engineer' or expert_desk.expert_persona_label.strip().lower() == 'ai vmware engineer'
    if not is_vmware_persona:
        return base
    triage_state = expert_desk.vmware_triage
    if triage_state is None:
        return base
    issue_family = triage_state.issue_family.strip()
    if not issue_family:
        return base

    uploaded_log_names = [name.strip() for name in expert_desk.uploaded_log_names if name.strip()]
    sufficiency = evaluate_vmware_log_sufficiency(issue_family, uploaded_log_names)
    triage_state.required_logs = sufficiency.required_logs
    triage_state.received_logs = sufficiency.received_logs
    triage_state.missing_logs = sufficiency.missing_logs
    triage_state.optional_logs = sufficiency.optional_logs
    triage_state.log_sufficiency_status = sufficiency.log_sufficiency_status
    triage_state.log_guidance_summary = sufficiency.log_guidance_summary
    triage_state.resolution_status = normalize_vmware_resolution_status(
        triage_state.resolution_status,
        log_sufficiency_status=triage_state.log_sufficiency_status,
    )
    expert_desk.vmware_triage = triage_state
    expert_desk.vmware_handoff = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=None)
    base['expert_desk'] = expert_desk.model_dump(mode='json')
    return base


def refresh_vmware_triage_policy_state(session_metadata: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(session_metadata) if isinstance(session_metadata, dict) else {}
    expert_desk = read_expert_desk_metadata(base)
    if expert_desk is None:
        return base
    is_vmware_persona = expert_desk.expert_persona_id == 'ai-vmware-engineer' or expert_desk.expert_persona_label.strip().lower() == 'ai vmware engineer'
    if not is_vmware_persona:
        return base
    triage_state = expert_desk.vmware_triage
    if triage_state is None:
        return base
    issue_family = triage_state.issue_family.strip()
    if not issue_family:
        return base

    has_prior_assistant_turn = bool(triage_state.last_updated_from_turn_id.strip())
    policy_decision = decide_vmware_next_move(
        triage_state=triage_state,
        latest_user_feedback='',
        has_prior_assistant_turn=has_prior_assistant_turn,
    )
    next_move = _non_regressive_patch_policy_next_move(
        triage_state=triage_state,
        decided_next_move=policy_decision.next_move,
    )
    triage_state.policy_next_move = next_move
    if next_move != policy_decision.next_move:
        triage_state.next_best_question = _fallback_question_for_non_regressive_move(next_move, triage_state)
    else:
        triage_state.next_best_question = policy_decision.focused_question

    current_stage = triage_state.conversation_stage.strip()
    decided_stage = stage_for_vmware_next_move(next_move)
    if (
        policy_decision.next_move == 'confirm_issue_family'
        and next_move != 'confirm_issue_family'
        and current_stage
        and current_stage.lower() not in {'issue_definition'}
    ):
        triage_state.conversation_stage = current_stage
    else:
        triage_state.conversation_stage = decided_stage
    triage_state.resolution_status = normalize_vmware_resolution_status(
        triage_state.resolution_status,
        log_sufficiency_status=triage_state.log_sufficiency_status,
    )
    expert_desk.vmware_triage = triage_state
    expert_desk.vmware_handoff = build_vmware_handoff_packet(expert_desk=expert_desk, transcript_messages=None)
    base['expert_desk'] = expert_desk.model_dump(mode='json')
    return base


def normalize_vmware_resolution_status(raw_status: str, *, log_sufficiency_status: str = '') -> str:
    normalized = raw_status.strip().lower()
    alias_map = {
        '': 'unresolved',
        'in_progress': 'unresolved',
        'triage': 'unresolved',
        'triaging': 'unresolved',
        'stable': 'monitoring',
        'observing': 'monitoring',
        'fixed': 'resolved',
        'waiting_on_logs': 'blocked_waiting_on_logs',
        'blocked_logs': 'blocked_waiting_on_logs',
        'waiting_on_user': 'blocked_waiting_on_user_action',
        'waiting_on_customer': 'blocked_waiting_on_user_action',
        'needs_handoff': 'needs_human_handoff',
        'handoff_required': 'needs_human_handoff',
    }
    candidate = alias_map.get(normalized, normalized)
    if candidate in VMWARE_RESOLUTION_STATUSES:
        return candidate
    if log_sufficiency_status.strip().lower() == 'insufficient':
        return 'blocked_waiting_on_logs'
    return 'unresolved'


def build_vmware_handoff_packet(
    *,
    expert_desk: ExpertDeskSessionMetadata,
    transcript_messages: list[dict[str, Any]] | None,
) -> VmwareHandoffPacket | None:
    triage = expert_desk.vmware_triage
    if triage is None:
        return None

    issue_family = triage.issue_family.strip() or 'unconfirmed issue family'
    resolution_status = normalize_vmware_resolution_status(
        triage.resolution_status,
        log_sufficiency_status=triage.log_sufficiency_status,
    )
    logs_received = [item.strip() for item in triage.received_logs if item.strip()]
    if not logs_received:
        logs_received = [item.strip() for item in expert_desk.uploaded_log_names if item.strip()]
    logs_missing = [item.strip() for item in triage.missing_logs if item.strip()]

    confirmed_facts = [f'Issue family: {issue_family}']
    if triage.suspected_layer.strip():
        confirmed_facts.append(f'Suspected layer: {triage.suspected_layer.strip()}')
    if triage.impact_scope.strip():
        confirmed_facts.append(f'Impact scope: {triage.impact_scope.strip()}')
    if triage.recent_change_summary.strip():
        confirmed_facts.append(f'Recent change summary: {triage.recent_change_summary.strip()}')
    if triage.symptom_summary.strip():
        confirmed_facts.append(f'Symptom summary: {triage.symptom_summary.strip()}')
    confirmed_facts.append(
        'Log evidence reflects uploaded file metadata names only; parsed-log conclusions are not persisted in this phase.'
    )

    user_count = 0
    assistant_count = 0
    if transcript_messages:
        for message in transcript_messages:
            role = str(message.get('role', '')).strip().lower()
            text = str(message.get('text', '')).strip()
            if not text:
                continue
            if role == 'user':
                user_count += 1
            if role == 'assistant':
                assistant_count += 1

    actions_taken: list[str] = []
    if user_count or assistant_count:
        actions_taken.append(f'Transcript captured {user_count} user message(s) and {assistant_count} assistant message(s).')
    if triage.policy_next_move.strip():
        actions_taken.append(f'Deterministic policy next move: {triage.policy_next_move.strip()}.')
    if triage.next_best_question.strip():
        actions_taken.append(f'Focused next question: {triage.next_best_question.strip()}')
    if not actions_taken:
        actions_taken.append('No transcript-derived action summary is available yet.')

    handoff_reason = ''
    if resolution_status == 'needs_human_handoff':
        handoff_reason = 'Current troubleshooting path requires human expert escalation.'
    elif resolution_status == 'blocked_waiting_on_logs':
        handoff_reason = 'Troubleshooting is blocked waiting on required logs.'
    elif resolution_status == 'blocked_waiting_on_user_action':
        handoff_reason = 'Troubleshooting is blocked waiting on required user action.'

    issue_summary = triage.symptom_summary.strip() or expert_desk.issue_description.strip() or expert_desk.request_label.strip()
    working_hypothesis = triage.suspected_layer.strip() or f'Current working path remains {issue_family}.'
    recommended_next_step = triage.next_best_question.strip() or _human_readable_next_step_for_policy_move(
        triage.policy_next_move,
        triage,
    )
    return VmwareHandoffPacket(
        issue_summary=issue_summary,
        working_hypothesis=working_hypothesis,
        confirmed_facts=confirmed_facts,
        open_questions=[item.strip() for item in triage.open_questions if item.strip()],
        actions_taken=actions_taken,
        logs_received=logs_received,
        logs_missing=logs_missing,
        log_sufficiency_status=triage.log_sufficiency_status.strip() or 'unknown',
        current_resolution_status=resolution_status,
        recommended_next_step=recommended_next_step,
        handoff_reason=handoff_reason,
        ready_for_handoff=resolution_status == 'needs_human_handoff',
    )



def _human_readable_next_step_for_policy_move(policy_next_move: str, triage_state: VmwareTriageState) -> str:
    move = policy_next_move.strip().lower()
    if move == 'confirm_issue_family':
        return 'Confirm which VMware issue family best matches the current symptoms.'
    if move == 'confirm_scope':
        return 'Confirm whether impact is isolated or spread across multiple hosts or clusters.'
    if move == 'collect_recent_change':
        return 'Capture the most recent environment change before symptoms started.'
    if move == 'request_missing_logs':
        missing = [item.strip() for item in triage_state.missing_logs if item.strip()]
        if missing:
            return f"Request the missing logs next: {', '.join(missing)}."
        return 'Request the next relevant VMware logs to strengthen evidence.'
    if move == 'validate_hypothesis':
        return 'Validate whether the current VMware hypothesis still fits the latest evidence.'
    if move == 'propose_safe_next_step':
        return 'Run one safe verification step and capture the outcome.'
    if move == 'verify_result':
        return 'Verify whether the latest action improved the observed impact.'
    if move == 'summarize_progress':
        return 'Summarize confirmed findings, open questions, and next actions.'
    if move == 'handoff_required':
        return 'Prepare a clean human handoff summary with current evidence and blockers.'
    if move == 'resolution_confirmed':
        return 'Confirm the issue is resolved and stable before closing this path.'
    return 'Continue evidence-driven VMware triage with the next focused validation step.'


def _non_regressive_patch_policy_next_move(*, triage_state: VmwareTriageState, decided_next_move: str) -> str:
    if decided_next_move != 'confirm_issue_family':
        return decided_next_move
    if not triage_state.issue_family.strip():
        return decided_next_move

    has_scope = bool(triage_state.impact_scope.strip())
    has_recent_change = bool(triage_state.recent_change_summary.strip())
    log_status = triage_state.log_sufficiency_status.strip().lower()
    has_sufficient_logs = log_status in {'sufficient', 'partial'}
    if not (has_scope and has_recent_change and has_sufficient_logs):
        return decided_next_move

    existing_next_move = triage_state.policy_next_move.strip().lower()
    if existing_next_move in VMWARE_NEXT_MOVES and existing_next_move != 'confirm_issue_family':
        return existing_next_move

    stage = triage_state.conversation_stage.strip().lower()
    if stage in {'mitigation', 'remediation'}:
        return 'propose_safe_next_step'
    if stage in {'verification', 'verify_result'}:
        return 'verify_result'
    if stage in {'summary', 'summarize_progress'}:
        return 'summarize_progress'
    return 'validate_hypothesis'


def _fallback_question_for_non_regressive_move(move: str, triage_state: VmwareTriageState) -> str:
    if move == 'propose_safe_next_step':
        return 'Can we run one safe verification step now and confirm the result before changing anything disruptive?'
    if move == 'verify_result':
        return 'After that step, did alarms, host state, and workload impact improve or stay the same?'
    if move == 'summarize_progress':
        return 'Would you like a short summary of confirmed findings, open risks, and next actions?'
    if move == 'validate_hypothesis':
        return 'Based on your latest details, should we revise the issue family before we continue?'
    missing = [item.strip() for item in triage_state.missing_logs if item.strip()]
    if missing:
        return f"Can you upload {', '.join(missing)} next so we can validate this path?"
    return 'Can you confirm the latest symptom timeline so we can continue triage?'


def build_prompt_context(expert_desk: ExpertDeskSessionMetadata) -> dict[str, str]:
    raw = expert_desk.model_dump(mode='json')
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if key == 'vmware_handoff':
            continue
        if value is None:
            continue
        if isinstance(value, list):
            flattened = ', '.join(str(item).strip() for item in value if str(item).strip())
            if flattened:
                cleaned[key] = flattened
            continue
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if nested_value is None:
                    continue
                if isinstance(nested_value, list):
                    flattened = ', '.join(str(item).strip() for item in nested_value if str(item).strip())
                    if flattened:
                        cleaned[f'{key}_{nested_key}'] = flattened
                    continue
                normalized = str(nested_value).strip()
                if normalized:
                    cleaned[f'{key}_{nested_key}'] = normalized
            continue
        normalized = str(value).strip()
        if normalized:
            cleaned[key] = normalized
    return cleaned
