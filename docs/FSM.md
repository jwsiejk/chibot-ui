# UI State Machine (client)

States: Disconnected → Connecting → Greeting → Listening → Thinking → Responding → Listening

Allowed transitions:
- Disconnected → Connecting
- Connecting → Greeting | Error | Disconnected
- Greeting → Listening | Error
- Listening → Thinking | Responding | Error
- Thinking → Responding | Listening | Error
- Responding → Listening | Error
- Error → Connecting | Disconnected

Invariant: show **Listening** only when asr.ready has been received AND recorder.listening is true.
