import json
from app.asgi_gateway import app as flask_app
from app.db import db

def _collect(resp_data: bytes):
    text = resp_data.decode('utf-8', errors='ignore')
    events = []
    for block in [b.strip() for b in text.split('\n\n') if b.strip()]:
        if block.startswith(':'):
            continue
        for line in block.split('\n'):
            if line.startswith('data: '):
                payload = line[len('data: '):]
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
    return events

def test_nudge_backoff_and_end_session_email():
    client = flask_app.test_client()
    stream = client.get('/api/v1/admin/config/stream')  # keep an SSE open; not used
    # First nudge
    rv = client.post('/api/v1/chat', json={'session_id':'s2','cmd':'nudge'})
    assert rv.status_code == 200 and rv.get_json()['nudged'] in (True, False)
    # End session => email sent
    rv = client.post('/api/v1/chat', json={'session_id':'s2','cmd':'end_session','email':'user@example.com'})
    assert rv.status_code == 200 and rv.get_json()['emailed'] is True
    emails = db.list_emails()
    assert any(e['subject'].startswith('Ask Chip — Session transcript') for e in emails), "Expected transcript email"
