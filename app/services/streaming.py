# app/services/streaming.py — Production-grade assistant framing
# Guarantees assistant_* frames are broadcast even if the LLM provider fails.
# Text-first design: generate/broadcast assistant text; TTS is optional elsewhere.

from __future__ import annotations
from typing import List, Dict, Tuple, Optional, Any, Iterable
import base64
import threading, time as _t
import os
import re

from .llm_provider import get_provider
from .awareness import annotate
from .engagement import score as score_engagement
from .dialog_policy import pick as pick_policy
from .retrieval import search as kb_search
from .persona_prompt import build_persona_preamble, format_kb_context
from .suggestions import hygienic_suggestions
from .vendor_clients import make_openai_client
from .nlg.humanize import humanize_text, sounds_botty
from ..db import db
from ..ws.bus import bus
from ..obs import jlog as _jlog
from ..personas.store import PersonaManager, PersonaStore
from ..personas.prompt_builder import build_messages
from .. import nlu as _nlu

try:
    from ..api_v1.admin import _emit as _admin_emit  # SSE to Admin
except Exception:
    def _admin_emit(*a, **k):  # no-op if admin channel absent
        pass


def _get_persona_for_session(session_id: str) -> Dict:
    try:
        sess = db.memory.get('sessions', {}).get(session_id) or {}
        persona_id = (sess.get('persona_id') or 'chip')
        return db.memory.get('personas', {}).get(persona_id) or {'id': 'chip'}
    except Exception:
        return {'id': 'chip'}


def _build_prompt(seed_text: str, persona: Dict, kb_snippets: List[str], teacher_move: Optional[str]) -> str:
    parts = [build_persona_preamble(persona)]
    if kb_snippets:
        parts.append(format_kb_context(kb_snippets))
    if teacher_move:
        parts.append(f"Teacher move: {teacher_move}.")
    parts.append(f"User said: {seed_text.strip()}")
    return "\n\n".join(parts)


# --- text hygiene -------------------------------------------------------------
# Remove ANY occurrence (not just trailing) of legacy "[KB:n]" stamps, case-insensitive.
_KB_TAG_RE = re.compile(r"\s*\[(?:KB|kb)\s*:\s*\d+\]\s*")

def _scrub_debug_stamps(s: str) -> str:
    """
    Remove legacy debug stamps like "[KB:0]" that were historically appended to
    assistant text for telemetry. We keep the value as metadata instead.
    This removes ALL occurrences safely without touching other content.
    """
    if not s:
        return s
    # Replace any matches with a single space, then trim.
    return _KB_TAG_RE.sub(" ", s).strip()


ENABLE_CHIP_FOUNDATION = os.getenv("ENABLE_CHIP_FOUNDATION", "1").lower() not in ("0", "false", "no")
ENABLE_POLICY_CHIPS = os.getenv("ENABLE_POLICY_CHIPS", "1").strip().lower() not in ("0", "false", "no")
STRICT_SYSTEM_SHIM = (
    "Sound like a human Pure Storage expert. Keep openings short, prefer tight bullets when explaining steps, "
    "and never mention being an AI or chatbot."
)


def _should_use_foundation(seed_text: str) -> bool:
    if not ENABLE_CHIP_FOUNDATION:
        return False
    try:
        if str(seed_text or "").strip().lower() == "greet":
            return False
    except Exception:
        return False
    return True


def _apply_system_shim(messages: List[Dict[str, str]], shim: str) -> List[Dict[str, str]]:
    cloned: List[Dict[str, str]] = [dict(m) for m in messages]
    for msg in cloned:
        if msg.get("role") == "system":
            msg["content"] = (msg.get("content") or "").rstrip() + "\n" + shim
            return cloned
    cloned.insert(0, {"role": "system", "content": shim})
    return cloned


