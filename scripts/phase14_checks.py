#!/usr/bin/env python3
import os, sys, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import create_app

ROOT = os.path.dirname(os.path.dirname(__file__))

def assert_in_file(path, patterns):
    text = open(path, 'r', encoding='utf-8', errors='ignore').read()
    for p in patterns:
        if re.search(p, text) is None:
            raise AssertionError(f"Missing '{p}' in {path}")

def run():
    # 1) Server config includes VAD + worklet
    app = create_app()
    cli = app.test_client()
    r = cli.get('/api/v1/admin/config')
    assert r.status_code == 200, f"/admin/config {r.status_code}"
    cfg = (r.get_json() or {}).get('config', {})
    for k in ('audio_worklet_enabled','vad_attack_ms','vad_release_ms','vad_dbfs_threshold'):
        assert k in cfg, f"config missing {k}"
    assert cfg.get('audio_worklet_enabled') in (False, True)

    # 2) Client config exports worklet flag container
    assert_in_file(os.path.join(ROOT, 'static/js/config.js'), [r'export\s+const\s+FEATURES', r'AUDIO_WORKLET_ENABLED'])

    # 3) Voice.js has UA detection + gating + thresholds
    v = os.path.join(ROOT, 'static/js/voice.js')
    assert_in_file(v, [r'function\s+detectAudioCaps', r'workletSupported', r'__PH14_USE_WORKLET', r'getVADThresholds'])

    # 4) WS heartbeat exists
    assert_in_file(os.path.join(ROOT, 'static/js/ws.js'), [r'startHeartbeat\(\)', r'\{\s*type:\s*"ping"'])

    print("PH14: ALL CHECKS PASS")

if __name__ == '__main__':
    run()
