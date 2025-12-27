from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

_log = logging.getLogger(__name__)


class TurnState(str, Enum):
    GREET = "greet"
    IDLE = "idle"
    CAPTURING = "capturing"
    ASR_OPENING = "asr_opening"
    ASR_OPEN = "asr_open"
    FINALIZING = "finalizing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


class TurnEvent(str, Enum):
    TURN_START = "turn_start"
    FIRST_AUDIO = "first_audio"
    ASR_OPEN = "asr_open"
    ASR_FIRST_AUDIO = "asr_first_audio"
    ASR_FINAL = "asr_final"
    ASR_TIMEOUT = "asr_timeout"
    TURN_STOP = "turn_stop"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    TURN_FINALIZED = "turn_finalized"


_ILLEGAL_ACTION = "illegal_transition"


_TRANSITIONS: dict[tuple[TurnState, TurnEvent], tuple[TurnState, tuple[str, ...]]] = {
    (TurnState.GREET, TurnEvent.TURN_START): (TurnState.CAPTURING, ("greet_turn_start",)),
    (TurnState.GREET, TurnEvent.TURN_STOP): (TurnState.GREET, ("duplicate_turn_stop",)),
    (TurnState.GREET, TurnEvent.TURN_FINALIZED): (TurnState.IDLE, ("finalized",)),
    (TurnState.IDLE, TurnEvent.TURN_START): (TurnState.CAPTURING, ("turn_started",)),
    (TurnState.IDLE, TurnEvent.TURN_STOP): (TurnState.IDLE, ("duplicate_turn_stop",)),
    (TurnState.CAPTURING, TurnEvent.TURN_START): (TurnState.CAPTURING, ("duplicate_turn_start",)),
    (TurnState.CAPTURING, TurnEvent.FIRST_AUDIO): (TurnState.CAPTURING, ("first_audio",)),
    (TurnState.CAPTURING, TurnEvent.ASR_OPEN): (TurnState.ASR_OPEN, ("asr_open",)),
    (TurnState.CAPTURING, TurnEvent.ASR_FINAL): (TurnState.FINALIZING, ("final_without_open",)),
    (TurnState.CAPTURING, TurnEvent.ASR_TIMEOUT): (TurnState.FINALIZING, ("timeout_without_open",)),
    (TurnState.CAPTURING, TurnEvent.TURN_STOP): (TurnState.INTERRUPTED, ("turn_stop",)),
    (TurnState.ASR_OPENING, TurnEvent.ASR_OPEN): (TurnState.ASR_OPEN, ("asr_open",)),
    (TurnState.ASR_OPENING, TurnEvent.TURN_STOP): (TurnState.INTERRUPTED, ("turn_stop",)),
    (TurnState.ASR_OPEN, TurnEvent.ASR_FIRST_AUDIO): (TurnState.ASR_OPEN, ("asr_first_audio",)),
    (TurnState.ASR_OPEN, TurnEvent.ASR_FINAL): (TurnState.FINALIZING, ("asr_final",)),
    (TurnState.ASR_OPEN, TurnEvent.ASR_TIMEOUT): (TurnState.FINALIZING, ("asr_timeout",)),
    (TurnState.ASR_OPEN, TurnEvent.TURN_STOP): (TurnState.INTERRUPTED, ("turn_stop",)),
    (TurnState.FINALIZING, TurnEvent.ASR_FINAL): (TurnState.FINALIZING, ("duplicate_asr_final",)),
    (TurnState.FINALIZING, TurnEvent.ASR_TIMEOUT): (TurnState.FINALIZING, ("duplicate_asr_timeout",)),
    (TurnState.FINALIZING, TurnEvent.TURN_STOP): (TurnState.FINALIZING, ("duplicate_turn_stop",)),
    (TurnState.FINALIZING, TurnEvent.TTS_START): (TurnState.SPEAKING, ("tts_start",)),
    (TurnState.FINALIZING, TurnEvent.TURN_FINALIZED): (TurnState.IDLE, ("finalized",)),
    (TurnState.SPEAKING, TurnEvent.TTS_END): (TurnState.IDLE, ("tts_end",)),
    (TurnState.SPEAKING, TurnEvent.TURN_FINALIZED): (TurnState.IDLE, ("finalized",)),
    (TurnState.INTERRUPTED, TurnEvent.TURN_STOP): (TurnState.INTERRUPTED, ("duplicate_turn_stop",)),
    (TurnState.INTERRUPTED, TurnEvent.ASR_FINAL): (TurnState.FINALIZING, ("late_asr_final",)),
    (TurnState.INTERRUPTED, TurnEvent.ASR_TIMEOUT): (TurnState.FINALIZING, ("late_asr_timeout",)),
    (TurnState.INTERRUPTED, TurnEvent.TURN_FINALIZED): (TurnState.IDLE, ("finalized",)),
}


@dataclass(frozen=True)
class TurnStateTransition:
    event: TurnEvent
    from_state: TurnState
    to_state: TurnState
    ts_ms: int
    actions: tuple[str, ...]
    illegal: bool


class TurnStateMachine:
    def __init__(self, initial_state: TurnState = TurnState.IDLE) -> None:
        self._state = initial_state
        self._timeline: list[TurnStateTransition] = []

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def timeline(self) -> tuple[TurnStateTransition, ...]:
        return tuple(self._timeline)

    @staticmethod
    def transition(state: TurnState, event: TurnEvent) -> tuple[TurnState, tuple[str, ...]]:
        transition = _TRANSITIONS.get((state, event))
        if transition is None:
            return state, (_ILLEGAL_ACTION,)
        return transition

    def apply(
        self,
        event: TurnEvent,
        ts_ms: int,
        *,
        turn_id: str | None = None,
        turn_index: int | None = None,
        logger: logging.Logger | None = None,
    ) -> TurnStateTransition:
        next_state, actions = self.transition(self._state, event)
        illegal = _ILLEGAL_ACTION in actions
        if illegal:
            (logger or _log).warning(
                "evt=turn_state_illegal_transition state=%s event=%s turn_id=%s turn_index=%s",
                self._state.value,
                event.value,
                turn_id,
                turn_index,
            )
        transition = TurnStateTransition(
            event=event,
            from_state=self._state,
            to_state=next_state,
            ts_ms=ts_ms,
            actions=tuple(actions),
            illegal=illegal,
        )
        self._timeline.append(transition)
        self._state = next_state
        return transition

    def timeline_summary(self) -> str:
        return ",".join(
            f"{entry.event.value}:{entry.from_state.value}->{entry.to_state.value}@{entry.ts_ms}"
            for entry in self._timeline
        )
