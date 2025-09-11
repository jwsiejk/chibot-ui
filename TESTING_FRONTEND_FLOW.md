
# Ask Chip Frontend Flow Sanity (docs only)

- On Start: WS opens, mic initialized, **VAD NOT ARMED**.
- Server greet streams; on `state: assistant_speaking` → disarmVAD.
- On `assistant_end` → armVAD (now user can speak).
- During speaking, MediaRecorder collects one blob per turn (≈300ms min speech).
