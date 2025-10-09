import logging
import math
import threading
import time
from typing import Callable, Dict, Optional

from ..db import db
from ..session_state import (
    can_nudge,
    idle_duration_ms,
    is_ready_for_nudge,
    guard_remaining_ms,
    mark_nudge,
    mark_user_action,
    mark_user_activity,
)
from ..session_state import silence_guard_satisfied
from ..ws.bus import bus

_LOCK = threading.Lock()
_scheduled: Dict[str, Dict[str, Dict[str, object]]] = {}

_SECOND_OFFSET_MS = 10_000
_AUTO_OFFSET_MS = 25_000

_LOGGER = logging.getLogger(__name__)


def _enabled() -> bool:
    cfg = db.get_config()
    return bool(cfg.get("nudges_enabled", True))


def _nudge_delay_ms() -> int:
    cfg = db.get_config()
    return int(cfg.get("nudge_delay_ms", 7000))


def _backoff_max() -> int:
    cfg = db.get_config()
    return int(cfg.get("nudge_backoff_after_ignored", 2))


def _silence_guard_ms() -> int:
    cfg = db.get_config()
    try:
        return int(cfg.get("silence_guard_ms", 1800))
    except Exception:
        return 1800


def _timings() -> tuple[int, int, int]:
    base = max(1000, int(_nudge_delay_ms()))
    return base, base + _SECOND_OFFSET_MS, base + _AUTO_OFFSET_MS


def _session_record(session_id: str) -> Dict:
    return db.memory.setdefault(
        "sessions",
        {},
    ).setdefault(
        session_id,
        {
            "email": "user@example.com",
            "messages": [],
            "nudges": 0,
            "persona_id": "chip",
        },
    )


def _increment_session_nudges(session_id: str) -> None:
    sess = _session_record(session_id)
    sess["nudges"] = sess.get("nudges", 0) + 1


def _schedule_timer(
    session_id: str,
    name: str,
    delay_ms: int,
    fn: Callable[[], None],
    *,
    origin: str = "",
) -> None:
    delay = max(0, int(delay_ms)) / 1000.0

    timer: Optional[threading.Timer] = None
    armed_wall = time.time()
    armed_mono = time.monotonic()
    nonce = time.monotonic_ns()

    def _run() -> None:
        nonlocal timer
        if timer is None:
            return
        with _LOCK:
            timers = _scheduled.get(session_id)
            entry = timers.get(name) if timers else None
            current = entry.get("timer") if isinstance(entry, dict) else None
            current_nonce = entry.get("nonce") if isinstance(entry, dict) else None
            if not timers or current is not timer or current_nonce != nonce:
                return
        try:
            elapsed_ms = int((time.monotonic() - armed_mono) * 1000)
            _LOGGER.info(
                "nudge_fired sid=%s name=%s elapsed_ms=%s origin=%s",
                session_id,
                name,
                elapsed_ms,
                origin or "",
            )
            fn()
        finally:
            with _LOCK:
                timers = _scheduled.get(session_id)
                entry = timers.get(name) if timers else None
                if (
                    timers
                    and isinstance(entry, dict)
                    and entry.get("timer") is timer
                    and entry.get("nonce") == nonce
                ):
                    timers.pop(name, None)
                    if not timers:
                        _scheduled.pop(session_id, None)

    timer = threading.Timer(delay, _run)
    timer.daemon = True

    with _LOCK:
        timers = _scheduled.setdefault(session_id, {})
        existing = timers.get(name)
        if isinstance(existing, dict):
            existing_timer = existing.get("timer")
        else:
            existing_timer = existing
        if isinstance(existing_timer, threading.Timer):
            try:
                existing_timer.cancel()
            except Exception:
                pass
        timers[name] = {
            "timer": timer,
            "armed_at": armed_wall,
            "armed_mono": armed_mono,
            "delay_ms": int(delay_ms),
            "origin": origin or "",
            "nonce": nonce,
        }

    _LOGGER.info(
        "nudge_timer_set sid=%s name=%s delay_ms=%s origin=%s",
        session_id,
        name,
        int(delay_ms),
        origin or "",
    )

    timer.start()


def cancel_idle_timers(session_id: str, *, source: str = "", mark: bool = True) -> None:
    timers: Optional[Dict[str, Dict[str, object]]] = None
    with _LOCK:
        timers = _scheduled.pop(session_id, None)

    if timers:
        for entry in list(timers.values()):
            timer = entry.get("timer") if isinstance(entry, dict) else entry
            try:
                if isinstance(timer, threading.Timer):
                    timer.cancel()
            except Exception:
                pass
        _LOGGER.info(
            "nudge_timer_cancelled sid=%s reason=%s names=%s",
            session_id,
            source or "",
            sorted(timers.keys()),
        )

    if mark:
        try:
            mark_user_action(session_id)
        except Exception:
            pass

    if source:
        try:
            mark_user_activity(session_id)
        except Exception:
            pass


def _nudge_text(stage: int) -> str:
    if stage == 1:
        return "Chip here—just checking in. What should we tackle today?"
    if stage == 2:
        return "Still with me? Want a quick start or should I send notes?"
    return "I'll send over a quick summary and close this session for now."


def _emit_suggestions(session_id: str, turn_id: Optional[str]) -> None:
    try:
        from ..services.suggestions import hygienic_suggestions
        from ..services.streaming import merge_suggestions, build_suggestion_items

        legacy = hygienic_suggestions("")
        merged = merge_suggestions(legacy, cap=2)
        if not merged:
            return
        bus.broadcast(
            session_id,
            {
                "type": "suggestions",
                "turn_id": turn_id,
                "items": build_suggestion_items(merged),
            },
        )
    except Exception:
        pass


