import pytest
from app import create_app

@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

def test_greet(client):
    r = client.post('/api/greet', json={})
    assert r.status_code == 200
    j = r.get_json()
    assert j.get('ok') is True
    assert isinstance(j.get('text'), str)

def test_chat(client):
    r = client.post('/api/chat', json={'text': 'Hello'})
    assert r.status_code == 200
    j = r.get_json()
    assert j.get('ok') is True
    assert isinstance(j.get('reply'), str)

def test_profile_roundtrip(client):
    payload = {'name': 'Test User', 'title': 'Engineer', 'email': 'test@example.com', 'region': 'NA'}
    r = client.post('/api/profile', json=payload)
    assert r.status_code == 200
    r2 = client.get('/api/profile')
    j2 = r2.get_json()
    assert j2.get('ok') is True
