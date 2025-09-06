import os
from app.asgi_gateway import app as flask_app
from app.db import db

def _csrf(c): return c.get('/api/v1/auth/csrf').get_json()['csrf']
def _login_admin(c,email):
    tok=_csrf(c); c.post('/api/v1/auth/login', json={'email':email}, headers={'X-CSRF-Token': tok}); return tok

def minimal_pack(pid, voice):
    return {
        "id": pid,
        "public_title": "Demo Persona",
        "persona_intensity": 0.13,
        "prompt": {"system": "Be helpful", "guidelines": []},
        "policy": {"teacher_moves": True},
        "lexicon_tweaks": [],
        "tts": {"provider": "elevenlabs", "voice_id": voice},
        "quotes": {"enabled": True, "bank": "q"},
        "features": {"suggestions": True, "nudges": True}
    }

def test_persona_crud_publish_rollback_preview_and_export(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS","jwsiejk@purestorage.com")
    c = flask_app.test_client()
    tok = _login_admin(c,"jwsiejk@purestorage.com")
    # create draft
    r = c.post('/api/v1/admin/personas', json={"pack": minimal_pack("demo","v1"), "publish": False})
    assert r.status_code==200 and r.get_json()['state']['draft']['tts']['voice_id']=="v1"
    # publish v1
    r2 = c.post('/api/v1/admin/personas/demo/publish')
    assert r2.status_code==200 and r2.get_json()['published']['version']==1
    # update draft to v2
    r3 = c.put('/api/v1/admin/personas/demo', json={"pack": minimal_pack("demo","v2")})
    assert r3.status_code==200 and r3.get_json()['draft']['tts']['voice_id']=="v2"
    # publish v2
    r4 = c.post('/api/v1/admin/personas/demo/publish')
    assert r4.get_json()['published']['version']==2
    # rollback to v1
    r5 = c.post('/api/v1/admin/personas/demo/rollback', json={"version":1})
    assert r5.get_json()['published']['version']==1
    # export should show voice v1
    exp = c.get('/api/v1/admin/personas/demo/export').get_json()['pack']
    assert exp['tts']['voice_id']=="v1"
    # preview on a session
    prev = c.post('/api/v1/admin/personas/demo/preview', json={"session_id":"sP"}).get_json()
    assert prev['persona_id']=="demo"
    assert db.memory['sessions']['sP']['persona_id']=="demo"
    # import second persona (published by default)
    imp = c.post('/api/v1/admin/personas/import', json={"pack": minimal_pack("imported","iv1")}).get_json()
    assert imp['ok'] and imp['published']['version']==1

def test_persona_validation_blocks_arbitrary_code(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS","jwsiejk@purestorage.com")
    c = flask_app.test_client(); _login_admin(c,"jwsiejk@purestorage.com")
    bad = minimal_pack("evil","v1"); bad["code"] = "print('hack')"
    r = c.post('/api/v1/admin/personas', json={"pack": bad})
    assert r.status_code==400 and "forbidden" in r.get_json()['error']
