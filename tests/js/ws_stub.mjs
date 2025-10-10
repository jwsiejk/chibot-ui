function hooks() {
  return globalThis.__TEST_WS_HOOKS || {};
}

export function openWS() { return undefined; }
export function waitWSOpen() { return Promise.resolve(); }
export function isOpen() { return true; }
export function isConnecting() { return false; }
export function closeWS() { return Promise.resolve(); }
export function bufferedAmount() { return 0; }
export function configure() { return undefined; }
export function sendJSON(...args) {
  const fn = hooks().onSendJSON;
  return fn ? fn(...args) : undefined;
}
export function sendAudioChunk(...args) {
  const fn = hooks().onSendAudioChunk;
  const result = fn ? fn(...args) : undefined;
  return Promise.resolve(result);
}
export function sendCloseStream(...args) {
  const fn = hooks().onSendCloseStream;
  const result = fn ? fn(...args) : undefined;
  return Promise.resolve(result);
}
