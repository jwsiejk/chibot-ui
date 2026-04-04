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
