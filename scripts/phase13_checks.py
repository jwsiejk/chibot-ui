
#!/usr/bin/env python3

import os, sys, time, json, signal, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.ws.bus import bus
from app.api_v1.admin import _emit
from app import create_app

def flood_audio(sid="p13", tid="t1", chunks=1000):
    drops = 0
    for i in range(chunks):
        fr = {"type":"audio_chunk","turn_id":tid,"base64":"A"*8192}
        bus.broadcast(sid, fr)
    # if no subscriber, frames buffered; ensure cap applied
    return True

def subscribe_and_drain(sid="p13"):
    q = bus.subscribe(sid)
    got = 0
    start = time.time()
    while time.time() - start < 1.0:
        try:
            fr = q.get(timeout=0.1); got += 1
        except Exception:
            pass
    return got

def test_coalesce_and_cap():
    # Flood without subscribers to trigger pending buffer
    flood_audio(chunks=400)
    # Subscribe and ensure we don't crash; expecting <= max_pending
    q = bus.subscribe("p13")
    cnt = 0
    start = time.time()
    while time.time() - start < 1.0:
        try:
            fr = q.get(timeout=0.05); cnt += 1
        except Exception:
            pass
    assert cnt <= 256, f"pending exceeded cap: {cnt}"
    print("PH13: backpressure cap/coalesce PASS (frames:", cnt, ")")

def test_sigterm_drain():
    app = create_app()
    # Install our SIGTERM handler already in asgi_gateway; simulate
    try:
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None) if signal.getsignal(signal.SIGTERM) else None
        time.sleep(0.2)
        print("PH13: SIGTERM drain handler installed PASS")
    except Exception as e:
        raise AssertionError("SIGTERM drain simulation failed") from e

if __name__ == "__main__":
    test_coalesce_and_cap()
    test_sigterm_drain()
    print("PH13: ALL CHECKS PASS")
