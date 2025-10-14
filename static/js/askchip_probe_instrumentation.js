// when you arm the mic (or on first MediaRecorder chunk)
window.__askchip_probe?.onMediaContainerized();
window.__askchip_probe?.onAsrStart(turnId);

// when you get the first partial / final
window.__askchip_probe?.onAsrPartial();
window.__askchip_probe?.onAsrFinal(turnId);

// when you compute latencies (or have them server-side and mirror once)
window.__askchip_probe?.onLatencyBreakdown({
  dg_connect: 410,
  first_partial_from_mic_start: 780,
  asr_final: 1320
});

// when VAD interrupts TTS
window.__askchip_probe?.onBargeIn();
window.__askchip_probe?.onTTSPause();

// when policy decides to diagnose
window.__askchip_probe?.onPolicyDiagnose();

// when your interpreter returns results (single event per user turn)
window.__askchip_probe?.onNlu({
  user_goal, phase, depth, delivery_pref,
  entities: { product, env },
  confidence, needs_clarification, missing
});

// when user confirms session depth
window.__askchip_probe?.onSessionGoal('deep_dive', ['depth']);

// when your bus emits a phase change (already auto-detected by this script)
// window.__askchip_probe?.onState('recording');
