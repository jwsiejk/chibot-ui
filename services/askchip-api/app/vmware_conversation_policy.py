from __future__ import annotations

from dataclasses import dataclass

from app.api_models import VmwareTriageState

VMWARE_NEXT_MOVES = {
    'confirm_issue_family',
    'confirm_scope',
    'collect_recent_change',
    'request_missing_logs',
    'validate_hypothesis',
    'propose_safe_next_step',
    'verify_result',
    'summarize_progress',
    'handoff_required',
    'resolution_confirmed',
}

VMWARE_STAGE_BY_NEXT_MOVE = {
    'confirm_issue_family': 'issue_definition',
    'confirm_scope': 'scope_confirmation',
    'collect_recent_change': 'change_validation',
    'request_missing_logs': 'log_collection',
    'validate_hypothesis': 'hypothesis_validation',
    'propose_safe_next_step': 'mitigation',
    'verify_result': 'verification',
    'summarize_progress': 'summary',
    'handoff_required': 'handoff',
    'resolution_confirmed': 'resolution',
}


@dataclass(frozen=True)
class VmwareConversationPolicyDecision:
    next_move: str
    working_hypothesis: str
    focused_question: str
    confidence_band: str
    user_feedback_signal: str
    log_sufficiency_status: str


def decide_vmware_next_move(
    *,
    triage_state: VmwareTriageState | None,
    latest_user_feedback: str,
    has_prior_assistant_turn: bool,
) -> VmwareConversationPolicyDecision:
    state = triage_state or VmwareTriageState()
    feedback_signal = _classify_feedback_signal(latest_user_feedback)
    confidence_band = _confidence_band(state.confidence)
    issue_family = state.issue_family.strip().lower()
    stage = state.conversation_stage.strip().lower()
    log_status = state.log_sufficiency_status.strip().lower() or 'unknown'
    resolution_status = state.resolution_status.strip().lower()

    working_hypothesis = _build_working_hypothesis(state)
    focused_question = _default_question_for_move('confirm_issue_family', state)
    next_move = 'confirm_issue_family'

    if resolution_status in {'resolved'}:
        next_move = 'resolution_confirmed'
    elif resolution_status in {'needs_human_handoff'}:
        next_move = 'handoff_required'
    elif resolution_status in {'blocked_waiting_on_logs'}:
        next_move = 'request_missing_logs'
    elif resolution_status in {'blocked_waiting_on_user_action'}:
        next_move = 'propose_safe_next_step'
    elif feedback_signal == 'correction':
        next_move = 'validate_hypothesis'
    elif not issue_family:
        next_move = 'confirm_issue_family'
    elif log_status in {'insufficient', 'unknown_issue_family'}:
        next_move = 'request_missing_logs'
    elif not state.impact_scope.strip():
        next_move = 'confirm_scope'
    elif not state.recent_change_summary.strip():
        next_move = 'collect_recent_change'
    elif confidence_band == 'low':
        next_move = 'validate_hypothesis'
    elif stage in {'mitigation', 'remediation'}:
        next_move = 'propose_safe_next_step'
    elif stage in {'verification', 'verify_result'}:
        next_move = 'verify_result'
    elif stage in {'summary', 'summarize_progress'}:
        next_move = 'summarize_progress'
    elif feedback_signal == 'confirmed' and has_prior_assistant_turn:
        next_move = 'propose_safe_next_step'

    if next_move not in VMWARE_NEXT_MOVES:
        next_move = 'validate_hypothesis'
    focused_question = _default_question_for_move(next_move, state)

    return VmwareConversationPolicyDecision(
        next_move=next_move,
        working_hypothesis=working_hypothesis,
        focused_question=focused_question,
        confidence_band=confidence_band,
        user_feedback_signal=feedback_signal,
        log_sufficiency_status=log_status,
    )


def stage_for_vmware_next_move(next_move: str) -> str:
    normalized = next_move.strip().lower()
    return VMWARE_STAGE_BY_NEXT_MOVE.get(normalized, 'hypothesis_validation')


def _build_working_hypothesis(state: VmwareTriageState) -> str:
    issue_family = state.issue_family.strip().lower()
    if issue_family == 'host-networking':
        return 'Working hypothesis: this may be host uplink or vDS path instability, not yet confirmed.'
    if issue_family == 'storage-pathing':
        return 'Working hypothesis: this may be storage path failover or APD/PDL timing instability, not yet confirmed.'
    if issue_family == 'vcenter-services':
        return 'Working hypothesis: this may be a vCenter control-plane service health issue, not yet confirmed.'
    if issue_family == 'vm-performance':
        return 'Working hypothesis: this may be host contention or scheduling pressure, not yet confirmed.'
    return 'Working hypothesis: this appears to be a VMware infrastructure issue path, but we should confirm the family first.'


def _default_question_for_move(move: str, state: VmwareTriageState) -> str:
    if move == 'confirm_issue_family':
        return 'Does this align most with host networking, storage pathing, vCenter services, or VM performance impact?'
    if move == 'confirm_scope':
        return 'Is the impact isolated to one host/cluster, or are multiple clusters affected right now?'
    if move == 'collect_recent_change':
        return 'What changed in the environment within the hour before symptoms started?'
    if move == 'request_missing_logs':
        missing = [item.strip() for item in state.missing_logs if item.strip()]
        if missing:
            return f"Can you upload {', '.join(missing)} next so we can validate this path?"
        return 'Which VMware logs can you upload next so we can confirm this path?'
    if move == 'validate_hypothesis':
        return 'Based on your latest details, should we revise the issue family before we continue?'
    if move == 'propose_safe_next_step':
        return 'Can we run one safe verification step now and confirm the result before changing anything disruptive?'
    if move == 'verify_result':
        return 'After that step, did alarms, host state, and workload impact improve or stay the same?'
    if move == 'summarize_progress':
        return 'Would you like a short summary of confirmed findings, open risks, and next actions?'
    if move == 'handoff_required':
        return 'Do you want me to prepare a clean handoff summary for human escalation with current evidence?'
    if move == 'resolution_confirmed':
        return 'Can you confirm production impact is resolved and stable so we can close this path?'
    return 'Can you confirm the latest symptom timeline so we can continue triage?'


def _classify_feedback_signal(text: str) -> str:
    normalized = text.strip().lower()
    if not normalized:
        return 'none'
    correction_markers = (
        'no,',
        'not exactly',
        'actually',
        'correction',
        'instead',
        "that's not",
        'that is not',
    )
    if any(marker in normalized for marker in correction_markers):
        return 'correction'
    confirmation_markers = ('yes', 'correct', 'right', 'exactly', 'that matches', 'agreed')
    if any(marker in normalized for marker in confirmation_markers):
        return 'confirmed'
    return 'new_data'


def _confidence_band(confidence: float) -> str:
    if confidence < 0.45:
        return 'low'
    if confidence < 0.75:
        return 'medium'
    return 'high'
