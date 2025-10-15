import { onTtsStart, onTtsEnd } from '../legacy/VoiceLegacyFacade.js';

export { onTtsStart, onTtsEnd };

export function registerTtsEventListener({ createContext, onTtsStart: startFn, onTtsEnd: endFn, windowRef } = {}) {
  const win = windowRef || (typeof window !== 'undefined' ? window : null);
  const addEventListener = win?.addEventListener?.bind(win);
  if (!addEventListener) {
    return;
  }

  try {
    addEventListener('chip-tts', (event) => {
      const contextBase = typeof createContext === 'function' ? createContext(event) || {} : {};
      contextBase.event = event;
      const handledStart = typeof startFn === 'function' ? startFn(contextBase) : false;
      if (handledStart) {
        return;
      }
      const handledEnd = typeof endFn === 'function' ? endFn(contextBase) : false;
      if (handledEnd) {
        return;
      }
    });
  } catch {}
}
