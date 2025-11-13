# Google Cloud Speech-to-Text — Best Practices (BP)

- GCP Streaming Speech-to-Text is the default ASR path.
- The WebSocket adapter always streams 16-bit PCM audio at 16 kHz mono.
- Environment configuration relies on default Google credentials (no vendor-specific overrides required).
- The Admin panel exposes a single "Google Cloud STT" vendor choice.
- Telemetry and readiness diagnostics now report the vendor as `gcp`.
