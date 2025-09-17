# app/ws/ws_asgi.py — Phase 2 (Deepgram wired; pass-through Results)
from __future__ import annotations
import asyncio, os, contextlib
from typing import Optional, Dict, Any

from .schema_v1 import parse_client_json, make_keepalive_ack, make_results, make_utterance_end, make_error
from .turn_buffer import TurnBuffer
from app.services.streaming_asr.deepgram_client import DeepgramClient

def _dumps(obj) -> str:
    import json as _json
    return _json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def _has_deepgram_key() -> bool:
    return bool((os.getenv("DEEPGRAM_API_KEY") or "").strip())

async def _pump_dg_to_client(dg: DeepgramClient, send, turn_id_ref):
    """Relay Deepgram events to client as Results/UtteranceEnd."""
    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                # ignore
                continue
            if et in ("user_partial","user_final"):
                is_final = (et == "user_final")
                text = ev.get("text") or ""
                await send({"type":"websocket.send","text": _dumps(make_results(turn_id_ref[0], transcript=text, confidence=0.0, is_final=is_final))})
                if is_final:
                    await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id_ref[0]))})
            elif et == "asr_error":
                await send({"type":"websocket.send","text": _dumps(make_error("asr_error", str(ev.get("error") or "unknown")))})
            # else: ignore unknown
    except Exception as e:
        # Non-fatal in tests without vendor
        try:
            await send({"type":"websocket.send","text": _dumps(make_error("relay_fail", e.__class__.__name__))})
        except Exception:
            pass

async def ws_chat(scope, receive, send):
    if scope.get("type") != "websocket":
        await send({"type":"http.response.start","status":404,"headers":[]})
        await send({"type":"http.response.body","body":b"not found"})
        return

    await send({"type":"websocket.accept"})

    cfg: Dict[str, Any] = {}
    buf = TurnBuffer()
    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None
    turn_id_ref = [0]  # box for closure

    async def _ensure_dg_connected():
        nonlocal dg, rx_task
        if dg is None and _has_deepgram_key():
            dg = DeepgramClient({})
            await dg.connect()  # dg.connect
            # set current turn id
            turn_id_ref[0] = buf.turn_seq + 1
            rx_task = asyncio.create_task(_pump_dg_to_client(dg, send, turn_id_ref))

    try:
        while True:
            ev = await receive()
            et = ev.get("type")

            if et == "websocket.receive":
                # Binary frames → forward to Deepgram when enabled; always buffer locally
                if ev.get("bytes") is not None:
                    chunk = ev.get("bytes") or b""
                    buf.append(chunk)
                    if _has_deepgram_key():
                        await _ensure_dg_connected()
                        if dg is not None:
                            await dg.send(chunk)  # dg.send
                    continue

                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")
                        if t == "KeepAlive":
                            await send({"type":"websocket.send","text": _dumps(make_keepalive_ack())})
                        elif t == "Configure":
                            cfg.update(obj)
                            # DeepgramClient sends its own Configure on connect; future phases can map fields.
                        elif t == "CloseStream":
                            # Close the current turn.
                            turn_id, _pcm = buf.close_turn()
                            turn_id_ref[0] = turn_id
                            if _has_deepgram_key() and dg is not None:
                                await dg.close(wait_for_final=True)  # dg.close
                                # rx_task will emit final + utterance_end
                            else:
                                # No vendor: emit an empty final to satisfy contract
                                await send({"type":"websocket.send","text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id))})
                    except ValueError as e:
                        await send({"type":"websocket.send","text": _dumps(make_error("bad_message", str(e)))})

            elif et == "websocket.disconnect":
                break
            else:
                # ignore
                pass
    finally:
        try:
            if dg is not None:
                try:
                    await dg.close(wait_for_final=False)
                except Exception:
                    pass
        finally:
            try:
                if rx_task:
                    rx_task.cancel()
                    with contextlib.suppress(Exception):
                        await rx_task
            except Exception:
                pass
            try:
                await send({"type":"websocket.close"})
            except Exception:
                pass
