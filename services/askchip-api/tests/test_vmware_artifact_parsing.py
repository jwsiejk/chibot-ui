from pathlib import Path

from app.vmware_artifacts import parse_uploaded_vmware_artifact


def _fixture(name: str) -> bytes:
    return (Path(__file__).parent / 'fixtures' / 'vmware' / name).read_bytes()


def test_parse_vmkernel_log_success() -> None:
    result = parse_uploaded_vmware_artifact('vmkernel.log', _fixture('vmkernel.log'))
    assert result.status == 'parsed_supported'
    assert result.status != 'uploaded_supported_unparsed'
    assert result.evidence is not None
    assert result.evidence.artifact_type == 'vmkernel.log'
    assert result.evidence.parsed_line_count == 3


def test_parse_vobd_log_success() -> None:
    result = parse_uploaded_vmware_artifact('vobd.log', _fixture('vobd.log'))
    assert result.status == 'parsed_supported'
    assert result.status != 'uploaded_supported_unparsed'
    assert result.evidence is not None
    assert 'service_health' in result.evidence.matched_categories


def test_parse_vpxd_log_success() -> None:
    result = parse_uploaded_vmware_artifact('vpxd.log', _fixture('vpxd.log'))
    assert result.status == 'parsed_supported'
    assert result.status != 'uploaded_supported_unparsed'
    assert result.evidence is not None
    assert result.evidence.timestamp_start is not None


def test_parse_unsupported_artifact() -> None:
    result = parse_uploaded_vmware_artifact('hostd.log', b'2026-03-10 00:00:00 hostd started')
    assert result.status == 'uploaded_unsupported'
    assert result.evidence is None


def test_parse_failure_for_malformed_content() -> None:
    result = parse_uploaded_vmware_artifact('vmkernel.log', b'\xff\xfe\x00\x00')
    assert result.status == 'parse_failed'
    assert 'decode' in result.parse_error
