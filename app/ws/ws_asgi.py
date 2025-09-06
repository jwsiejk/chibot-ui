# app/ws/ws_asgi.py
import json, asyncio, urllib.parse
from .bus import bus
from ..services.streaming import make_assistant_frames
from .barge import BargeState
from app.api_v1.admin import _emit
from app.drain_state import is_draining
from .one_tab import acquire as _acquire, release as _release

def _normalize_frame(fr: dict) -> dict:
    """Map internal frame dialect to external client dialect:
       text -> assistant_chunk (content -> text)
       end  -> assistant_end
    """
    try:
        t = fr.get("type")
        if t == "text":
            out = dict(fr); out["type"] = "assistant_chunk"; out["text"] = out.get("content","" ); out.pop("content", None); return out
        if t == "end":
            out = dict(fr); out["type"] = "assistant_end"; return out
        return fr
    except Exception:
        return fr

async def ws_chat(scope, receive, send):
    assert scope["type"] == "websocket"

    # Parse session_id from query string
    qs = scope.get("query_string", b"").decode("utf-8")
    params = urllib.parse.parse_qs(qs)
    session_id = (params.get("session_id", [None])[0]) or "default"
    tab_id = (params.get("tab", [None])[0]) or (params.get("tab_id", [None])[0]) or "default"
    _key = f"{session_id}:{tab_id}"
    barge = BargeState()
    paused = False

    last_tid = None

    # Accept
    await send({"type": "websocket.accept"})
    last_activity = __import__('time').time()
    try:
        _emit('ws_open', session_id=session_id)
    except Exception:
        pass
    # One-WS-per-tab guard
    if not _acquire(_key):
        await send({"type":"websocket.close","code":4001})
        return
    # Initial ready state
    await send({"type": "websocket.send", "text": json.dumps({"type": "state", "phase": "ready"})})

    # Subscribe to the bus
    loop = asyncio.get_event_loop()
    q = bus.subscribe(session_id)

    async def forward_bus():
        while True:
            # The bus queue is threadsafe, use executor to avoid blocking
            frame = await loop.run_in_executor(None, q.get)
            # Drop late frames for canceled turns
            tid = frame.get("turn_id")
            if frame.get("type") in ("text", "audio_chunk", "end") and tid and bus.is_canceled(session_id, tid):
                continue
            await send({"type": "websocket.send", "text": json.dumps(frame, separators=(",", ":"))})

    forward_task = asyncio.create_task(forward_bus())

    async def idle_watchdog():
        try:
            import time
            from app.db import db as _db
            cfg = _db.get_config(); idle_ms = int(cfg.get('ws_idle_timeout_ms', 30000))
            while True:
                await asyncio.sleep(1.0)
                if is_draining():
                    await send({'type':'websocket.close','code':1012})
                    break
                if idle_ms <= 0: continue
                if (time.time() - last_activity) * 1000.0 > idle_ms:
                    await send({'type':'websocket.close','code':1001})
                    break
        except Exception:
            pass
    watchdog_task = asyncio.create_task(idle_watchdog())

    try:
        while True:
            ev = await receive()
            t = ev["type"]

            if t == "websocket.disconnect":
                forward_task.cancel()
                break

            if t == "websocket.receive":
                last_activity = __import__('time').time()
                data = ev.get("text")
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except Exception:
                    continue

                mtype = msg.get("type")

                # Heartbeat: reply to app-level pings
                if mtype == "ping":
                    await send({"type": "websocket.send", "text": json.dumps({"type": "pong", "t": msg.get("t")})})
                    continue

                if mtype == "control":
                    cmd = msg.get("cmd")
                    if cmd == "barge_start":
                        try:
                            _emit('nudge','barge_start',session_id=session_id)
                        except Exception:
                            pass
                        # Soft barge-in: pause, then confirm after confirm_ms
                        confirm_ms = int((msg.get("confirm_ms") or 0) or int((__import__('app').db.db.get_config().get('confirm_ms', 420))))
                        def _send_state(phase):
                            asyncio.create_task(send({"type": "websocket.send", "text": json.dumps({"type":"state","phase": phase})}))
                        def _on_commit():
                            nonlocal last_tid
                            if last_tid:
                                bus.cancel_turn(session_id, last_tid)
                        barge.start(confirm_ms=confirm_ms, on_commit=_on_commit, send_state=_send_state)
                    elif cmd == "barge_cancel":
                        def _send_state(phase):
                            asyncio.create_task(send({"type": "websocket.send", "text": json.dumps({"type":"state","phase": phase})}))
                        barge.cancel(send_state=_send_state)
                    elif cmd in ("barge_commit", "interrupt", "esc"):
                        def _send_state(phase):
                            asyncio.create_task(send({"type": "websocket.send", "text": json.dumps({"type":"state","phase": phase})}))
                        def _on_commit():
                            nonlocal last_tid
                            if last_tid:
                                bus.cancel_turn(session_id, last_tid)
                        barge.commit(send_state=_send_state)
                    elif cmd == "nudge":
                        # server synthesizes a short nudge reply
                        _, frames = make_assistant_frames("Still with me? Want a quick recap?")
                        for fr in frames:
                            if fr.get("type") == "end":
                                fr["reason"] = "nudge"
                            await send({"type":"websocket.send","text": json.dumps(_normalize_frame(fr), separators=(",",":"))})

                elif mtype == "user_text":
                    try:
                        from ..policy.nudges import cancel_nudge
                        cancel_nudge(session_id)
                    except Exception:
                        pass
                    _, frames = make_assistant_frames(msg.get("text") or "")
                    for fr in frames:
                        if fr.get("type") == "text":
                            last_tid = fr.get("turn_id")
                        await send({"type":"websocket.send","text": json.dumps(_normalize_frame(fr), separators=(",",":"))})
    finally:
        try:
            forward_task.cancel()
        except Exception:
            pass
        try:
            watchdog_task.cancel()
        except Exception:
            pass
        try:
            _release(_key)
        except Exception:
            pass
        try:
            _emit('ws_close', session_id=session_id)
        except Exception:
            pass
