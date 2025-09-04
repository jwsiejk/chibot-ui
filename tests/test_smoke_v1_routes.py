def test_greet_route_registered(client):
    rv = client.get('/api/v1/greet')
    # For skeleton: accept 501 Not Implemented JSON with ok field.
    assert rv.is_json
    assert rv.status_code in (200, 501)
    assert 'ok' in rv.get_json()

def test_chat_route_registered(client):
    rv = client.post('/api/v1/chat', json={'text':'hi'})
    assert rv.is_json
    assert rv.status_code in (200, 400, 501)
    assert 'ok' in rv.get_json()

def test_voice_routes_registered(client):
    rv = client.post('/api/v1/voice/tts-with-visemes', json={'text':'hi'})
    assert rv.is_json
    assert rv.status_code in (200, 400, 501)
    assert 'ok' in rv.get_json()

def test_admin_logs_sse(client):
    rv = client.get('/api/v1/admin/logs')
    assert rv.status_code == 200
    assert rv.mimetype == 'text/event-stream'
