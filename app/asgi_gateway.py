
from __future__ import annotations

"""
ASGI gateway for Ask Chip — Option A (same service, same origin, no CORS).

- Starts a Starlette app that:
  • Exposes versioned REST under /api/v1/*
  • Exposes a WebSocket chat loop under /ws/v1/chat
  • Exposes Server‑Sent Events (SSE) admin logs under /api/v1/admin/logs
  • Mounts the existing Flask WSGI app at "/" for templates/static/legacy routes

Start command (Render):
  gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:$PORT app.asgi_gateway:asgi
"""

import os
import json
import asyncio
import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional, AsyncGenerator, List

import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.requests import Request
from asgiref.wsgi import WsgiToAsgi

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _bool_env(key: str, default: bool=False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.lower() in ("1","true","t","yes","y","on")

def _json_error(status: int, code: str, message: str, **extra) -> JSONResponse:
    payload = {"ok": False, "code": code, "error": message}
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=status)

def _extract_text(data: Dict[str, Any]) -> str:
    return (
        (data.get("text")
         or data.get("message")
         or data.get("input")
         or data.get("prompt")
         or "").strip()
    )

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")

# --------------------------------------------------------------------------------------
# Lightweight in-process call log with async subscribers (SSE + diagnostics)
# Falls back to this implementation if utils.call_log is missing.
# --------------------------------------------------------------------------------------

class _AsyncCallLog:
    def __init__(self, maxlen: int = 500):
        from collections import deque
        self._entries = deque(maxlen=maxlen)
        self._listeners: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def add(self, kind: str, msg: str, **extra) -> Dict[str, Any]:
        evt = {"ts": _now_iso(), "kind": kind, "message": msg}
        if extra:
            evt.update(extra)
        self._entries.append(evt)
        # fanout (fire-and-forget)
        for q in list(self._listeners):
            try:
                q.put_nowait(evt)
            except Exception:
                pass
        return evt

    def recent(self, n: int = 100) -> List[Dict[str, Any]]:
        n = max(0, min(n, 500))
        return list(self._entries)[-n:]

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._listeners.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass

# Try to use the project's existing call_log if present (threaded version).
try:
    from utils.call_log import call_log as _threaded_call_log  # type: ignore

    class _CompatCallLog(_AsyncCallLog):
        """Adapt the threaded CallLog to our async interface."""
        async def add(self, kind: str, msg: str, **extra) -> Dict[str, Any]:
            try:
                return _threaded_call_log.add(kind, msg, **extra)  # type: ignore
            except Exception:
                return await super().add(kind, msg, **extra)

        def recent(self, n: int = 100) -> List[Dict[str, Any]]:
            try:
                return _threaded_call_log.recent(n)  # type: ignore
            except Exception:
                return super().recent(n)

        async def subscribe(self) -> asyncio.Queue:
            # Bridge by reading from a background thread and pushing to an asyncio.Queue.
            import queue, threading
            q_async: asyncio.Queue = asyncio.Queue()
            q_sync = _threaded_call_log.subscribe()  # type: ignore

            def pump():
                try:
                    while True:
                        try:
                            item = q_sync.get(timeout=1.0)
                            asyncio.run_coroutine_threadsafe(q_async.put(item), asyncio.get_event_loop())
                        except queue.Empty:
                            continue
                except Exception:
                    pass

            t = threading.Thread(target=pump, daemon=True)
            t.start()
            return q_async

        async def unsubscribe(self, q: asyncio.Queue) -> None:
            # No-op; underlying threaded log manages listeners.
            return None

    call_log = _CompatCallLog()
except Exception:
    call_log = _AsyncCallLog()

# --------------------------------------------------------------------------------------
# OpenAI helpers (1.x SDK if available)
# --------------------------------------------------------------------------------------

def _openai_client():
    try:
        from openai import OpenAI
        return OpenAI()
    except Exception:
        return None

async def _llm_complete(user_text: str, ctx: Optional[Dict[str, Any]]=None) -> str:
    """Return a short Chip-style reply. Falls back to a simple echo if not configured."""
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    sys_prompt = (
        "You are Chip, a concise, friendly systems engineer. "
        "Answer briefly, in natural sentences (no rigid lists), unless asked for details."
    )
    client = _openai_client()
    if client:
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0.6,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_text},
                ],
            )
            txt = (resp.choices[0].message.content or "").strip()
            if txt:
                return txt
        except Exception as e:
            await call_log.add("warn", "openai_error", error=str(e), model=model)
    # Fallback
    return f"{user_text}".strip() or "Hi—what should we tackle first?"

# --------------------------------------------------------------------------------------
# ElevenLabs TTS (with optional visemes) – minimal bridge
# --------------------------------------------------------------------------------------

