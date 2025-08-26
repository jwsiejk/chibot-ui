/*! addons-conversation-tuning.js
 * Speed up Chip's speech and ensure he completes a thought.
 * - Additive: no changes to existing functions required.
 * - Applies a playbackRate (default 1.18, configurable via window.AC_SPEECH_RATE).
 * - Wraps speakWithVisemes (if present) to lightly humanize and to finalize the thought
 *   with proper punctuation so Chip doesn't sound cut off.
 * - Keeps "Chip" persona intact; does not change endpoints or core logic.
 * - Guards against regressions: checks for existence before touching anything.
 */
(function () {
  "use strict";

  // ---- Config (override by setting window.AC_SPEECH_RATE before this file loads) ----
  var RATE = Number((window && window.AC_SPEECH_RATE) || 1.18); // 18% faster by default
  if (!isFinite(RATE) || RATE <= 0) RATE = 1.18;

  // ---- Helpers ----
  function _applyPlaybackRate(mediaEl) {
    if (!mediaEl) return;
    try {
      if (!mediaEl.__ac_rate_applied) {
        mediaEl.defaultPlaybackRate = RATE;
        mediaEl.playbackRate = RATE;
        // preserve pitch (browser-specific flags)
        if ("preservesPitch" in mediaEl) mediaEl.preservesPitch = true;
        if ("mozPreservesPitch" in mediaEl) mediaEl.mozPreservesPitch = true;
        if ("webkitPreservesPitch" in mediaEl) mediaEl.webkitPreservesPitch = true;
        mediaEl.__ac_rate_applied = true;
      }
    } catch (_) {}
  }

  // Patch media play() so *any* audio Chip plays uses the faster rate
  try {
    var _origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      _applyPlaybackRate(this);
      return _origPlay.apply(this, arguments);
    };
  } catch (_) {}

  // Ensure the utterance ends as a complete thought.
  function ac_finalizeThought(text) {
    try {
      if (!text) return text;
      var t = String(text).replace(/\s+/g, " ").trim();

      // If ends in a conjunction/fragments, add a clean finisher.
      if (/\b(?:and|but|so|because|which|that)\s*$/i.test(t)) {
        t += ", and that’s the key point.";
      }
      // If there's no closing punctuation, add one concise closer.
      if (!/[.?!…]\s*$/.test(t)) {
        var closers = [
          "That’s the gist.",
          "That’s the key idea.",
          "That’s the short version.",
          "That’s the takeaway."
        ];
        t += " " + closers[Math.floor(Math.random() * closers.length)];
      }
      return t;
    } catch (_) {
      return text;
    }
  }

  // Optional light humanization (if ac_naturalize exists we respect it)
  function ac_shaper(text) {
    var t = text || "";
    try {
      if (typeof window.ac_naturalize === "function") {
        t = window.ac_naturalize(t);
      }
      t = ac_finalizeThought(t);
      return t;
    } catch (_) {
      return t;
    }
  }

  // Teacher-style idle prompts (only if you already have an inactivity scheduler)
  (function augmentIdlePrompts() {
    try {
      if (!window._AC_IDLE_PROMPTS) {
        window._AC_IDLE_PROMPTS = [
          "Want more detail or should I keep it high level?",
          "Do you want a quick comparison to alternatives, or best practices on Pure?",
          "Is that enough for now, or should I unpack it a bit more?"
        ];
      } else {
        // Merge without duplicates
        var add = [
          "Want more detail or should I keep it high level?",
          "Do you want a quick comparison to alternatives, or best practices on Pure?",
          "Is that enough for now, or should I unpack it a bit more?"
        ];
        add.forEach(function (s) {
          if (window._AC_IDLE_PROMPTS.indexOf(s) === -1) window._AC_IDLE_PROMPTS.push(s);
        });
      }
    } catch (_) {}
  })();

  // Wrap speakWithVisemes (when available)
  (function waitAndWrapSpeak() {
    var attempts = 0;
    var iv = setInterval(function () {
      attempts += 1;
      if (attempts > 100) return clearInterval(iv); // give up after ~20s
      if (typeof window.speakWithVisemes === "function") {
        var _orig = window.speakWithVisemes;
        window.speakWithVisemes = async function (text /*, ...rest */) {
          var args = Array.prototype.slice.call(arguments);
          try {
            args[0] = ac_shaper(text);
          } catch (_) {}
          var res = await _orig.apply(this, args);
          // Re-arm inactivity if available so Chip can offer teacher-style follow-ups.
          try {
            if (typeof window.ac_scheduleInactivity === "function") {
              var first = typeof window._AC_IDLE_FIRST_MS !== "undefined" ? window._AC_IDLE_FIRST_MS : 9000;
              window.ac_scheduleInactivity(first);
            }
          } catch (_) {}
          return res;
        };
        clearInterval(iv);
      }
    }, 200);
  })();
})();