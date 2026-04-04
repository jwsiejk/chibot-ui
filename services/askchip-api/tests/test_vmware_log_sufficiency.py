from app.vmware_log_sufficiency import VMWARE_LOG_REQUIREMENT_MATRIX, evaluate_vmware_log_sufficiency


def test_vmware_log_requirement_matrix_has_expected_issue_families() -> None:
    assert 'host-networking' in VMWARE_LOG_REQUIREMENT_MATRIX
    assert 'storage-pathing' in VMWARE_LOG_REQUIREMENT_MATRIX
    assert 'vcenter-services' in VMWARE_LOG_REQUIREMENT_MATRIX


def test_evaluator_marks_sufficient_when_all_required_metadata_names_are_present() -> None:
    result = evaluate_vmware_log_sufficiency(
        'host-networking',
        ['esx01-vmkernel.log', 'esx01-vobd.log', 'vcenter-events-bundle.tgz'],
    )

    assert result.log_sufficiency_status == 'sufficient'
    assert result.missing_logs == []
    assert set(result.received_logs) == {'vmkernel.log', 'vobd.log', 'vCenter Server logs'}


def test_evaluator_marks_partial_when_only_some_required_logs_are_present() -> None:
    result = evaluate_vmware_log_sufficiency('storage-pathing', ['cluster-vmkernel.log'])

    assert result.log_sufficiency_status == 'partial'
    assert result.received_logs == ['vmkernel.log']
    assert result.missing_logs == ['ESXi host support bundle', 'Storage array event logs']


def test_evaluator_marks_unknown_for_unmapped_issue_family() -> None:
    result = evaluate_vmware_log_sufficiency('cpu-scheduler', ['vmkernel.log'])

    assert result.log_sufficiency_status == 'unknown_issue_family'
    assert result.required_logs == []
    assert result.received_logs == []


def test_vcenter_services_with_only_vpxd_log_stays_partial() -> None:
    result = evaluate_vmware_log_sufficiency('vcenter-services', ['vpxd.log'])

    assert result.log_sufficiency_status == 'partial'
    assert result.received_logs == ['vpxd.log']
    assert result.missing_logs == ['vCenter Server logs']


def test_generic_archive_does_not_count_as_esxi_support_bundle() -> None:
    result = evaluate_vmware_log_sufficiency('storage-pathing', ['bundle.tgz', 'host-vmkernel.log'])

    assert result.log_sufficiency_status == 'partial'
    assert result.received_logs == ['vmkernel.log']
    assert 'ESXi host support bundle' in result.missing_logs


def test_weak_san_or_array_names_do_not_satisfy_storage_array_event_logs() -> None:
    weak_names = ['san.txt', 'array.txt', 'storage-array-notes.md']
    result = evaluate_vmware_log_sufficiency('storage-pathing', weak_names)

    assert result.log_sufficiency_status == 'insufficient'
    assert result.received_logs == []
    assert 'Storage array event logs' in result.missing_logs


def test_storage_array_event_log_requires_clear_event_artifact() -> None:
    result = evaluate_vmware_log_sufficiency('storage-pathing', ['powerstore-controller-events.log'])

    assert result.log_sufficiency_status == 'partial'
    assert result.received_logs == ['Storage array event logs']