def _eleven_keys():
    key = (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY") or "").strip()
    voice = (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVEN_VOICE_ID") or os.getenv("CHIP_VOICE_ID") or "").strip()
    model = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2")
    return key, voice, model

def _tts_with_elevenlabs(text: str, *, voice_id: Optional[str]=None, model_id: Optional[str]=None) -> Dict[str, Any]:
    """
    Returns: { ok: bool, audio_base64: str|None, visemes: list|None, error: str|None }
    """
    key, default_voice, default_model = _eleven_keys()
    voice_id = voice_id or default_voice
    model_id = model_id or default_model
    if not key:
        return {"ok": False, "error": "ELEVENLABS_API_KEY not set", "audio_base64": None, "visemes": None}
    if not voice_id:
        return {"ok": False, "error": "ELEVENLABS_VOICE_ID/VOICE_ID not set", "audio_base64": None, "visemes": None}

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "optimize_streaming_latency": 0,
        "output_format": "mp3_44100_128",
        # If your account supports it, you can request additional metadata; many plans
        # do not yet return viseme data via REST. We return None for visemes today.
        # "enable_subtitles": True,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            try:
                err_json = r.json()
                msg = err_json.get("detail") or err_json.get("error") or err_json
            except Exception:
                msg = r.text[:300]
            return {"ok": False, "error": f"ElevenLabs error {r.status_code}: {msg}", "audio_base64": None, "visemes": None}
        audio_b64 = _b64(r.content)
        # Visemes not provided by this stream endpoint; return None for now.
        return {"ok": True, "audio_base64": audio_b64, "visemes": None, "error": None}
    except Exception as e:
        return {"ok": False, "error": f"TTS exception: {e}", "audio_base64": None, "visemes": None}

# --------------------------------------------------------------------------------------
# STT via OpenAI Whisper (1.x SDK)
# --------------------------------------------------------------------------------------

async def _stt_transcribe_bytes(filename: str, content: bytes, mime: str = "audio/wav") -> Dict[str, Any]:
    client = _openai_client()
    if not client:
        return {"ok": False, "error": "OPENAI_API_KEY not set or SDK not available", "text": None}
    model = os.getenv("OPENAI_STT_MODEL", "whisper-1")
    try:
        # The 1.x SDK accepts a file-like object.
        import io as _io
        f = _io.BytesIO(content)
        f.name = filename  # type: ignore[attr-defined]
        resp = client.audio.transcriptions.create(model=model, file=f)
        text = (resp.text or "").strip()
        return {"ok": True, "text": text}
    except Exception as e:
        await call_log.add("warn", "stt_error", error=str(e), model=model)
        return {"ok": False, "error": str(e), "text": None}

# --------------------------------------------------------------------------------------
# Starlette app + routes
# --------------------------------------------------------------------------------------

asgi = Starlette(debug=_bool_env("DEBUG", False))

# ---- Health ----
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "ts": _now_iso(), "service": "ask-chip", "ws": True, "sse": True})

