# Test Plan

Smoke (manual):
1) Start session → expect: Connecting → Greeting → Listening (badge).
2) Speak 2–3 s → expect: mic_start → partial → final → mic_stop → Responding → Listening.
3) No duplicate headers; no pre-ready audio; no second asr.open.

CI (pytest or log scan):
- Order: RS (RecognitionStarted) → AR (asr.ready) → MS (mic_start) → F (final) → ME (mic_stop).
- Exactly 1 mic_start / 1 mic_stop per turn.
- 0 occurrences of mic start outside asr.ready handler.
- No `header_conflict`. `audio_header_dup_ignored` allowed, but rare.
