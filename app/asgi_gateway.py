from __future__ import annotations

"""
Ask Chip — ASGI gateway (Option A, same-origin) — Phase 1 (flip to /api/v1)
- Keeps Flask to serve the UI and legacy endpoints while we migrate.
- Adds versioned /api/v1/* on the Starlette side with full chat pipeline parity.
- Provides WS at /ws/v1/chat that streams PCM chunks (for low-latency playback).
- Bridges Flask cookie sessions so /api/v1/* can read/write session (email, chip_ctx, profile).
- Exposes SSE admin log stream at /api/v1/admin/logs with keepalive heartbeats (prevents worker timeouts).
- Serves /static/** directly from ASGI (StaticFiles) to avoid WSGI bridge races on assets.

Start command (Render UI or dash):
  gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --timeout ${WEB_TIMEOUT:-120} --keep-alive 5 --bind 0.0.0.0:$PORT app.asgi_gateway:asgi
"""

import os
import json
import asyncio
import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional, AsyncGenerator, List, Tuple

import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.requests import Request
from starlette.staticfiles import StaticFiles
from asgiref.wsgi import WsgiToAsgi

# Project services (reuse the Flask pipeline logic)
try:
    from services.reply_service import generate_reply            # full chat pipeline
    from services.entity_normalizer import detect_product, detect_intent, normalize_text_to_pure
    from services.session_ctx import get as ctx_get, set as ctx_set
    from services.llm_service import generate_greeting
except Exception:
    generate_reply = None  # type: ignore
    detect_product = detect_intent = normalize_text_to_pure = None  # type: ignore
    def generate_greeting(*_a, **_k): return "Hey—Chip here. What are we tackling today?"  # type: ignore

try:
    from services.email_service import send_email
except Exception:
    def send_email(*_a, **_k): return False  # type: ignore

try:
    from services.accounts_service import search_accounts
except Exception:
    def search_accounts(q: str, limit: int = 20): return []  # type: ignore

from utils.call_log import call_log

# Mount the existing Flask app at "/"
try:
    from app.legacy_app import create_app as _create_flask_app
except Exception:
    _create_flask_app = None  # type: ignore

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_error(status: int, code: str, message: str, **extra) -> JSONResponse:
    payload = {"ok": False, "code": code, "error": message}
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=status)

def _extract_text(data: Dict[str, Any]) -> str:
    return (data.get("text")
            or data.get("message")
            or data.get("input")
            or data.get("prompt")
            or "").strip()

# -----------------------------
# Flask session bridge (cookie)
# -----------------------------

_flask_app = _create_flask_app() if _create_flask_app else None
_wsgi = WsgiToAsgi(_flask_app) if _flask_app else None

# Serializer to read/write Flask's signed session cookie
_flask_cookie_name = None
_flask_serializer = None
_flask_cookie_params: Dict[str, Any] = {}

if _flask_app:
    try:
        from flask.sessions import SecureCookieSessionInterface
        _ssi = SecureCookieSessionInterface()
        _flask_serializer = _ssi.get_signing_serializer(_flask_app)
        _flask_cookie_name = _flask_app.session_cookie_name
        _flask_cookie_params = dict(
            httponly=True,
            secure=bool(_flask_app.config.get("SESSION_COOKIE_SECURE", False)),
            samesite=_flask_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
            path=_flask_app.config.get("SESSION_COOKIE_PATH", "/"),
        )
    except Exception:
        _flask_serializer = None

def _read_flask_session(request: Request) -> Dict[str, Any]:
    if not _flask_serializer:
        return {}
    raw = request.cookies.get(_flask_cookie_name or "session")
    if not raw:
        return {}
    try:
        data = _flask_serializer.loads(raw)  # type: ignore
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _write_flask_session(resp: Response, session_data: Dict[str, Any]) -> Response:
    if not _flask_serializer:
        return resp
    try:
        raw = _flask_serializer.dumps(session_data)  # type: ignore
        resp.set_cookie(_flask_cookie_name or "session", raw, **_flask_cookie_params)
    except Exception:
        pass
    return resp

