
def test_phase_status_marked_complete():
    s = open("docs/WS_PHASE_PLAN.md","r",encoding="utf-8").read()
    assert "Phase 0" in s and "COMPLETE" in s, "Phase 0 should be marked COMPLETE"
    assert "Phase 1" in s and "COMPLETE" in s, "Phase 1 should be marked COMPLETE"
    assert "Phase 2" in s and "COMPLETE" in s, "Phase 2 should be marked COMPLETE"