def _call_foundation_llm(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    client = make_openai_client()
    model = (
        cfg.get("openai_model")
        or cfg.get("OPENAI_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )
    temperature = float(cfg.get("gen_temperature", 0.3))
    top_p = float(cfg.get("gen_top_p", 1.0))
    payload = [dict(m) for m in messages]
    resp = client.chat.completions.create(
        model=model,
        messages=payload,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )
    return (resp.choices[0].message.content or "").strip()


def _call_foundation_with_retry(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    primary = _call_foundation_llm(messages, cfg)
    human = humanize_text(primary)
    if not human:
        return human
    if not sounds_botty(human):
        return human
    try:
        strict_msgs = _apply_system_shim(messages, STRICT_SYSTEM_SHIM)
        strict_reply = _call_foundation_llm(strict_msgs, cfg)
        strict_human = humanize_text(strict_reply)
        if strict_human:
            return strict_human
    except Exception:
        pass
    return human


def _broadcast_frames(session_id: str, frames: List[Dict], turn_id: str) -> None:
    try:
        for fr in frames:
            bus.broadcast(session_id, fr)
            if fr.get('type') == 'assistant_chunk':
                _admin_emit('assistant_chunk', session_id=session_id, turn_id=str(turn_id))
            elif fr.get('type') == 'assistant_end':
                _admin_emit('assistant_end', session_id=session_id, turn_id=str(turn_id))
    except Exception:
        pass


def _suggestion_cap(default: int = 4) -> int:
    try:
        cap = int(os.getenv("SUGGESTION_MAX", str(default)))
    except Exception:
        return default
    return max(0, cap)


def _normalize_suggestion_item(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("text", "label", "title"):
            val = raw.get(key)
            if isinstance(val, str):
                return val.strip()
        for val in raw.values():
            if isinstance(val, str):
                return val.strip()
    try:
        return str(raw or "").strip()
    except Exception:
        return ""


def merge_suggestions(*lists: Iterable[Any], cap: int = 4) -> List[str]:
    limit = _suggestion_cap()
    if cap is not None:
        limit = min(limit, cap)
    if limit <= 0:
        return []

    seen: List[str] = []
    seen_set = set()
    for lst in lists:
        if not lst:
            continue
        for raw in lst:
            text = _normalize_suggestion_item(raw)
            if not text:
                continue
            if len(text) > 50:
                text = text[:50].rstrip()
            if not text or text in seen_set:
                continue
            seen.append(text)
            seen_set.add(text)
            if len(seen) >= limit:
                return seen
    return seen[:limit]


def build_suggestion_items(items: List[str]) -> List[Dict[str, str]]:
    return [{"text": it} for it in items]


def _collect_policy_chips(policy: Optional[Dict[str, Any]]) -> List[str]:
    if not ENABLE_POLICY_CHIPS or not policy:
        return []
    raw = policy.get("chips") if isinstance(policy, dict) else None
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        chips = list(raw)
    else:
        chips = [raw]
    cap = _suggestion_cap()
    cleaned: List[str] = []
    seen = set()
    for item in chips:
        text = _normalize_suggestion_item(item)
        if not text:
            continue
        if len(text) > 50:
            text = text[:50].rstrip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
        if len(cleaned) >= cap:
            break
    return cleaned


def make_assistant_frames(seed_text: str,
                          session_id: str,
                          meta: Optional[Dict] = None,
                          correlation_user_msg_id: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """
    Produce assistant frames for a given user seed text using the configured LLM provider.
    ALWAYS returns a frames list that includes at least:
        - one 'assistant_chunk' (with either model text OR a safe fallback), and
        - one 'assistant_end'.
    Also broadcasts frames to the WS bus as they are prepared.
    """
    meta = meta or {}
    cfg = db.get_config()

    if _should_use_foundation(seed_text):
        try:
            return _make_foundation_frames(
                seed_text,
                session_id,
                meta,
                cfg,
                correlation_user_msg_id=correlation_user_msg_id,
            )
        except Exception as e:
            try:
                _admin_emit('foundation_pipeline_error', error=e.__class__.__name__)
            except Exception:
                pass

    return _make_legacy_frames(
        seed_text,
        session_id,
        meta,
        cfg,
        correlation_user_msg_id=correlation_user_msg_id,
    )


def _make_foundation_frames(seed_text: str,
                            session_id: str,
                            meta: Dict[str, Any],
                            cfg: Dict[str, Any],
                            *,
                            correlation_user_msg_id: Optional[str]) -> Tuple[str, List[Dict]]:
    store = PersonaStore()
    persona = PersonaManager(store).get_active()
    dialog_meta = dict(meta or {})

    nlu_result = _nlu.infer(seed_text, persona['id'], dialog_meta, store)
    policy = _nlu.policy.decide(nlu_result, nlu_result.get('tags', {}), persona['id'], store)

    prompt_meta = dict(dialog_meta)
    prompt_meta['intent'] = nlu_result.get('intent')
    if policy.get('teacher_move'):
        prompt_meta['teacher_move'] = policy.get('teacher_move')

    examples = store.match_examples(persona['id'], seed_text)
    messages, teacher_move, prompt_hash = build_messages(
        persona=persona,
        user_text=seed_text,
        dialog_meta=prompt_meta,
        examples=examples,
    )

    reply = _call_foundation_with_retry(messages, cfg)
    safe_reply = _scrub_debug_stamps(reply)

    policy_chips = _collect_policy_chips(policy)

    try:
        _jlog(
            "turn_telemetry",
            sid=session_id,
            intent=nlu_result.get('intent'),
            confidence=nlu_result.get('confidence'),
            teacher_move=policy.get('teacher_move') or teacher_move,
            chips=len(policy_chips),
            prompt_hash=prompt_hash,
        )
    except Exception:
        pass

    turn_id = db.memory.setdefault('turn_seq', 0) + 1
    db.memory['turn_seq'] = turn_id

    frames: List[Dict] = []
    chunk: Dict[str, Any] = {
        'type': 'assistant_chunk',
        'turn_id': str(turn_id),
        'text': safe_reply,
        'kb_hits': 0,
        'intent': nlu_result.get('intent'),
        'teacher_move': policy.get('teacher_move') or teacher_move,
    }
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(chunk)

    frames.append({'type': 'state', 'phase': 'ready'})

    legacy_suggestions = []
    try:
        if cfg.get('suggestions_enabled', True):
            legacy_suggestions = hygienic_suggestions(safe_reply)
    except Exception:
        legacy_suggestions = []
    merged_suggestions = merge_suggestions(policy_chips, legacy_suggestions)
    try:
        _jlog(
            "chips_emit",
            sid=session_id,
            intent=nlu_result.get('intent'),
            count=len(merged_suggestions),
            items=merged_suggestions,
        )
    except Exception:
        pass
    if merged_suggestions:
        frames.append(
            {
                'type': 'suggestions',
                'turn_id': str(turn_id),
                'items': build_suggestion_items(merged_suggestions),
            }
        )

    end_fr = {'type': 'assistant_end', 'turn_id': str(turn_id)}
    if correlation_user_msg_id:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    _broadcast_frames(session_id, frames, str(turn_id))
    return str(turn_id), frames


def _make_legacy_frames(seed_text: str,
                        session_id: str,
                        meta: Dict[str, Any],
                        cfg: Dict[str, Any],
                        *,
                        correlation_user_msg_id: Optional[str]) -> Tuple[str, List[Dict]]:
    persona = _get_persona_for_session(session_id)

    labels = annotate(seed_text, meta)
    labels['engagement'] = score_engagement(seed_text, meta)
    policy = pick_policy(labels, cfg)
    teacher_move = (policy or {}).get('teacher_move')

    kb: List[str] = []
    try:
        if cfg.get('kb_enabled', False):
            kb = kb_search(seed_text, top_k=int(cfg.get('kb_top_k', 3)))
    except Exception:
        kb = []

    prompt = _build_prompt(seed_text, persona, kb, teacher_move)

    try:
        provider = get_provider(cfg)
    except Exception as e:
        provider = None
        _admin_emit("llm_provider_error", error=e.__class__.__name__)

    reply: str
    error_note: Optional[str] = None
    if provider is not None:
        try:
            reply = provider.generate_reply(prompt, persona=persona, teacher_move=teacher_move, context={'kb': kb})
        except Exception as e:
            error_note = f"llm_error:{e.__class__.__name__}"
            reply = "Hi! I’m ready to help. (Model is warming up.)"
            _admin_emit("llm_generate_error", error=e.__class__.__name__)
    else:
        error_note = "llm_not_available"
        reply = "Hi! I’m ready to help."

    safe_reply = _scrub_debug_stamps(reply)

    try:
        _is_greet = False
        try:
            _is_greet = (str(seed_text).strip().lower() == "greet") or (str((meta or {}).get("source","")).strip().lower() == "greet")
        except Exception:
            _is_greet = (str(seed_text).strip().lower() == "greet")
        if _is_greet:
            _words = (safe_reply or "Hi there!").strip().split()
            if len(_words) > 5:
                safe_reply = " ".join(_words[:5]).rstrip(",.;:!")
    except Exception:
        pass

    turn_id = db.memory.setdefault('turn_seq', 0) + 1
    db.memory['turn_seq'] = turn_id

    frames: List[Dict] = []

    chunk = {
        'type': 'assistant_chunk',
        'turn_id': str(turn_id),
        'text': safe_reply,
        'kb_hits': len(kb),
    }
    if correlation_user_msg_id:
        chunk['correlation_user_msg_id'] = correlation_user_msg_id
    if error_note:
        chunk['note'] = error_note
    frames.append(chunk)

    legacy_suggestions: List[Any] = []
    try:
        if cfg.get('suggestions_enabled', True):
            legacy_suggestions = hygienic_suggestions(safe_reply)
    except Exception:
        legacy_suggestions = []
    policy_chips = _collect_policy_chips(policy)
    merged_suggestions = merge_suggestions(policy_chips, legacy_suggestions)
    try:
        intent = labels.get('intent') if isinstance(labels, dict) else None
        _jlog(
            "chips_emit",
            sid=session_id,
            intent=intent,
            count=len(merged_suggestions),
            items=merged_suggestions,
        )
    except Exception:
        pass
    if merged_suggestions:
        frames.append(
            {
                'type': 'suggestions',
                'turn_id': str(turn_id),
                'items': build_suggestion_items(merged_suggestions),
            }
        )

    end_fr = {'type': 'assistant_end', 'turn_id': str(turn_id)}
    if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
        end_fr['correlation_user_msg_id'] = correlation_user_msg_id
    frames.append(end_fr)

    _broadcast_frames(session_id, frames, str(turn_id))
    return str(turn_id), frames


# --- MIME mapping helper ------------------------------------------------------

def _guess_tts_mime(output_format: str | None) -> str:
    """
    Map configured TTS output format to an appropriate MIME/container for MSE playback.
    We keep this conservative to avoid mismatches with provider bytes.
    """
    fmt = (output_format or os.environ.get('ELEVEN_OUTPUT_FORMAT') or '').lower()
    # Prefer actual, common provider defaults. If unknown, assume MP3 to avoid mismatches.
    if 'webm' in fmt and 'opus' in fmt:
        return 'audio/webm; codecs="opus"'
    if 'ogg' in fmt and 'opus' in fmt:
        return 'audio/ogg; codecs="opus"'
    if 'mp3' in fmt:
        return 'audio/mpeg'
    if 'wav' in fmt or 'pcm' in fmt or 'l16' in fmt:
        return 'audio/wav'
    # Fallback to MP3 (widely supported; safer than claiming WebM if provider returned MP3)
    return 'audio/mpeg'


# --- WS TTS scheduling (audio over WS) ---------------------------------------
def schedule_tts_audio(session_id: str,
                       text: str,
                       turn_id: str | None = None,
                       correlation_user_msg_id: Optional[str] = None,
                       chunk_bytes: int = 8192,
                       delay_ms: int = 0) -> None:
    """Synthesize TTS for `text` and stream as WS frames.
    Emits frames like:
        {
          "type": "assistant_audio",
          "turn_id": <str or null>,
          "mime": "<audio mime>",
          "audio_chunks": ["<base64>", ...],
          "is_last": <bool>
        }
    After the final chunk, an explicit:
        { "type": "UtteranceEnd", "turn_id": <str or null> }
    is broadcast to mark audio-complete.
    Non-blocking: runs in a background thread.
    """
    if not text:
        return
    cfg = db.get_config()
    feature_audio = bool(cfg.get("feature_audio", True))

    # Track TTS lifecycle for admin truth
    tts_tbl = db.memory.setdefault('tts_status', {})
    sess_tbl = tts_tbl.setdefault(session_id, {})
    turn_key = str(turn_id) if turn_id is not None else 'greet'
    state = sess_tbl.setdefault(turn_key, {'started': False, 'first_chunk': False, 'done': False, 'error': None})

    if not feature_audio:
        return

    def _run():
        try:
            # Small pacing delay if requested
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)

            # Pick provider (vendor only)
            from app.services.tts_provider import get_tts_provider
            provider = get_tts_provider(cfg or {})

            # Determine desired MIME from configured output format
            desired_mime = _guess_tts_mime(cfg.get('tts_output_format'))

            # Synthesize
            try:
                # Re-scrub here as a safety net in case any upstream callers missed it
                audio_bytes, _vis = provider.synth(_scrub_debug_stamps(text))
                state['started'] = True
                try:
                    _admin_emit('tts:start', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
                except Exception:
                    pass
            except Exception as e:
                state['error'] = str(e)
                try:
                    _admin_emit('tts:error', session_id=session_id, turn_id=str(turn_id) if turn_id else None, error=str(e))
                except Exception:
                    pass
                # Even on error, signal end-of-utterance so clients can clean up gracefully
                try:
                    bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
                except Exception:
                    pass
                return

            # Chunk and broadcast
            mv = memoryview(audio_bytes)
            idx = 0
            max_frames = 256  # guardrail
            sent = 0
            first_sent = False

            while idx < len(mv):
                # stop early if canceled
                try:
                    from ..ws.bus import bus as _bus_ref
                    if _bus_ref.is_canceled(session_id, str(turn_id) if turn_id else None):
                        break
                except Exception:
                    pass

                next_idx = idx + int(chunk_bytes)
                part = bytes(mv[idx: next_idx])
                idx = next_idx

                try:
                    b64 = base64.b64encode(part).decode("ascii")
                    fr = {
                        "type": "assistant_audio",
                        "turn_id": str(turn_id) if turn_id else None,
                        "mime": desired_mime,
                        "audio_chunks": [b64],
                        "is_last": (idx >= len(mv)),
                    }
                    if correlation_user_msg_id:
                        fr["correlation_user_msg_id"] = correlation_user_msg_id

                    bus.broadcast(session_id, fr)

                    if not first_sent:
                        first_sent = True
                        state['first_chunk'] = True
                        try:
                            _admin_emit('tts:first_chunk', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
                        except Exception:
                            pass

                    sent += 1
                    if sent >= max_frames:
                        # Safety cut: end stream explicitly if we truncated
                        try:
                            bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
                        except Exception:
                            pass
                        break
                except Exception:
                    # If a single chunk fails, try to continue; we'll still emit UtteranceEnd.
                    pass
        finally:
            # Always mark tts done (admin), and always signal UtteranceEnd if we haven't already.
            try:
                _admin_emit('tts:done', session_id=session_id, turn_id=str(turn_id) if turn_id else None)
            except Exception:
                pass

            try:
                bus.broadcast(session_id, {"type": "UtteranceEnd", "turn_id": str(turn_id) if turn_id else None})
            except Exception:
                pass

            state['done'] = True

    threading.Thread(target=_run, daemon=True).start()


def schedule_frames(session_id: str,
                    frames: List[Dict],
                    delay_ms: int = 0,
                    correlation_user_msg_id: Optional[str] = None) -> None:
    """
    Optionally schedule frames with a delay (for pacing). Ensures an assistant_end is emitted.
    Non-blocking: runs in a background thread.
    """
    def _run():
        try:
            if delay_ms and delay_ms > 0:
                _t.sleep(max(0, delay_ms) / 1000.0)
            for fr in frames:
                try:
                    # Belt-and-suspenders: never let legacy stamps slip through on rebroadcast.
                    if isinstance(fr, dict) and fr.get('type') == 'assistant_chunk' and 'text' in fr:
                        fr['text'] = _scrub_debug_stamps(fr['text'])
                    bus.broadcast(session_id, fr)
                except Exception:
                    pass
            # Ensure an assistant_end terminator exists
            if not any(fr.get('type') in ('assistant_end', 'end') for fr in frames):
                end_fr = {'type': 'assistant_end', 'turn_id': frames[-1].get('turn_id') if frames else None}
                if correlation_user_msg_id and 'correlation_user_msg_id' not in end_fr:
                    end_fr['correlation_user_msg_id'] = correlation_user_msg_id
                try:
                    bus.broadcast(session_id, end_fr)
                except Exception:
                    pass
        finally:
            try:
                _admin_emit('schedule_frames_done', session_id=session_id)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


# ---- WS orchestration helpers (centralize greet/user pipelines) ----

def run_ws_greet(session_id: str) -> str:
    """
    Produce assistant text for greet, broadcast frames, schedule TTS audio,
    and nudge UI with state+suggestions. Returns turn_id.
    """
    tid, frames = make_assistant_frames("greet", session_id, meta={"source": "ws_greet"})
    # TTS: use first assistant_chunk text if present (feature_audio gating inside schedule_tts_audio)
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid)
    except Exception:
        pass
    # UI nudges
    try:
        bus.broadcast(session_id, {"type": "state", "phase": "ready"})
        base_suggestions = merge_suggestions(hygienic_suggestions(""))
        if base_suggestions:
            bus.broadcast(
                session_id,
                {"type": "suggestions", "turn_id": tid, "items": build_suggestion_items(base_suggestions)},
            )
    except Exception:
        pass
    return tid


def run_ws_user_turn(session_id: str, text: str, correlation_user_msg_id: Optional[str] = None) -> str:
    """
    Produce assistant text for a user turn and schedule both text pacing and TTS.
    Mirrors HTTP /api_v1/chat behavior for consistency.
    """
    tid, frames = make_assistant_frames(text, session_id, meta={"source": "user_ws"},
                                        correlation_user_msg_id=correlation_user_msg_id)
    # Keep schedule_frames for pacing/end guarantees to mirror HTTP route
    try:
        schedule_frames(session_id, frames, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    # TTS for assistant text
    try:
        text_for_tts = next((fr.get("text") for fr in frames if fr.get("type") == "assistant_chunk"), "")
        if text_for_tts:
            # Pass scrubbed text to TTS (extra safety)
            schedule_tts_audio(session_id, _scrub_debug_stamps(text_for_tts), turn_id=tid, correlation_user_msg_id=correlation_user_msg_id)
    except Exception:
        pass
    return tid
