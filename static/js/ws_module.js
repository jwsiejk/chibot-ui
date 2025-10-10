const __assetVersion =
  (typeof globalThis !== 'undefined' && globalThis.__askchipAssetVersion) || '';
const __wsSuffix = __assetVersion ? `?v=${__assetVersion}` : '';
export const WS_MODULE_URL = `/static/js/ws.js${__wsSuffix}`;

let cached = null;

const wsModulePromise = import(WS_MODULE_URL).then((mod) => {
  cached = mod;
  return mod;
}).catch((err) => {
  console.error('[ws_module] failed to load ws.js module', err);
  throw err;
});

function moduleReady() {
  return cached;
}

export function getWSModule() {
  return wsModulePromise;
}

export function openWS(...args) {
  if (moduleReady()) return moduleReady().openWS(...args);
  return wsModulePromise.then((mod) => mod.openWS(...args));
}

export function waitWSOpen(...args) {
  if (moduleReady()) return moduleReady().waitWSOpen(...args);
  return wsModulePromise.then((mod) => mod.waitWSOpen(...args));
}

export function isOpen(...args) {
  if (moduleReady()) return moduleReady().isOpen(...args);
  return false;
}

export function isConnecting(...args) {
  if (moduleReady()) return moduleReady().isConnecting(...args);
  return false;
}

export function closeWS(...args) {
  if (moduleReady()) return moduleReady().closeWS(...args);
  wsModulePromise.then((mod) => { mod.closeWS?.(...args); }).catch(() => {});
}

export function bufferedAmount(...args) {
  if (moduleReady()) return moduleReady().bufferedAmount(...args);
  return 0;
}

export function configure(...args) {
  if (moduleReady()) return moduleReady().configure(...args);
  wsModulePromise.then((mod) => { mod.configure?.(...args); }).catch(() => {});
}

export function sendJSON(...args) {
  if (moduleReady()) return moduleReady().sendJSON(...args);
  wsModulePromise.then((mod) => { mod.sendJSON?.(...args); }).catch(() => {});
}

export function sendAudioChunk(...args) {
  if (moduleReady()) return moduleReady().sendAudioChunk(...args);
  return wsModulePromise.then((mod) => mod.sendAudioChunk(...args));
}

export function sendCloseStream(...args) {
  if (moduleReady()) return moduleReady().sendCloseStream(...args);
  wsModulePromise.then((mod) => { mod.sendCloseStream?.(...args); }).catch(() => {});
}
