import pytest

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = {'email': 'tester@example.com'}
        yield client


def _get_csrf(client):
    resp = client.get('/api/v1/csrf')
    assert resp.status_code == 200
    token = resp.headers.get('X-CSRF-Token')
    assert token
    return token


def test_post_client_session(client):
    token = _get_csrf(client)
    resp = client.post(
        '/api/v1/asr/client-session',
        json={'session_id': 'unit-test'},
        headers={'X-CSRF-Token': token},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert 'session' in payload and isinstance(payload['session'], dict)


def test_get_client_session(client):
    resp = client.get('/api/v1/asr/client-session?session_id=tester')
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert 'session' in payload and isinstance(payload['session'], dict)
