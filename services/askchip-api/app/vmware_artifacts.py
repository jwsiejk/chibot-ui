from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.api_models import VmwareArtifactEvidence

SUPPORTED_VMWARE_LOGS = {'vmkernel.log', 'vobd.log', 'vpxd.log'}
VmwareArtifactStatus = Literal[
    'metadata_only',
    'uploaded_supported_unparsed',
    'parsed_supported',
    'uploaded_unsupported',
    'parse_failed',
]
"""Artifact status vocabulary for session metadata + upload records.

Current synchronous upload behavior intentionally emits:
- parsed_supported
- uploaded_unsupported
- parse_failed

Reserved/not emitted in the synchronous upload path:
- metadata_only (filename/session metadata context only; not an upload result)
- uploaded_supported_unparsed (reserved for a future async parsing pipeline)
"""


@dataclass(frozen=True)
class VmwareParseResult:
    status: VmwareArtifactStatus
    artifact_type: str
    evidence: VmwareArtifactEvidence | None
    parse_error: str = ''


def classify_vmware_artifact(filename: str) -> str:
    return Path(filename).name.strip().lower()


def parse_uploaded_vmware_artifact(filename: str, content: bytes) -> VmwareParseResult:
    artifact_type = classify_vmware_artifact(filename)
    if artifact_type not in SUPPORTED_VMWARE_LOGS:
        return VmwareParseResult(status='uploaded_unsupported', artifact_type=artifact_type, evidence=None)

    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as exc:
        return VmwareParseResult(
            status='parse_failed',
            artifact_type=artifact_type,
            evidence=None,
            parse_error=f'utf-8 decode failed: {exc.reason}',
        )

    lines = [line.rstrip('\n') for line in text.splitlines() if line.strip()]
    if not lines:
        return VmwareParseResult(status='parse_failed', artifact_type=artifact_type, evidence=None, parse_error='log file is empty')

    timestamp_values = _extract_timestamps(lines)
    categories, notable_lines = _extract_categories_and_notable_lines(lines)
    evidence = VmwareArtifactEvidence(
        parser_kind='vmware_log_v1',
        artifact_type=artifact_type,
        parsed_line_count=len(lines),
        timestamp_start=min(timestamp_values).isoformat() if timestamp_values else None,
        timestamp_end=max(timestamp_values).isoformat() if timestamp_values else None,
        matched_categories=categories,
        notable_lines=notable_lines,
        parse_warnings=[] if timestamp_values else ['no recognizable timestamps detected'],
    )
    return VmwareParseResult(status='parsed_supported', artifact_type=artifact_type, evidence=evidence)


def _extract_timestamps(lines: list[str]) -> list[datetime]:
    found: list[datetime] = []
    patterns = (
        r'(?P<ymd>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})',
        r'(?P<short>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',
    )
    for line in lines:
        for pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            raw = match.group(0).replace(' ', 'T')
            try:
                found.append(datetime.fromisoformat(raw))
                break
            except ValueError:
                continue
    return found


def _extract_categories_and_notable_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    category_patterns: dict[str, tuple[str, ...]] = {
        'storage': ('storage', 'scsi', 'nmp', 'vmfs', 'datastore', 'apd', 'pdl'),
        'network': ('net', 'nic', 'vmnic', 'dvport', 'uplink', 'link down', 'tcp'),
        'ha_cluster': ('ha', 'fdm', 'cluster', 'vpxa'),
        'service_health': ('error', 'warn', 'failed', 'exception', 'critical'),
    }
    matched: set[str] = set()
    notable: list[str] = []
    for line in lines:
        lowered = line.lower()
        for category, patterns in category_patterns.items():
            if any(token in lowered for token in patterns):
                matched.add(category)
        if any(token in lowered for token in ('error', 'warn', 'fail', 'exception', 'critical')) and len(notable) < 5:
            notable.append(line[:280])
    return sorted(matched), notable
