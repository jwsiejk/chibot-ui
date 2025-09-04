from app.asgi_gateway import app as flask_app
from app.services.limits import _buckets

def test_rate_limit_chat_short_burst():
    _buckets.clear()
    client = flask_app.test_client()
    oks, too_many = 0, 0
    for i in range(10):  # bigger burst
        rv = client.post('/api/v1/chat', json={'text': f'hello {i}'})
        if rv.status_code == 429:
            too_many += 1
        else:
            oks += 1
    assert too_many >= 1
    assert oks >= 1