# -----------------------------
# Health & features
# -----------------------------

async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "ts": _now_iso(), "service": "ask-chip", "ws": True, "sse": True})

async def features_v1(_: Request) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "features": {"ws": True, "sse": True, "tts": "elevenlabs", "stt": "openai_whisper_optional"}
    })

# -----------------------------
# /api/v1/chat — full pipeline parity
# -----------------------------

async def chat_v1(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        data = {}
    user_text = _extract_text(data)
    if not user_text:
        return _json_error(400, "bad_request", "Missing 'text' in body")
    sess = _read_flask_session(request)

    # Prior context
    ctx_prev = ctx_get(sess)

    # Normalize + intent/product
    normalized = None
    updates: Dict[str, str] = {}
    intent = (detect_intent(user_text) if callable(detect_intent) else None) or ctx_prev.get("intent")
    prior_product = ctx_prev.get("product")

    if callable(normalize_text_to_pure) and (intent or prior_product):
        try:
            normalized, updates = normalize_text_to_pure(user_text)  # type: ignore
        except Exception:
            normalized, updates = None, {}
    detected_product = (updates or {}).get("product") or \
                       (detect_product(user_text) if callable(detect_product) else None) or \
                       prior_product

    clean_text = (normalized or user_text).strip()

    # Persist updated context in Flask session
    ctx_data = ctx_set(sess, {"product": detected_product or "", "intent": intent or ""}) or {}

    # Generate reply using the same service as Flask
    if callable(generate_reply):
        reply, err = generate_reply(clean_text, ctx=ctx_data)  # type: ignore
    else:
        reply, err = clean_text, None

    if err:
        call_log.add("warn", "openai_error", error=str(err))
    call_log.add("chat:v1", "ok", text=user_text, reply=reply, ctx=ctx_data)

    resp = JSONResponse({"ok": True, "reply": reply, "message": reply, "text": reply})
    return _write_flask_session(resp, sess)

# -----------------------------
# Voice: TTS (+visemes placeholder) and STT
# -----------------------------

def _eleven_keys() -> Tuple[str, str, str]:
    key = (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY") or os.getenv("XI_API_KEY") or "").strip()
    voice = (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVEN_VOICE_ID") or os.getenv("CHIP_VOICE_ID") or "").strip()
    model = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2")
    return key, voice, model

def _tts_stream_pcm_chunks(text: str, *, chunk_samples: int = 2400, sample_rate: int = 24000):
    """
    Generator yielding Int16 PCM chunks (base64) for a given text using ElevenLabs stream endpoint.
    Assumes 'pcm_24000' output format unless overridden by ELEVEN_OUTPUT_FORMAT_WS.
    """
    key, voice_id, model_id = _eleven_keys()
    if not key or not voice_id:
        yield {"type": "error", "code": "tts_config", "error": "Missing ELEVENLABS_API_KEY or VOICE_ID"}
        return

    output_fmt = os.getenv("ELEVEN_OUTPUT_FORMAT_WS", "pcm_24000")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": key, "accept": "*/*", "content-type": "application/json"}
    payload = {"text": text, "model_id": model_id, "optimize_streaming_latency": 0, "output_format": output_fmt}
    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=90) as r:
            if r.status_code != 200:
                try:
                    err_json = r.json()
                    msg = err_json.get("detail") or err_json.get("error") or err_json
                except Exception:
                    msg = r.text[:300]
                yield {"type": "error", "code": "tts_error", "error": f"ElevenLabs {r.status_code}: {msg}"}
                return
            buf = bytearray()
            frame_bytes = chunk_samples * 2  # int16 mono
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                buf.extend(chunk)
                while len(buf) >= frame_bytes:
                    sl = bytes(buf[:frame_bytes]); del buf[:frame_bytes]
                    b16 = base64.b64encode(sl).decode("utf-8")
                    yield {"type": "audio_chunk", "b16": b16, "sr": sample_rate}
            if buf:
                b16 = base64.b64encode(bytes(buf)).decode("utf-8")
                yield {"type": "audio_chunk", "b16": b16, "sr": sample_rate}
    except Exception as e:
        yield {"type": "error", "code": "tts_exception", "error": str(e)}

async def tts_v1(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = _extract_text(data)
    if not text:
        return _json_error(400, "bad_request", "Missing 'text' in body")

    # One-shot TTS via existing bridge (mp3 base64 + visemes)
    try:
        from services.tts_bridge import synthesize_with_visemes  # type: ignore
        audio_b64, visemes, err = synthesize_with_visemes(text)
        if err or not audio_b64:
            call_log.add("tts", "error", error=str(err or "unknown"))
            return _json_error(502, "tts_error", str(err or "TTS failed"))
        call_log.add("tts", "ok", size=len(audio_b64))
        return JSONResponse({"ok": True, "audio_base64": audio_b64, "visemes": visemes})
    except Exception as e:
        call_log.add("tts", "missing_bridge", error=str(e))
        return _json_error(500, "server_error", "TTS bridge not available")

async def stt_v1(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
            file = form.get("file") or form.get("audio") or form.get("blob")
            content = await file.read()  # type: ignore
            filename = getattr(file, "filename", "audio.webm")  # type: ignore
            mime = getattr(file, "content_type", "audio/webm")  # type: ignore
        except Exception:
            return _json_error(400, "bad_request", "Invalid multipart payload")
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
        filename = data.get("filename") or "audio.webm"
        mime = data.get("mime") or "audio/webm"

    try:
        from openai import OpenAI
        client = OpenAI()
        import io as _io
        f = _io.BytesIO(content); f.name = filename  # type: ignore
        model = os.getenv("OPENAI_STT_MODEL", "whisper-1")
        lang = os.getenv("OPENAI_STT_LANGUAGE","en")
        resp = client.audio.transcriptions.create(model=model, file=f, language=lang, response_format="json")
        text = (getattr(resp, "text", "") or "").strip()
        call_log.add("stt", "ok", mime=mime, n=len(text))
        return JSONResponse({"ok": True, "text": text})
    except Exception as e:
        call_log.add("stt", "error", error=str(e))
        return _json_error(502, "stt_error", str(e))

# -----------------------------
# Profile / Me / Email / Accounts
# -----------------------------

async def me_v1(request: Request) -> JSONResponse:
    sess = _read_flask_session(request)
    email = (sess.get("user", {}) or {}).get("email") or sess.get("email")
    return JSONResponse({"ok": True, "email": email or ""})

async def profile_v1(request: Request) -> JSONResponse:
    sess = _read_flask_session(request)
    email = (sess.get("user", {}) or {}).get("email") or sess.get("email")
    if request.method == "GET":
        profile = {
            "firstName": (sess.get("user", {}) or {}).get("firstName") or "",
            "lastName":  (sess.get("user", {}) or {}).get("lastName") or "",
            "role":      (sess.get("user", {}) or {}).get("role") or "",
            "company":   (sess.get("user", {}) or {}).get("company") or "",
            "email": email or "",
        }
        return JSONResponse({"ok": True, "profile": profile})
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = dict(sess.get("user") or {})
    for k in ("firstName", "lastName", "role", "company", "email"):
        v = (data.get(k) or "").strip()
        if v:
            user[k] = v
    if not user.get("email") and email:
        user["email"] = email
    sess["user"] = user
    call_log.add("profile:save", "session", **user)
    try:
        from utils.db import upsert_profile  # type: ignore
        upsert_profile(user)
        call_log.add("profile:save", "db_ok", email=user.get("email"))
    except Exception as e:
        call_log.add("profile:save", "db_skip", error=str(e))
    resp = JSONResponse({"ok": True, "profile": user})
    return _write_flask_session(resp, sess)

async def greet_v1(request: Request) -> JSONResponse:
    sess = _read_flask_session(request)
    profile = sess.get("user") or {}
    try:
        import memory  # optional
        email = (profile or {}).get("email") or sess.get("email")
        if email:
            profile = memory.get_user(email) or profile  # type: ignore
    except Exception:
        pass
    try:
        text = generate_greeting(profile)
        if not text:
            text = "Hey—Chip here. What are we tackling today?"
    except Exception:
        text = "Hey—Chip here. What are we tackling today?"
    call_log.add("greet", "ok", text=text)
    # return all three for FE parity
    return JSONResponse({"ok": True, "text": text, "message": text, "reply": text})

async def email_send_v1(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        data = {}
    to = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    html = (data.get("html") or "").strip()
    body = (data.get("text") or data.get("body") or "").strip()
    if not to or not subject:
        return _json_error(400, "bad_request", "to and subject required")
    ok = False
    try:
        ok = bool(send_email(to, subject, html=html, text=body))
    except Exception as e:
        call_log.add("email", "send_error", error=str(e))
        ok = False
    if ok:
        call_log.add("email", "send_ok", to=to, subject=subject)
        return JSONResponse({"ok": True})
    else:
        call_log.add("email", "send_fail", to=to, subject=subject)
        return JSONResponse({"ok": False, "error": "send_failed"})

async def accounts_search_v1(request: Request) -> JSONResponse:
    q = (request.query_params.get("q") or "").strip()
    if not q:
        call_log.add("accounts", "empty_query")
        return JSONResponse({"ok": True, "results": []})
    try:
        results = search_accounts(q, limit=int(request.query_params.get("limit") or 20))
    except Exception as e:
        call_log.add("accounts", "search_error", error=str(e))
        results = []
    call_log.add("accounts", "search_ok", q=q, n=len(results))
    return JSONResponse({"ok": True, "results": results})

# -----------------------------
# WS: /ws/v1/chat — stream PCM + support barge-in (client stops playback)
# -----------------------------

async def ws_chat(ws: WebSocket):
    await ws.accept()
    path = ws.scope.get("path", "")
    call_log.add("ws", "connected", path=path)
    try:
        await ws.send_json({"type": "ready", "ts": _now_iso(), "service": "ws.chat"})
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type") or "user_text"
            if mtype in ("close", "stop"):
                break
            if mtype not in ("user_text", "text"):
                await ws.send_json({"type": "error", "code": "bad_message", "error": f"Unknown type '{mtype}'"})
                continue

            user_text = _extract_text(msg)
            if not user_text:
                await ws.send_json({"type": "error", "code": "bad_request", "error": "Missing text"})
                continue

            # Generate reply (full pipeline parity)
            try:
                class _FakeReq:
                    def __init__(self, cookies, body):
                        self.cookies = cookies
                        self._body = body
                    async def json(self): return self._body
                _req = _FakeReq({}, {"text": user_text, "ctx": msg.get("ctx") or {}})
                chat_resp = await chat_v1(_req)  # type: ignore
                payload = json.loads(chat_resp.body.decode("utf-8"))
                reply = (payload.get("reply") or "").strip()
            except Exception as e:
                reply = ""
                await ws.send_json({"type": "error", "code": "chat_error", "error": str(e)})

            if not reply:
                await ws.send_json({"type": "final_text", "text": ""})
                await ws.send_json({"type": "end"})
                continue

            await ws.send_json({"type": "partial_text", "text": reply[:80]})
            await ws.send_json({"type": "final_text", "text": reply})

            # Stream PCM audio chunks for the reply
            got_audio = False
            for evt in _tts_stream_pcm_chunks(reply):
                if evt.get("type") == "audio_chunk":
                    got_audio = True
                    await ws.send_json(evt)
                elif evt.get("type") == "error":
                    await ws.send_json(evt)
                    break

            await ws.send_json({"type": "end"})
            call_log.add("ws_chat", "turn_ok", has_audio=bool(got_audio), chars=len(reply))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        call_log.add("error", "ws_exception", error=str(e))
        try:
            await ws.send_json({"type": "error", "code": "server_error", "error": str(e)})
        except Exception:
            pass
    finally:
        call_log.add("ws", "disconnected", path=path)
        try:
            await ws.close()
        except Exception:
            pass

# -----------------------------
# Admin SSE (/api/v1/admin/logs) — with heartbeats
# -----------------------------

async def sse_logs(_: Request) -> StreamingResponse:
    async def _next_from_queue(q, timeout_sec: float = 15.0):
        get = getattr(q, "get", None)
        if get is None:
            await asyncio.sleep(timeout_sec)
            return None
        if asyncio.iscoroutinefunction(get):
            try:
                return await asyncio.wait_for(get(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                return None
        loop = asyncio.get_event_loop()
        def _blocking_get():
            try:
                return get(timeout=timeout_sec)
            except TypeError:
                return get()
            except Exception:
                return None
        try:
            return await loop.run_in_executor(None, _blocking_get)
        except Exception:
            return None

    async def event_stream() -> AsyncGenerator[bytes, None]:
        q = call_log.subscribe()
        try:
            for evt in call_log.recent(100):
                yield f"event: message\ndata: {json.dumps(evt)}\n\n".encode("utf-8")
            while True:
                item = await _next_from_queue(q, timeout_sec=15.0)
                if item is None:
                    yield b": keepalive\n\n"
                else:
                    yield f"event: message\ndata: {json.dumps(item)}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            pass
        finally:
            call_log.unsubscribe(q)
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

# -----------------------------
# Assemble Starlette app & routes
# -----------------------------

asgi = Starlette(debug=bool(os.getenv("DEBUG", "").strip().lower() in ("1","true","yes","on")))

# v1 API
asgi.routes.extend([
    Route("/api/health", endpoint=healthz, methods=["GET"]),  # non-versioned health
    Route("/api/v1/features", endpoint=features_v1, methods=["GET"]),
    Route("/api/v1/chat", endpoint=chat_v1, methods=["POST"]),
    Route("/api/v1/greet", endpoint=greet_v1, methods=["GET"]),
    Route("/api/v1/me", endpoint=me_v1, methods=["GET"]),
    Route("/api/v1/profile", endpoint=profile_v1, methods=["GET", "POST"]),
    Route("/api/v1/email/send", endpoint=email_send_v1, methods=["POST"]),
    Route("/api/v1/accounts/search", endpoint=accounts_search_v1, methods=["GET"]),
    Route("/api/v1/voice/tts-with-visemes", endpoint=tts_v1, methods=["POST"]),
    Route("/api/v1/voice/stt", endpoint=stt_v1, methods=["POST"]),
    Route("/api/v1/admin/logs", endpoint=sse_logs, methods=["GET"]),
])

# WS (v1 + alias for current UI)
asgi.routes.extend([
    WebSocketRoute("/ws/v1/chat", endpoint=ws_chat),
    WebSocketRoute("/ws/chat", endpoint=ws_chat),  # temporary back-compat
])

# Back-compat TTS aliases (temporary; remove after logs show no hits)
asgi.routes.extend([
    Route("/api/speak", endpoint=tts_v1, methods=["POST"]),
    Route("/api/tts_with_visemes", endpoint=tts_v1, methods=["POST"]),
    Route("/api/voice/tts_with_visemes", endpoint=tts_v1, methods=["POST"]),
    Route("/tts_with_visemes", endpoint=tts_v1, methods=["POST"]),
    Route("/tts", endpoint=tts_v1, methods=["POST"]),
    Route("/speak", endpoint=tts_v1, methods=["POST"]),
    Route("/eleven/tts", endpoint=tts_v1, methods=["POST"]),
    Route("/eleven/speak", endpoint=tts_v1, methods=["POST"]),
])

# Serve static assets directly from ASGI (avoids WSGI bridge 500s)
asgi.mount("/static", StaticFiles(directory="static", html=False), name="static")

# Mount Flask app at root for UI + any legacy routes
if _wsgi:
    asgi.mount("/", _wsgi)
else:
    async def _root(_: Request):
        return PlainTextResponse("Ask Chip ASGI gateway running; Flask app not mounted.", status_code=200)
    asgi.routes.append(Route("/", endpoint=_root, methods=["GET"]))

# No CORS under Option A (same origin). If you need cross-origin later, add CORSMiddleware guarded by env.
