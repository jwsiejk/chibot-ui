#!/usr/bin/env python3
import os, sys, json, base64, time
sys.path.insert(0, '.')

# Test-only env relaxations
os.environ.setdefault('OPENAI_API_KEY','TEST')
os.environ.setdefault('OPENAI_MODEL','gpt-4o-mini')
os.environ.setdefault('ELEVENLABS_API_KEY','TEST')
os.environ.setdefault('ELEVENLABS_VOICE_ID','TESTVOICE')
os.environ.setdefault('EMAIL_HOST','smtp.test')
os.environ.setdefault('EMAIL_PORT','587')
os.environ.setdefault('EMAIL_HOST_USER','user')
os.environ.setdefault('EMAIL_HOST_PASSWORD','pass')
os.environ.setdefault('FROM_EMAIL','chip@example.com')
os.environ.setdefault('EMAIL_USE_TLS','true')
os.environ.setdefault('RATE_LIMIT_WINDOW_S','0.05')
os.environ.setdefault('RATE_LIMIT_MAX','100')
os.environ['CSRF_ENFORCED']=''

# Monkeypatch network
import urllib.request as _u, json as _j
class _Resp:
    def __init__(self, b: bytes): self._b=b
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _urlopen(req, timeout=30):
    url = req.full_url if hasattr(req,'full_url') else str(req)
    if '/v1/chat/completions' in url:
        return _Resp(_j.dumps({'choices':[{'message':{'content':'ok'}}]}).encode('utf-8'))
    if url.endswith('/alignment'):
        return _Resp(_j.dumps({'phonemes':[
            {'start_ms':0,'phoneme':'HH'},
            {'start_ms':120,'phoneme':'EH'},
            {'start_ms':260,'phoneme':'L'},
        ]}).encode('utf-8'))
    if '/text-to-speech/' in url and '/alignment' not in url:
        return _Resp(b'FAKE_MP3_DATA_BYTES')
    if '/v1/audio/transcriptions' in url:
        return _Resp(_j.dumps({'text':'hello world'}).encode('utf-8'))
    return _Resp(b'{}')
_u.urlopen = _urlopen

# Fake SMTP
import smtplib as _s
class _S:
    def __init__(self, host, port, timeout=30): pass
    def starttls(self): pass
    def login(self,u,p): pass
    def sendmail(self,f,t,m): pass
    def quit(self): pass
_s.SMTP = _S

import app
app_ = app.create_app()
client = app_.test_client()

def A(c,m):
    print(('PASS' if c else 'FAIL')+': '+m)
    return c

ok = True

# greet
r = client.get('/api/v1/greet?session_id=testsid')
ok &= A(r.status_code == 200, 'GET /api/v1/greet returns 200')
ok &= A(r.is_json, 'greet returns JSON')
j = r.get_json()
ok &= A(j.get('ok') is True and 'turn_id' in j, 'greet JSON ok + turn_id')

# chat
time.sleep(0.12)
r = client.post('/api/v1/chat', json={'text':'Test message'})
print('# DEBUG chat', r.status_code, r.data[:200])
ok &= A(r.status_code in (200,202), 'POST /api/v1/chat returns 200/202')
ok &= A(r.is_json, 'chat returns JSON')

# tts
time.sleep(0.12)
r = client.post('/api/v1/chat/tts-with-visemes', json={'text':'Hello world'})
print('# DEBUG tts', r.status_code, r.data[:200])
ok &= A(r.status_code == 200, 'POST /api/v1/chat/tts-with-visemes returns 200')
j = r.get_json()
ok &= A(j.get('ok') is True and isinstance(j.get('audio_b64'), str) and isinstance(j.get('visemes'), list), 'tts JSON ok + audio_b64 + visemes present')

# stt
time.sleep(0.12)
r = client.post('/api/v1/voice/stt', data=b'FAKE', content_type='audio/webm')
print('# DEBUG stt', r.status_code, r.data[:200])
ok &= A(r.status_code in (200,400), 'POST /api/v1/voice/stt returns 200/400')
ok &= A(r.is_json, 'stt returns JSON')

# admin logs
r = client.get('/api/v1/admin/logs')
ok &= A(r.status_code in (200,206), 'GET /api/v1/admin/logs returns OK-ish')

print('\nRESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
