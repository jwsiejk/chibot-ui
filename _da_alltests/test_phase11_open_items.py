import json, copy
from app.asgi_gateway import app as flask_app

def _csrf(c): return c.get('/api/v1/auth/csrf').get_json()['csrf']
def _login(c, email):
    tok = _csrf(c)
    c.post('/api/v1/auth/login', json={'email': email}, headers={'X-CSRF-Token': tok})

def test_diagnostics_and_config_stream_and_codecs(monkeypatch):
    # gate admin
    monkeypatch.setenv("ADMIN_EMAILS", "jwsiejk@purestorage.com")
    c = flask_app.test_client()
    # not logged in -> 403
    assert c.get('/api/v1/admin/diagnostics').status_code == 403
    # login
    _login(c, "jwsiejk@purestorage.com")
    # diagnostics reflects config
    d = c.get('/api/v1/admin/diagnostics').get_json()
    assert d['ok'] and 'ws_ping_interval_ms' in d['diagnostics']
    # config stream SSE opens and receives an event after a config edit
    stream = c.get('/api/v1/admin/config/stream')
    tok = _csrf(c)
    c.post('/api/v1/admin/config', json={'ws_ping_interval_ms': 26000}, headers={'X-CSRF-Token': tok})
    text = stream.data.decode('utf-8', 'ignore')
    assert 'config_updated' in text
    # codecs grid: GET and PUT
    grid = c.get('/api/v1/admin/compat/codecs').get_json()
    assert grid['ok'] and 'audio' in grid['codecs']
    new = copy.deepcopy(grid['codecs'])
    # flip safari webm_opus to true to simulate new support
    if 'webm_opus' in new['audio']:
        new['audio']['webm_opus']['safari'] = True
    put = c.put('/api/v1/admin/compat/codecs', json=new).get_json()
    assert put['ok'] and put['codecs']['version'] == grid['codecs']['version'] + 1

def test_audio_migration_plan(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "jwsiejk@purestorage.com")
    c = flask_app.test_client()
    _login(c, "jwsiejk@purestorage.com")
    plan = c.get('/api/v1/admin/audio_migration').get_json()
    assert plan['ok'] and plan['plan']['current_mode'] in ('scriptprocessor','audioworklet')
    # Flip current mode via config and re-check
    tok = _csrf(c)
    c.post('/api/v1/admin/config', json={'audio_recording_mode':'audioworklet'}, headers={'X-CSRF-Token': tok})
    plan2 = c.get('/api/v1/admin/audio_migration').get_json()
    assert plan2['plan']['current_mode'] == 'audioworklet'
