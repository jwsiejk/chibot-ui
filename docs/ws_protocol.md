# AskChip WS Protocol (Phase 1)

This document defines the **Phase 1** WebSocket contract for `/ws/v1/chat`. It aligns to the Deepgram streaming model so that the FE and BE meet on an unambiguous schema.

## Client → Server (text JSON)

- `{"type":"Configure","encoding":"opus","sample_rate":48000,"channels":1,"smart_format":true,"punctuate":true,"vad_events":true,"utterance_end_ms":1200}`  
  Optional; mirrors provider flags. The server stores the values for the session.

- `{"type":"KeepAlive"}`  
  Sent ~every 3–5 seconds while idle. The server responds with `{"type":"KeepAliveAck"}`.

- `{"type":"CloseStream"}`  
  Ends the current user turn. The server finalizes any buffered audio and **emits a final `Results`**, then `UtteranceEnd`.

## Client → Server (binary)

- **Binary Opus** frames (as produced by `MediaRecorder('audio/webm;codecs=opus')`) are sent as raw binary frames.

## Server → Client (text JSON)

- Results (Deepgram-aligned):
  ```json
  {
    "type": "Results",
    "channel": {
      "alternatives": [
        {"transcript": "...", "confidence": 0.93}
      ],
      "is_final": true
    },
    "turn_id": 1
  }
  ```

- Utterance boundary (when `vad_events=true` and `utterance_end_ms>=1000`):
  ```json
  {"type":"UtteranceEnd","turn_id":1}
  ```

- Keepalive acknowledgement:
  ```json
  {"type":"KeepAliveAck"}
  ```

- Error:
  ```json
  {"type":"Error","code":"bad_message","message":"..."}
  ```

## Phase 1 Acceptance

- This document is present at `docs/ws_protocol.md`.
- FE/BE unit tests validate **text vs binary demux** and that **`CloseStream` produces a final `Results`**.
