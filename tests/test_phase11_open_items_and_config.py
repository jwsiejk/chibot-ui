from pathlib import Path
from app.asgi_gateway import app as flask_app

KEYS = [
    'show_instruction_strip','show_state_dots','theme',
    'suggestions_enabled','suggestions_max_items','suggestions_max_words',
    'nudges_enabled','nudge_delay_ms','nudge_backoff_after_ignored',
    'confirm_ms','echo_threshold_boost','min_speech_ms','voice_command_hints',
    'language_lock','max_turn_seconds','normalization_table_version',
    'nebraska_persona_level','nebraska_quotes_enabled',
    'ws_ping_interval_ms','ws_idle_timeout_ms','reconnect_policy',
    'redact_email_in_logs'
]

def test_admin_config_exposes_phase11_keys():
    c = flask_app.test_client()
    cfg = c.get('/api/v1/admin/config').get_json()['config']
    missing = [k for k in KEYS if k not in cfg]
    assert not missing, f"Missing config keys: {missing}"

def test_docs_exist_with_required_headings():
    root = Path(__file__).resolve().parents[1]
    ws = (root / "docs" / "ws_keepalive.md").read_text()
    grid = (root / "docs" / "browser_codec_grid.md").read_text()
    worklet = (root / "docs" / "audio_worklet_migration.md").read_text()
    assert "keep-alive" in ws.lower() and "idle" in ws.lower()
    assert "Browser / Codec Support Grid" in grid
    assert "AudioWorklet Migration Plan" in worklet
