export function emitVoiceEvent(name, detail) {
  if (!window.ADVANCED_LOGGING_ENABLED) return;
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice', {
      detail: { name, ts: Date.now(), ...detail },
    }));
  } catch {}
}
