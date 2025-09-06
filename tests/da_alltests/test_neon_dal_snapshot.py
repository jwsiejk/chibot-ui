from app.asgi_gateway import app as flask_app
from app.db import db
def test_neon_sqlite_snapshot_and_restore(tmp_path, monkeypatch):
    path = tmp_path / "neon.sqlite"; monkeypatch.setenv("PERSIST_SQLITE_PATH", str(path))
    c = flask_app.test_client()
    c.get('/api/v1/greet?session_id=sn1')
    c.post('/api/v1/chat', json={'session_id':'sn1','text':'hello world'})
    assert c.post('/api/v1/admin/storage/neon/init').status_code==200
    assert c.post('/api/v1/admin/storage/neon/snapshot', json={'session_id':'sn1'}).status_code==200
    db.memory['sessions']={}
    assert c.post('/api/v1/admin/storage/neon/restore', json={'session_id':'sn1'}).status_code==200
    tr=db.get_transcript('sn1'); assert 'USER:' in tr and 'hello world' in tr
