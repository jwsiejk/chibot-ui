const listenerMap = new Map();

export class TestCustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

export const windowStub = {
  __askchip_config: {},
  addEventListener(type, handler) {
    if (!listenerMap.has(type)) listenerMap.set(type, new Set());
    listenerMap.get(type).add(handler);
  },
  removeEventListener(type, handler) {
    const set = listenerMap.get(type);
    if (!set) return;
    set.delete(handler);
    if (set.size === 0) listenerMap.delete(type);
  },
  dispatchEvent(event) {
    const set = listenerMap.get(event.type);
    if (!set) return true;
    for (const handler of Array.from(set)) {
      handler(event);
    }
    return true;
  },
};

windowStub.CustomEvent = TestCustomEvent;
windowStub.dispatchEvent = windowStub.dispatchEvent.bind(windowStub);

export class FakeAnalyser {
  constructor() {
    this.fftSize = 2048;
    this.smoothingTimeConstant = 0.06;
    this._rms = 0;
  }

  connect() {}
  disconnect() {}

  setRmsDb(db) {
    if (!Number.isFinite(db)) {
      this._rms = 0;
      return;
    }
    this._rms = Math.pow(10, db / 20);
  }

  getFloatTimeDomainData(arr) {
    if (!arr) return;
    arr.fill(this._rms);
  }

  getFloatFrequencyData(arr) {
    if (!arr) return;
    arr.fill(-120);
  }
}

class FakeSource {
  connect() {}
  disconnect() {}
}

export const createdAnalysers = [];

let analyserFactory = () => {
  const analyser = new FakeAnalyser();
  createdAnalysers.push(analyser);
  return analyser;
};

export function setAnalyserFactory(factory) {
  analyserFactory = typeof factory === 'function'
    ? () => {
        const analyser = factory();
        createdAnalysers.push(analyser);
        return analyser;
      }
    : () => {
        const analyser = new FakeAnalyser();
        createdAnalysers.push(analyser);
        return analyser;
      };
}

export class FakeAudioContext {
  constructor() {
    this.state = 'running';
    this.sampleRate = 48000;
    this.destination = { channelCount: 1 };
  }

  async resume() {}
  createMediaStreamSource() { return new FakeSource(); }
  createAnalyser() { return analyserFactory(); }
  createGain() {
    return {
      gain: { value: 1 },
      connect() {},
      disconnect() {},
    };
  }
  createBiquadFilter() {
    return {
      type: 'highpass',
      frequency: { value: 0 },
      Q: { value: 0 },
      connect() {},
      disconnect() {},
    };
  }
  createMediaStreamDestination() {
    return {
      stream: fakeStream,
      connect() {},
      disconnect() {},
    };
  }
  close() {}
}

export const fakeStream = {
  active: true,
  getAudioTracks() {
    return [{
      label: 'Fake Mic',
      getSettings: () => ({ sampleRate: 48000, channelCount: 1 }),
    }];
  },
};

class FakeMediaRecorder {
  constructor() {
    this.state = 'inactive';
    this.mimeType = 'audio/ogg; codecs=opus';
    this._handlers = new Map();
  }

  start() {
    this.state = 'recording';
    if (typeof this.onstart === 'function') {
      setTimeout(() => this.onstart(), 0);
    }
    const handlers = this._handlers.get('start');
    if (handlers) {
      for (const handler of Array.from(handlers)) {
        try { handler({ type: 'start' }); } catch {}
      }
    }
  }

  stop() {
    this.state = 'inactive';
    if (typeof this.onstop === 'function') {
      setTimeout(() => this.onstop(), 0);
    }
    const handlers = this._handlers.get('stop');
    if (handlers) {
      for (const handler of Array.from(handlers)) {
        try { handler({ type: 'stop' }); } catch {}
      }
    }
  }

  addEventListener(type, handler) {
    if (!this._handlers.has(type)) {
      this._handlers.set(type, new Set());
    }
    this._handlers.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    const handlers = this._handlers.get(type);
    if (!handlers) return;
    handlers.delete(handler);
    if (handlers.size === 0) {
      this._handlers.delete(type);
    }
  }
}

class FakeAudioElement {
  constructor() {
    this.volume = 1;
    this.paused = true;
    this._listeners = new Map();
  }

  get autoplay() { return this._autoplay; }
  set autoplay(v) { this._autoplay = !!v; }

  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    const set = this._listeners.get(type);
    if (!set) return;
    set.delete(handler);
    if (set.size === 0) this._listeners.delete(type);
  }

  _emit(type) {
    const set = this._listeners.get(type);
    if (!set) return;
    for (const handler of Array.from(set)) {
      handler();
    }
  }

  play() {
    this.paused = false;
    this._emit('playing');
    return Promise.resolve();
  }

  pause() {
    if (!this.paused) {
      this.paused = true;
      this._emit('pause');
    }
  }

  captureStream() {
    return {
      getAudioTracks() { return []; },
    };
  }
}

export function resetListeners() {
  listenerMap.clear();
}

export async function setupVoiceTestEnv() {
  resetListeners();
  windowStub.__askchip_config = {};
  windowStub.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
  windowStub.cancelAnimationFrame = (id) => clearTimeout(id);

  Object.defineProperty(globalThis, 'CustomEvent', { value: TestCustomEvent, configurable: true });
  Object.defineProperty(globalThis, 'window', { value: windowStub, configurable: true });
  Object.defineProperty(globalThis, 'performance', {
    value: { now: () => Date.now() },
    configurable: true,
  });
  Object.defineProperty(globalThis, 'navigator', {
    value: {
      mediaDevices: {
        async getUserMedia() { return fakeStream; },
      },
    },
    configurable: true,
  });
  Object.defineProperty(globalThis, '__TEST_WS_HOOKS', { value: {}, configurable: true });
  Object.defineProperty(globalThis, '__TEST_WS_MODULE', {
    value: await import('../ws_stub.mjs'),
    configurable: true,
  });
  Object.defineProperty(globalThis, 'AudioContext', { value: FakeAudioContext, configurable: true });
  Object.defineProperty(globalThis, 'webkitAudioContext', { value: FakeAudioContext, configurable: true });
  Object.defineProperty(globalThis, 'MediaRecorder', { value: FakeMediaRecorder, configurable: true });
  Object.defineProperty(globalThis, 'Audio', { value: FakeAudioElement, configurable: true });

  windowStub.AudioContext = FakeAudioContext;
  windowStub.webkitAudioContext = FakeAudioContext;
  windowStub.MediaRecorder = FakeMediaRecorder;
  windowStub.Audio = FakeAudioElement;
}
