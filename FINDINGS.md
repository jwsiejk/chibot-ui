# AskChip E2E Findings

**Passed:** 2/12

- **P1_greet_dupe_guard**: FAIL — Admin missing 'assistant_end'; Admin missing 'UtteranceEnd'
- **P2_mic_arms_after_greet**: FAIL — Admin missing 'asr:start'; Fail: admin missing 'asr:start'
- **P3_asr_ready_gate**: FAIL — Admin missing 'latency_breakdown'
- **P4_containerized_opus_sanitized**: FAIL — Deepgram WS URL not observed; Admin missing 'container=webm/opus'; Admin missing 'containerized=true'; Fail: admin missing 'containerized=true'
- **P5_close_timeout_race**: FAIL — Admin missing 'asr:final'; Admin missing 'CloseStream ack'
- **P6_barge_in_pauses_tts**: FAIL — Admin missing 'barge_in'; Admin missing 'tts_pause'
- **P7_state_debounce**: PASS
- **P8_no_assistant_dup_messages**: PASS
- **P9_chips_only_when_needed**: FAIL — Admin missing 'nlu'; nlu.needs_clarification expected true, got undefined; nlu.missing should include one of [depth, delivery_pref], got []; Admin missing 'suggestions_made'
- **P10_persona_governor_on_diagnose**: FAIL — Admin missing 'policy_decision: diagnose'
- **P11_session_goal_persists**: FAIL — Admin missing 'session_goal'; session_goal.depth expected 'deep_dive', got 'undefined'; session_goal.confirmed missing 'depth'; Fail: session_goal missing 'depth'
- **P12_nlu_completeness**: FAIL — Admin missing 'nlu'; No nlu event observed; Fail: nlu missing 'delivery_pref'; Fail: nlu missing 'entities.product'; Fail: nlu missing 'entities.env'