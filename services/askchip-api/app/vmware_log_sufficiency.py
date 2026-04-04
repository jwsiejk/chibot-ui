from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class VmwareIssueLogRequirements:
    issue_family: str
    required_logs: tuple[str, ...]
    optional_logs: tuple[str, ...]
    why_it_matters: str


@dataclass(frozen=True)
class VmwareLogSufficiencyResult:
    issue_family: str
    log_sufficiency_status: str
    required_logs: list[str]
    received_logs: list[str]
    missing_logs: list[str]
    optional_logs: list[str]
    log_guidance_summary: str


VMWARE_LOG_REQUIREMENT_MATRIX: dict[str, VmwareIssueLogRequirements] = {
    'host-networking': VmwareIssueLogRequirements(
        issue_family='host-networking',
        required_logs=('vmkernel.log', 'vobd.log', 'vCenter Server logs'),
        optional_logs=('ESXi host support bundle', 'Distributed switch / vmnic event export'),
        why_it_matters=(
            'These logs establish whether disconnects came from vmnic/link state changes, host network stack faults, '
            'or vCenter-side management event timing.'
        ),
    ),
    'storage-pathing': VmwareIssueLogRequirements(
        issue_family='storage-pathing',
        required_logs=('vmkernel.log', 'ESXi host support bundle', 'Storage array event logs'),
        optional_logs=('vpxd.log', 'HBA driver logs'),
        why_it_matters=(
            'This set confirms APD/PDL timing and path failover behavior between ESXi and the storage array.'
        ),
    ),
    'vcenter-services': VmwareIssueLogRequirements(
        issue_family='vcenter-services',
        required_logs=('vpxd.log', 'vCenter Server logs'),
        optional_logs=('vSphere UI/API gateway logs', 'PSC/SSO service logs'),
        why_it_matters='These logs show service health, API failures, and control-plane errors inside vCenter services.',
    ),
    'vm-performance': VmwareIssueLogRequirements(
        issue_family='vm-performance',
        required_logs=('vmkernel.log', 'ESXi host support bundle'),
        optional_logs=('esxtop capture', 'vCenter performance charts export'),
        why_it_matters=(
            'These logs are needed to separate host contention and scheduling pressure from guest-only symptoms.'
        ),
    ),
}

_LOG_PATTERN_MAP: dict[str, tuple[str, ...]] = {
    'vmkernel.log': ('vmkernel.log',),
    'vobd.log': ('vobd.log',),
    'vpxd.log': ('vpxd.log',),
    'vCenter Server logs': ('vcenter', 'vcsa', 'applmgmt', 'vpxd-profiler', 'vmon'),
    'ESXi host support bundle': ('vm-support', 'support-bundle', 'esxi-support', 'esx-support'),
    'Storage array event logs': (),
    'Distributed switch / vmnic event export': ('vds', 'distributed switch', 'vmnic', 'uplink'),
    'HBA driver logs': ('hba', 'fc', 'iscsi', 'driver'),
    'vSphere UI/API gateway logs': ('vsphere-ui', 'vapi', 'rhttpproxy'),
    'PSC/SSO service logs': ('sso', 'sts', 'psc'),
    'esxtop capture': ('esxtop',),
    'vCenter performance charts export': ('performance chart', 'perf chart', 'vc-performance'),
}


def evaluate_vmware_log_sufficiency(issue_family: str, uploaded_log_names: list[str]) -> VmwareLogSufficiencyResult:
    normalized_family = issue_family.strip().lower()
    requirements = VMWARE_LOG_REQUIREMENT_MATRIX.get(normalized_family)
    if requirements is None:
        return VmwareLogSufficiencyResult(
            issue_family=normalized_family,
            log_sufficiency_status='unknown_issue_family',
            required_logs=[],
            received_logs=[],
            missing_logs=[],
            optional_logs=[],
            log_guidance_summary='Issue family is not mapped to a deterministic VMware log set yet; request key VMware logs for this path.',
        )

    normalized_uploaded = [name.strip().lower() for name in uploaded_log_names if name and name.strip()]
    received_logs = [label for label in requirements.required_logs if _matches_uploaded_metadata(label, normalized_uploaded)]
    missing_logs = [label for label in requirements.required_logs if label not in received_logs]

    if not requirements.required_logs:
        status = 'unknown_issue_family'
    elif not missing_logs:
        status = 'sufficient'
    elif received_logs:
        status = 'partial'
    else:
        status = 'insufficient'

    if status == 'sufficient':
        summary = (
            f"Log metadata for '{requirements.issue_family}' is sufficient to continue this troubleshooting path. "
            f"{requirements.why_it_matters}"
        )
    elif status == 'partial':
        summary = (
            f"Some required logs for '{requirements.issue_family}' are present, but more are still needed: "
            f"{', '.join(missing_logs)}. {requirements.why_it_matters}"
        )
    elif status == 'insufficient':
        summary = (
            f"Required logs for '{requirements.issue_family}' are not present yet. Collect: "
            f"{', '.join(requirements.required_logs)}. {requirements.why_it_matters}"
        )
    else:
        summary = 'Issue family is not mapped to a deterministic VMware log set yet; request key VMware logs for this path.'

    return VmwareLogSufficiencyResult(
        issue_family=requirements.issue_family,
        log_sufficiency_status=status,
        required_logs=list(requirements.required_logs),
        received_logs=received_logs,
        missing_logs=missing_logs,
        optional_logs=list(requirements.optional_logs),
        log_guidance_summary=summary,
    )


def _matches_uploaded_metadata(log_label: str, uploaded_log_names: list[str]) -> bool:
    if log_label == 'Storage array event logs':
        return any(_looks_like_storage_array_event_log(name) for name in uploaded_log_names)
    patterns = _LOG_PATTERN_MAP.get(log_label, (log_label.lower(),))
    for uploaded in uploaded_log_names:
        if any(pattern in uploaded for pattern in patterns):
            return True
    return False


def _looks_like_storage_array_event_log(uploaded_log_name: str) -> bool:
    storage_source_tokens = (
        'storage-array',
        'storage_array',
        'array-event',
        'controller-event',
        'storage-event',
        'netapp',
        'purestorage',
        'pure-storage',
        '3par',
        'nimble',
        'unity',
        'powerstore',
        'powermax',
        'isilon',
        'ontap',
        'san-switch',
        'fc-switch',
    )
    evidence_tokens = ('event', 'events', 'log', 'logs', 'alert', 'alerts', 'audit')
    separators = re.compile(r'[\s._-]+')
    normalized = separators.sub(' ', uploaded_log_name.strip().lower())
    normalized_source_tokens = tuple(separators.sub(' ', token.strip().lower()) for token in storage_source_tokens)
    has_storage_source = any(token in normalized for token in normalized_source_tokens)
    has_event_evidence = any(token in normalized for token in evidence_tokens)
    return has_storage_source and has_event_evidence
