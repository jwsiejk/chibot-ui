from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'service': 'askchip-api'}


def test_api_v1_health_endpoint() -> None:
    response = client.get('/api/v1/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'version': 'v1'}
