import json

from app.admin import flow_api


def _write_events(tmp_path, events):
    path = tmp_path / "events.ndjson"
    payload = "\n".join(json.dumps(event) for event in events) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def _decode_events(raw_bytes):
    text = raw_bytes.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_filter_events_expands_hud_aliases(tmp_path):
    events = [
        {"type": "EVT_DIAG_HUD", "ts_ms": 1},
        {"type": "EVT_HUD_STATE", "ts_ms": 2},
        {"type": "EVT_CLIENT_MIC_OPEN", "ts_ms": 3},
        {"type": "EVT_OTHER", "ts_ms": 4},
    ]
    path = _write_events(tmp_path, events)

    raw = flow_api._filter_events(path, {"EVT_DIAG_HUD"}, since_ms=None, limit=None)
    filtered = _decode_events(raw)

    assert [event["type"] for event in filtered] == [
        "EVT_DIAG_HUD",
        "EVT_HUD_STATE",
        "EVT_CLIENT_MIC_OPEN",
    ]


def test_filter_events_expands_guard_aliases(tmp_path):
    events = [
        {"type": "EVT_DIAG_FIRST_AUDIO_FRAME", "ts_ms": 5},
        {"type": "EVT_AG_MONITOR", "ts_ms": 6},
        {"type": "EVT_AG_ALERT", "ts_ms": 7},
        {"type": "EVT_LOG", "ts_ms": 8},
    ]
    path = _write_events(tmp_path, events)

    raw = flow_api._filter_events(
        path,
        {"EVT_DIAG_FIRST_AUDIO_FRAME"},
        since_ms=None,
        limit=None,
    )
    filtered = _decode_events(raw)

    assert [event["type"] for event in filtered] == [
        "EVT_DIAG_FIRST_AUDIO_FRAME",
        "EVT_AG_MONITOR",
        "EVT_AG_ALERT",
    ]

