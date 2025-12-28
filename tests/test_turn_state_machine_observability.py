import unittest

from app.ws.turn_state_machine import TurnEvent, TurnState, TurnStateMachine


class TurnStateMachineObservabilityTests(unittest.TestCase):
    def test_duplicate_turn_start_is_idempotent(self) -> None:
        machine = TurnStateMachine()
        first = machine.apply(TurnEvent.TURN_START, 100)
        second = machine.apply(TurnEvent.TURN_START, 110)

        self.assertEqual(first.to_state, TurnState.CAPTURING)
        self.assertEqual(second.to_state, TurnState.CAPTURING)
        self.assertIn("duplicate_turn_start", second.actions)
        self.assertFalse(second.illegal)

    def test_duplicate_turn_stop_is_idempotent(self) -> None:
        machine = TurnStateMachine()
        machine.apply(TurnEvent.TURN_START, 100)
        first_stop = machine.apply(TurnEvent.TURN_STOP, 150)
        second_stop = machine.apply(TurnEvent.TURN_STOP, 160)

        self.assertEqual(first_stop.to_state, TurnState.INTERRUPTED)
        self.assertEqual(second_stop.to_state, TurnState.INTERRUPTED)
        self.assertIn("duplicate_turn_stop", second_stop.actions)
        self.assertFalse(second_stop.illegal)

    def test_asr_final_after_stop_is_tracked(self) -> None:
        machine = TurnStateMachine()
        machine.apply(TurnEvent.TURN_START, 100)
        machine.apply(TurnEvent.TURN_STOP, 150)
        final_transition = machine.apply(TurnEvent.ASR_FINAL, 200)

        self.assertEqual(final_transition.from_state, TurnState.INTERRUPTED)
        self.assertEqual(final_transition.to_state, TurnState.FINALIZING)
        self.assertIn("late_asr_final", final_transition.actions)
        self.assertFalse(final_transition.illegal)

    def test_illegal_transition_logs_as_illegal(self) -> None:
        machine = TurnStateMachine()
        transition = machine.apply(TurnEvent.ASR_FINAL, 100)

        self.assertEqual(transition.from_state, TurnState.IDLE)
        self.assertEqual(transition.to_state, TurnState.IDLE)
        self.assertTrue(transition.illegal)
        self.assertEqual(machine.illegal_count, 1)
        self.assertEqual(machine.transition_count, 1)
        self.assertIs(machine.first_illegal, transition)

    def test_turn_finalized_timestamp_is_monotonic(self) -> None:
        machine = TurnStateMachine()
        machine.apply(TurnEvent.TURN_START, 100)
        machine.apply(TurnEvent.ASR_FINAL, 150)
        machine.apply(TurnEvent.TURN_FINALIZED, 200)

        timestamps = [entry.ts_ms for entry in machine.timeline]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_asr_opening_state_removed(self) -> None:
        self.assertNotIn("ASR_OPENING", TurnState.__members__)

    def test_timeline_summary_contains_events(self) -> None:
        machine = TurnStateMachine()
        machine.apply(TurnEvent.TURN_START, 100)
        machine.apply(TurnEvent.ASR_OPEN, 120)
        machine.apply(TurnEvent.ASR_FINAL, 150)
        machine.apply(TurnEvent.TURN_FINALIZED, 200)

        summary = machine.timeline_summary()
        self.assertIn("turn_start:idle->capturing@100", summary)
        self.assertIn("asr_open:capturing->asr_open@120", summary)
        self.assertIn("asr_final:asr_open->finalizing@150", summary)
        self.assertIn("turn_finalized:finalizing->idle@200", summary)

    def test_timeline_summary_is_compressed(self) -> None:
        machine = TurnStateMachine()
        events = [
            TurnEvent.TURN_START,
            TurnEvent.FIRST_AUDIO,
            TurnEvent.ASR_OPEN,
            TurnEvent.ASR_FIRST_AUDIO,
            TurnEvent.ASR_FINAL,
            TurnEvent.TURN_FINALIZED,
            TurnEvent.TURN_START,
            TurnEvent.TURN_STOP,
            TurnEvent.TURN_STOP,
            TurnEvent.TURN_FINALIZED,
        ]
        for idx, event in enumerate(events, start=1):
            machine.apply(event, idx * 10)

        summary = machine.timeline_summary(max_entries=6)
        entries = summary.split(",")
        self.assertEqual(len(entries), 6)
        self.assertIn("…(5 suppressed)…", summary)
        self.assertIn("turn_start:idle->capturing@10", summary)
        self.assertIn("turn_finalized:interrupted->idle@100", summary)

    def test_greet_turn_start_is_illegal(self) -> None:
        machine = TurnStateMachine(initial_state=TurnState.GREET)
        transition = machine.apply(TurnEvent.TURN_START, 100)

        self.assertEqual(transition.from_state, TurnState.GREET)
        self.assertEqual(transition.to_state, TurnState.GREET)
        self.assertTrue(transition.illegal)


if __name__ == "__main__":
    unittest.main()
