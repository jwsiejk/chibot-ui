from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.api_models import ExpertDeskSessionMetadata, VmwareTriageState
from app.vmware_conversation_policy import VMWARE_NEXT_MOVES, decide_vmware_next_move, stage_for_vmware_next_move
from app.vmware_log_sufficiency import evaluate_vmware_log_sufficiency


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


def read_expert_desk_metadata(session_metadata: dict[str, Any] | None) -> ExpertDeskSessionMetadata | None:
    if not isinstance(session_metadata, dict):
        return None
    raw = session_metadata.get('expert_desk')
    if not isinstance(raw, dict):
        return None
    raw_expert_desk = dict(raw)
    raw_vmware_triage = raw_expert_desk.pop('vmware_triage', None)
    try:
        expert_desk = ExpertDeskSessionMetadata.model_validate({**raw_expert_desk, 'vmware_triage': None})
    except ValidationError:
        return None
    if raw_vmware_triage is None:
        return expert_desk
    try:
        expert_desk.vmware_triage = safe_partial_vmware_triage(raw_vmware_triage)
    except ValidationError:
        expert_desk.vmware_triage = None
    return expert_desk


def read_vmware_triage_state(session_metadata: dict[str, Any] | None) -> VmwareTriageState | None:
    expert_desk = read_expert_desk_metadata(session_metadata)
    if expert_desk is None:
        return None
    return expert_desk.vmware_triage


def update_vmware_triage_state(
    session_metadata: dict[str, Any] | None,
    triage_state: VmwareTriageState,
) -> dict[str, Any]:
    base = dict(session_metadata) if isinstance(session_metadata, dict) else {}
    expert_desk = read_expert_desk_metadata(base)
    if expert_desk is None:
        return base
    expert_desk.vmware_triage = triage_state
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
    expert_desk.vmware_triage = triage_state
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
    expert_desk.vmware_triage = triage_state
    base['expert_desk'] = expert_desk.model_dump(mode='json')
    return base


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
