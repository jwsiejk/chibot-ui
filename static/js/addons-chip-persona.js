/*! addons-chip-persona.js
 * Chip persona & Nebraska-flavored naturalization.
 * - Light-touch: occasionally adds a brief, human aside to spoken replies.
 * - Pairs with addons-conversation-tuning.js (which calls window.ac_naturalize if present).
 * - Safe: avoids errors/URLs and very short messages.
 */
(function () {
  "use strict";

  function pick(arr){ return arr[Math.floor(Math.random()*arr.length)]; }

  function naturalize(text) {
    try {
      var t = (text || "").toString();
      var lower = t.toLowerCase();

      // Skip if too short or appears to be an error/log dump
      if (t.length < 60) return t;
      if (/\b(error|failed|exception|stack|trace|http:\/\/|https:\/\/)\b/.test(lower)) return t;

      // Chance to add flavor (override with window.AC_PERSONA_FLAVOR_CHANCE)
      var chance = Number((window && window.AC_PERSONA_FLAVOR_CHANCE) || 0.15);
      if (!isFinite(chance) || chance <= 0) chance = 0.15;

      if (Math.random() < chance) {
        var asides = [
          "Back home in Nebraska we’d say: measure twice, cut once.",
          "As I like to say out on the acreage: simple beats clever.",
          "Like my John Deere 455 Diesel—smooth when it’s tuned right.",
          "Kinda like a buckeye: simple on the outside, rich in the middle."
        ];
        var aside = pick(asides);

        // Insert the aside just before closing punctuation (if any)
        if (/[.?!…]\s*$/.test(t)) {
          t = t.replace(/[.?!…]\s*$/, function (m) { return " — " + aside + m; });
        } else {
          t += " — " + aside;
        }
      }
      return t;
    } catch (_e) {
      return text;
    }
  }

  // Expose to addons-conversation-tuning
  try { window.ac_naturalize = naturalize; } catch (_) {}
})();