# ---- /api/v1/chat ----
async def api_chat(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        data = {}
    user_text = _extract_text(data)
    if not user_text:
        return _json_error(400, "bad_request", "Missing 'text' in body")
    ctx = data.get("ctx") or {}
    reply = await _llm_complete(user_text, ctx=ctx)
    await call_log.add("chat", "reply", text=user_text, reply=reply)
    return JSONResponse({"ok": True, "reply": reply, "message": reply, "text": reply})

# ---- /api/v1/voice/tts-with-visemes ----
async def api_tts(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = _extract_text(data)
    if not text:
        return _json_error(400, "bad_request", "Missing 'text' in body")
    voice_id = data.get("voice_id") or data.get("voice") or None
    model_id = data.get("model_id") or data.get("model") or None
    res = _tts_with_elevenlabs(text, voice_id=voice_id, model_id=model_id)
    if not res.get("ok"):
        await call_log.add("warn", "tts_error", error=res.get("error"), text=text)
        return _json_error(502, "tts_error", str(res.get("error") or "TTS failed"))
    await call_log.add("tts", "ok", size=len(res.get("audio_base64") or ""))
    return JSONResponse({"ok": True, "audio_base64": res["audio_base64"], "visemes": res.get("visemes")})

# ---- /api/v1/voice/stt ----
async def api_stt(request: Request) -> JSONResponse:
    # Accept multipart (file) or JSON { audio_base64, filename, mime }
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if not file:
            return _json_error(400, "bad_request", "Missing 'file' form field")
        content = await file.read()  # type: ignore[attr-defined]
        filename = getattr(file, "filename", "audio.wav")  # type: ignore[attr-defined]
        mime = getattr(file, "content_type", "audio/wav")  # type: ignore[attr-defined]
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
        b64 = (data.get("audio_base64") or "").split(",")[-1].strip()
        if not b64:
            return _json_error(400, "bad_request", "Missing 'audio_base64' in body")
        try:
            content = base64.b64decode(b64)
        except Exception:
            return _json_error(400, "bad_request", "Invalid base64 in 'audio_base64'")
        filename = data.get("filename") or "audio.wav"
        mime = data.get("mime") or "audio/wav"
    res = await _stt_transcribe_bytes(filename, content, mime=mime)
    if not res.get("ok"):
        return _json_error(502, "stt_error", str(res.get("error") or "STT failed"))
    await call_log.add("stt", "ok", text=res.get("text"))
    return JSONResponse({"ok": True, "text": res.get("text")})

# ---- /ws/v1/chat (text -> TTS audio roundtrip) ----
async def ws_chat(ws: WebSocket):
    await ws.accept()
    await call_log.add("ws", "connected", path="/ws/v1/chat")
    try:
        await ws.send_json({"type": "ready", "ts": _now_iso(), "service": "ws.chat"})
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype in (None, "user_text", "text"):
                user_text = _extract_text(msg)
                if not user_text:
                    await ws.send_json({"type": "error", "code": "bad_request", "error": "Missing text"})
                    continue
                # Generate reply
                reply = await _llm_complete(user_text, ctx=msg.get("ctx") or {})
                await call_log.add("ws_chat", "reply", text=user_text, reply=reply)
                await ws.send_json({"type": "assistant_text", "text": reply})
                # TTS
                tts = _tts_with_elevenlabs(reply)
                if not tts.get("ok"):
                    await ws.send_json({"type": "error", "code": "tts_error", "error": str(tts.get("error"))})
                else:
                    await ws.send_json({
                        "type": "assistant_audio",
                        "audio_base64": tts["audio_base64"],
                        "visemes": tts.get("visemes"),
                        "mime": "audio/mpeg",
                    })
            elif mtype == "ping":
                await ws.send_json({"type": "pong", "ts": _now_iso()})
            elif mtype in ("close", "stop"):
                break
            else:
                await ws.send_json({"type": "error", "code": "bad_message", "error": f"Unknown type '{mtype}'"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await call_log.add("error", "ws_exception", error=str(e))
        try:
            await ws.send_json({"type": "error", "code": "server_error", "error": str(e)})
        except Exception:
            pass
    finally:
        await call_log.add("ws", "disconnected", path="/ws/v1/chat")
        try:
            await ws.close()
        except Exception:
            pass

# ---- /api/v1/admin/logs (SSE) ----
async def sse_logs(request: Request) -> StreamingResponse:
    # Optional ?history=100 to replay recent events
    try:
        history_n = int(request.query_params.get("history", "0"))
    except Exception:
        history_n = 0

    async def event_stream() -> AsyncGenerator[bytes, None]:
        # Replay history
        if history_n:
            for evt in call_log.recent(history_n):
                yield f"event: message\ndata: {json.dumps(evt)}\n\n".encode("utf-8")

        q = await call_log.subscribe()
        try:
            # Heartbeat
            async def _heartbeat():
                while True:
                    yield b": keepalive\\n\\n"
                    await asyncio.sleep(15)

            hb = _heartbeat()
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: message\ndata: {json.dumps(evt)}\n\n".encode("utf-8")
                except asyncio.TimeoutError:
                    # send heartbeat
                    try:
                        yield next(hb)  # type: ignore
                    except StopIteration:
                        hb = _heartbeat()
                        yield b": keepalive\\n\\n"
        finally:
            await call_log.unsubscribe(q)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # for nginx
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

# --------------------------------------------------------------------------------------
# Route table
# --------------------------------------------------------------------------------------

routes = [
    Route("/api/health", endpoint=health, methods=["GET"]),
    Route("/api/v1/chat", endpoint=api_chat, methods=["POST"]),
    Route("/api/v1/voice/tts-with-visemes", endpoint=api_tts, methods=["POST"]),
    Route("/api/v1/voice/stt", endpoint=api_stt, methods=["POST"]),
    Route("/api/v1/admin/logs", endpoint=sse_logs, methods=["GET"]),
    WebSocketRoute("/ws/v1/chat", endpoint=ws_chat),
]

# Instantiate app with routes
asgi = Starlette(routes=routes, debug=_bool_env("DEBUG", False))

# Mount the existing Flask WSGI app (if available) at the root path
try:
    # The project exposes a WSGI app at app:app — import lazily to avoid circulars.
    from app import app as flask_app  # type: ignore
    asgi.mount("/", WsgiToAsgi(flask_app))
except Exception as e:
    # If Flask isn't available (e.g., during unit tests), expose a minimal root.
    async def _root(_: Request):
        return PlainTextResponse("Ask Chip ASGI gateway is running. Flask app not mounted.", status_code=200)
    asgi.routes.append(Route("/", endpoint=_root, methods=["GET"]))

# No CORS middleware under Option A (same-origin). If you need cross-origin later,
# add Starlette CORSMiddleware here guarded by an env flag.
# --------------------------------------------------------------------------------------
