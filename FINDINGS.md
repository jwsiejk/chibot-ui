# AskChip E2E Findings

**Passed:** 1/13

- **P1_greet_dupe_guard**: FAIL — WS console missing 'assistant_end'
- **P2_mic_arms_after_greet**: FAIL — WS console missing 'asr:start'; WS latency 'first_partial_from_mic_start' unavailable; Fail: WS console missing 'asr:start'; Fail: WS latency 'first_partial_from_mic_start' unavailable
- **P3_asr_ready_gate**: FAIL — WS console missing 'latency_breakdown'; WS latency 'dg_connect' unavailable; WS latency 'first_partial_from_mic_start' unavailable; Fail: WS latency 'first_partial_from_mic_start' unavailable
- **P4_containerized_opus_sanitized**: FAIL — Deepgram WS URL not observed; WS console missing 'container=webm/opus'; WS console missing 'containerized=true'; Fail: WS console missing 'containerized=true'
- **P5_close_timeout_race**: FAIL — WS console missing 'asr:final'; WS console missing 'CloseStream ack'
- **P6_barge_in_pauses_tts**: FAIL — WS console missing 'barge_in'; WS console missing 'tts_pause'; WS latency 'tts_pause_after_vad' unavailable; Fail: WS latency 'tts_pause_after_vad' unavailable
- **P7_state_debounce**: FAIL — WS state events missing for spam check
- **P8_no_assistant_dup_messages**: PASS
- **P9_chips_only_when_needed**: FAIL — WS console missing 'nlu'; No WS NLU event observed; WS console missing 'suggestions_made'; WS chips count 4 > 3; Fail: WS chips 4 > 3; Fail: No WS NLU event observed
- **P10_persona_governor_on_diagnose**: FAIL — WS console missing 'policy_decision: diagnose'
- **P11_session_goal_persists**: FAIL — WS console missing 'session_goal'; WS session_goal hints missing; Fail: WS session_goal hints missing
- **P12_nlu_completeness**: FAIL — WS console missing 'nlu'; No WS NLU event observed; Fail: WS nlu missing 'delivery_pref'; Fail: WS nlu missing 'entities.product'; Fail: WS nlu missing 'entities.env'
- **P15_long_help_session**: FAIL — WS console missing 'latency_breakdown'