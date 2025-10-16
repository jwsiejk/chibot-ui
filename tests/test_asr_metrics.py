from app.services.streaming_asr.asr_metrics import get_recent, record_turn_metrics


def test_record_turn_metrics_appends_and_returns_latest():
    payload = {"dg_model": "nova-test", "bytes_forwarded": 256, "dg_1011": False}
    entry = record_turn_metrics(42, payload)
    assert entry["turn_id"] == 42
    assert entry["dg_model"] == "nova-test"
    assert entry["bytes_forwarded"] == 256
    recent = get_recent(1)
    assert recent
    latest = recent[0]
    assert latest["turn_id"] == 42
    assert latest["dg_model"] == "nova-test"
    assert latest["bytes_forwarded"] == 256
    assert latest["dg_1011"] is False
