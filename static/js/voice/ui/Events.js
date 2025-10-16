const MAX_BREADCRUMBS = 50;

function recordVoiceBreadcrumb(entry) {
  try {
    if (typeof window === 'undefined') return;
    let store = window.__voice_breadcrumbs;
    if (!Array.isArray(store)) {
      store = [];
      window.__voice_breadcrumbs = store;
    }
    const payload = Object.freeze({ ...entry });
    store.push(payload);
    if (store.length > MAX_BREADCRUMBS) {
      store.splice(0, store.length - MAX_BREADCRUMBS);
    }
    try {
      console.info(`[voice][${payload.name}]`, payload);
    } catch {}
  } catch {}
}

export function emitVoiceEvent(name, detail) {
  if (!window.ADVANCED_LOGGING_ENABLED) return;
  const baseDetail = detail && typeof detail === 'object' ? { ...detail } : {};
  const eventDetail = { name, ts: Date.now(), ...baseDetail };
  recordVoiceBreadcrumb(eventDetail);
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice', {
      detail: eventDetail,
    }));
  } catch {}
}
