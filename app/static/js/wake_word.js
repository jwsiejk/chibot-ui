export const WakeWord = (() => {
  const listeners = new Set();
  let initialized = false;

  function init(_stream) {
    initialized = true;
  }

  function onHotword(cb) {
    listeners.add(cb);
  }

  window.__triggerHotword = () => {
    listeners.forEach((fn) => {
      try {
        fn();
      } catch (err) {
        console.warn("WakeWord listener error", err);
      }
    });
  };

  return {
    init,
    onHotword,
    get ready() {
      return initialized;
    }
  };
})();

if (typeof window !== "undefined") {
  window.WakeWord = WakeWord;
}
