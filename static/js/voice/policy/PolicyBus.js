let currentPolicy = null;
const listeners = new Set();

function freezePolicy(policy) {
  if (!policy || typeof policy !== 'object') {
    return null;
  }
  const snapshot = { ...policy };
  try {
    return Object.freeze(snapshot);
  } catch (err) {
    return snapshot;
  }
}

function notifyListeners(policy) {
  for (const listener of Array.from(listeners)) {
    if (typeof listener !== 'function') {
      listeners.delete(listener);
      continue;
    }
    try {
      listener(policy);
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('[voice][policy] listener error', err);
      }
    }
  }
}

const PolicyBus = {
  setPolicy(policy) {
    currentPolicy = freezePolicy(policy);
    notifyListeners(currentPolicy);
    return currentPolicy;
  },
  getPolicy() {
    return currentPolicy;
  },
  on(event, handler) {
    if (event !== 'policy' || typeof handler !== 'function') {
      return () => {};
    }
    listeners.add(handler);
    return () => {
      listeners.delete(handler);
    };
  },
  off(handler) {
    listeners.delete(handler);
  },
  clear() {
    listeners.clear();
    currentPolicy = null;
  },
};

export default PolicyBus;
export { PolicyBus };
