const IMPLEMENTATION = Object.create(null);

const KNOWN_METHODS = [
  'initMic',
  'armVAD',
  'disarmVAD',
  'isRecording',
  'bargeIn',
  'setVadBoost',
  'setGreetGateActive',
  'forceBargeInStart',
  'forceBargeInEnd',
  'initVoice',
  'startVoice',
  'stopVoice',
  'onWsOpen',
  'onWsMessage',
  'onWsClose',
  'onMicAvailable',
];

function resolveImplementation(name) {
  const fn = IMPLEMENTATION[name];
  if (typeof fn !== 'function') {
    throw new Error(`VoiceLegacyFacade.${name} not wired`);
  }
  return fn;
}

function delegate(name) {
  return function legacyDelegate(...args) {
    return resolveImplementation(name)(...args);
  };
}

export const initMic = delegate('initMic');
export const armVAD = delegate('armVAD');
export const disarmVAD = delegate('disarmVAD');
export const isRecording = delegate('isRecording');
export const bargeIn = delegate('bargeIn');
export const setVadBoost = delegate('setVadBoost');
export const setGreetGateActive = delegate('setGreetGateActive');
export const forceBargeInStart = delegate('forceBargeInStart');
export const forceBargeInEnd = delegate('forceBargeInEnd');
export const initVoice = delegate('initVoice');
export const startVoice = delegate('startVoice');
export const stopVoice = delegate('stopVoice');
export const onWsOpen = delegate('onWsOpen');
export const onWsMessage = delegate('onWsMessage');
export const onWsClose = delegate('onWsClose');
export const onMicAvailable = delegate('onMicAvailable');

export function registerVoiceLegacyFacade(overrides = {}) {
  if (!overrides || typeof overrides !== 'object') {
    return { ...IMPLEMENTATION };
  }
  for (const name of KNOWN_METHODS) {
    if (Object.prototype.hasOwnProperty.call(overrides, name)) {
      const candidate = overrides[name];
      if (typeof candidate === 'function') {
        IMPLEMENTATION[name] = candidate;
      }
    }
  }
  return { ...IMPLEMENTATION };
}
