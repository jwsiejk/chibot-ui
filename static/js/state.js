export const STATES = Object.freeze({
  READY: "ready",
  LISTENING: "listening",
  THINKING: "thinking",
  RESPONDING: "responding"
});

let current = STATES.READY;
const listeners = new Set();

export function getState(){ return current; }
export function onState(cb){ listeners.add(cb); return () => listeners.delete(cb); }

export function setState(next){
  if (!Object.values(STATES).includes(next)) return;
  const prev = current;
  if (prev === next) return;
  current = next;
  for (const cb of listeners) cb({prev, next});
  document.body.dataset.state = next; // for CSS dots
}