def _emit_nudge(session_id: str, stage: int) -> None:
    text = _nudge_text(stage)
    if not text:
        return

    try:
        from ..services.streaming import make_assistant_frames, prepare_turn_metadata
    except Exception:
        return

    meta, _, _ = prepare_turn_metadata(
        text,
        {"source": "nudge", "channel": "ws", "silence_stage": stage},
    )
    tid, frames = make_assistant_frames(
        text,
        session_id,
        meta=meta,
        broadcast_immediately=False,
    )

    for fr in frames:
        if fr.get("type") == "end":
            fr["reason"] = "nudge"
        bus.broadcast(session_id, fr)

    if stage == 1:
        _emit_suggestions(session_id, tid)

    try:
        bus.broadcast(
            session_id,
            {
                "type": "text",
                "role": "assistant",
                "content": text,
                "turn_id": tid,
            },
        )
    except Exception:
        pass


def _emit_auto_close(session_id: str) -> None:
    text = _nudge_text(3)
    try:
        from ..services.streaming import make_assistant_frames, prepare_turn_metadata
    except Exception:
        bus.broadcast(
            session_id,
            {
                "type": "assistant_chunk",
                "turn_id": None,
                "text": text,
            },
        )
        bus.broadcast(
            session_id,
            {
                "type": "assistant_end",
                "turn_id": None,
                "reason": "silence_auto_close",
            },
        )
    else:
        meta, _, _ = prepare_turn_metadata(
            text,
            {"source": "nudge_auto_close", "channel": "ws", "silence_stage": 3},
        )
        tid, frames = make_assistant_frames(
            text,
            session_id,
            meta=meta,
            broadcast_immediately=False,
        )
        for fr in frames:
            if fr.get("type") == "end":
                fr["reason"] = "silence_auto_close"
            bus.broadcast(session_id, fr)

    bus.broadcast(session_id, {"type": "session_end", "reason": "silence_auto_close"})


def arm_idle_timers(session_id: str, *, reason: str = "") -> bool:
    if not session_id:
        return False

    cancel_idle_timers(session_id, mark=False)

    if not _enabled():
        return False

    guard_ms = _silence_guard_ms()
    guard_ready = silence_guard_satisfied(session_id, guard_ms)
    if not is_ready_for_nudge(session_id, guard_ms):
        if not guard_ready:
            remaining = int(guard_remaining_ms(session_id, guard_ms))
            if remaining > 0:
                _LOGGER.info(
                    "nudge_guard_wait sid=%s reason=%s remaining_ms=%s",
                    session_id,
                    reason or "guard",
                    remaining,
                )

                def _deferred() -> None:
                    arm_idle_timers(session_id, reason=reason)

                _schedule_timer(
                    session_id,
                    "guard",
                    remaining,
                    _deferred,
                    origin=f"{reason or 'guard'}:guard",
                )
            return False
        _LOGGER.info(
            "nudge_arm_blocked sid=%s reason=%s guard_ms=%s",
            session_id,
            reason or "state_guard",
            guard_ms,
        )
        return False

    first_ms, second_ms, auto_ms = _timings()

    def _can_fire(stage: str) -> bool:
        if not _enabled():
            return False
        if not is_ready_for_nudge(session_id, guard_ms):
            _LOGGER.info(
                "nudge_blocked_state sid=%s stage=%s guard_ms=%s",
                session_id,
                stage,
                guard_ms,
            )
            return False
        try:
            idle_ms = idle_duration_ms(session_id)
        except Exception:
            idle_ms = float("inf")
        delay_ms = _nudge_delay_ms()
        if idle_ms < delay_ms:
            idle_repr = int(idle_ms) if math.isfinite(idle_ms) else "inf"
            _LOGGER.info(
                "nudge_blocked_idle sid=%s stage=%s idle_ms=%s delay_ms=%s",
                session_id,
                stage,
                idle_repr,
                delay_ms,
            )
            return False
        if not silence_guard_satisfied(session_id, guard_ms):
            _LOGGER.info(
                "nudge_blocked_guard sid=%s stage=%s guard_ms=%s",
                session_id,
                stage,
                guard_ms,
            )
            return False
        return True

    def stage_one():
        if not _can_fire("nudge1"):
            return
        if not can_nudge(session_id, _backoff_max()):
            return
        mark_nudge(session_id)
        _increment_session_nudges(session_id)
        _emit_nudge(session_id, 1)

    def stage_two():
        if not _can_fire("nudge2"):
            return
        if not can_nudge(session_id, _backoff_max()):
            return
        mark_nudge(session_id)
        _increment_session_nudges(session_id)
        _emit_nudge(session_id, 2)

    def stage_auto():
        _emit_auto_close(session_id)

    _schedule_timer(session_id, "nudge1", first_ms, stage_one, origin=reason or "")
    _schedule_timer(session_id, "nudge2", second_ms, stage_two, origin=reason or "")
    _schedule_timer(session_id, "auto", auto_ms, stage_auto, origin=reason or "")
    return True


def arm_nudge(session_id: str) -> bool:
    return arm_idle_timers(session_id)


def cancel_nudge(session_id: str) -> None:
    cancel_idle_timers(session_id)


def trigger_nudge_now(session_id: str, stage: int = 1) -> bool:
    if stage not in (1, 2):
        stage = 1
    if not can_nudge(session_id, _backoff_max()):
        return False
    mark_nudge(session_id)
    _increment_session_nudges(session_id)
    _emit_nudge(session_id, stage)
    return True


def _cancel_all() -> None:
    for sid in list(_scheduled.keys()):
        cancel_idle_timers(sid, mark=False)
