(() => {
  const KEY_CONFIG = [
    {
      key: "mode",
      label: "Mode",
      format(value) {
        if (typeof value === "string" && value.trim()) {
          return value;
        }
        return "—";
      }
    },
    {
      key: "allow_auto_vad",
      label: "Auto VAD",
      format(value) {
        if (typeof value === "boolean") {
          return value ? "On" : "Off";
        }
        return "—";
      }
    },
    {
      key: "barge_in_enabled",
      label: "Barge",
      format(value) {
        if (typeof value === "boolean") {
          return value ? "On" : "Off";
        }
        return "—";
      }
    }
  ];

  const CLASS_HIDDEN = "hidden";

  const currentPolicy = Object.create(null);
  const badgeRefs = new Map();

  let initialized = false;
  let host = null;
  let micGateBadge = null;
  let micGateActive = false;
  let micGateTimerId = null;
  let micGateHoldMs = 0;
  let bargeInEnabled = true;

  function ensureHost() {
    if (host && document.body.contains(host)) {
      return host;
    }
    const existing = document.querySelector("[data-policy-badges]");
    if (existing) {
      host = existing;
      return host;
    }
    const topbarLeft = document.querySelector(".topbar-left");
    if (!topbarLeft) {
      return null;
    }
    const container = document.createElement("div");
    container.dataset.policyBadges = "true";
    container.style.display = "flex";
    container.style.alignItems = "center";
    container.style.gap = "8px";
    container.style.flexWrap = "wrap";
    container.style.fontSize = "11px";
    container.style.fontWeight = "600";
    topbarLeft.appendChild(container);
    host = container;
    return host;
  }

  function styleBadge(el) {
    el.style.display = "inline-flex";
    el.style.alignItems = "center";
    el.style.gap = "4px";
    el.style.padding = "4px 10px";
    el.style.borderRadius = "999px";
    el.style.background = "rgba(255,255,255,0.08)";
    el.style.border = "1px solid rgba(255,255,255,0.12)";
    el.style.color = "var(--muted)";
    el.style.letterSpacing = "0.2px";
  }

  function ensureBadgeElements() {
    const container = ensureHost();
    if (!container) {
      return;
    }
    KEY_CONFIG.forEach(({ key }) => {
      if (badgeRefs.has(key)) {
        return;
      }
      const el = document.createElement("span");
      el.dataset.policyBadge = key;
      el.setAttribute("role", "status");
      styleBadge(el);
      container.appendChild(el);
      badgeRefs.set(key, el);
    });
  }

  function ensureMicGateBadge() {
    if (micGateBadge && document.body.contains(micGateBadge)) {
      return micGateBadge;
    }
    const statusContainer = document.querySelector(".chip-status");
    if (!statusContainer) {
      return null;
    }
    const badge = document.createElement("span");
    badge.dataset.micGate = "true";
    badge.textContent = "Mic gated (TTS active)";
    badge.setAttribute("role", "status");
    badge.classList.add(CLASS_HIDDEN);
    badge.style.marginLeft = "8px";
    badge.style.padding = "4px 10px";
    badge.style.borderRadius = "999px";
    badge.style.background = "rgba(255,91,110,0.16)";
    badge.style.border = "1px solid rgba(255,91,110,0.35)";
    badge.style.color = "var(--danger)";
    badge.style.fontSize = "12px";
    badge.style.fontWeight = "600";
    badge.style.letterSpacing = "0.2px";
    statusContainer.appendChild(badge);
    micGateBadge = badge;
    return micGateBadge;
  }

  function formatValue(key, value) {
    const config = KEY_CONFIG.find((item) => item.key === key);
    if (!config) {
      return "";
    }
    try {
      return config.format(value);
    } catch (err) {
      console.warn("PolicyBadges: failed to format value", key, err);
      return "—";
    }
  }

  function renderBadges() {
    ensureBadgeElements();
    KEY_CONFIG.forEach(({ key, label }) => {
      const el = badgeRefs.get(key);
      if (!el) {
        return;
      }
      const hasValue = Object.prototype.hasOwnProperty.call(currentPolicy, key);
      const value = hasValue ? currentPolicy[key] : undefined;
      el.textContent = `${label}: ${formatValue(key, value)}`;
    });
  }

  function clearMicGateTimer() {
    if (micGateTimerId) {
      clearTimeout(micGateTimerId);
      micGateTimerId = null;
    }
  }

  function hideMicGate() {
    const badge = ensureMicGateBadge();
    if (!badge) {
      return;
    }
    badge.classList.add(CLASS_HIDDEN);
    badge.setAttribute("aria-hidden", "true");
    micGateActive = false;
  }

  function showMicGate() {
    const badge = ensureMicGateBadge();
    if (!badge) {
      return;
    }
    clearMicGateTimer();
    badge.classList.remove(CLASS_HIDDEN);
    badge.removeAttribute("aria-hidden");
    micGateActive = true;
  }

  function scheduleMicGateRelease(delayMs) {
    clearMicGateTimer();
    if (!micGateActive) {
      return;
    }
    if (delayMs > 0) {
      micGateTimerId = setTimeout(() => {
        micGateTimerId = null;
        hideMicGate();
      }, delayMs);
    } else {
      hideMicGate();
    }
  }

  function handlePolicyInteraction(event) {
    const detail = event && event.detail;
    const policy = detail && detail.policy;
    if (!policy || typeof policy !== "object") {
      return;
    }
    let updated = false;
    KEY_CONFIG.forEach(({ key }) => {
      if (!Object.prototype.hasOwnProperty.call(policy, key)) {
        return;
      }
      currentPolicy[key] = policy[key];
      if (key === "barge_in_enabled" && typeof policy[key] === "boolean") {
        bargeInEnabled = policy[key];
      }
      updated = true;
    });
    if (updated) {
      renderBadges();
    }
    if (Object.prototype.hasOwnProperty.call(policy, "barge_in_enabled") && policy.barge_in_enabled !== false && micGateActive) {
      hideMicGate();
    }
  }

  function parseHoldMs(detail) {
    const hold = Number(detail && detail.post_hold_ms);
    if (Number.isFinite(hold) && hold > 0) {
      return hold;
    }
    return 0;
  }

  function handleTtsStart(event) {
    const detail = event && event.detail;
    micGateHoldMs = parseHoldMs(detail);
    if (bargeInEnabled === false) {
      showMicGate();
    }
  }

  function handleTtsEnd(event) {
    if (!micGateActive) {
      return;
    }
    const hold = parseHoldMs(event && event.detail);
    const delay = hold > 0 ? hold : micGateHoldMs;
    micGateHoldMs = 0;
    scheduleMicGateRelease(delay);
  }

  function reset() {
    clearMicGateTimer();
    hideMicGate();
    micGateHoldMs = 0;
    KEY_CONFIG.forEach(({ key }) => {
      delete currentPolicy[key];
    });
    bargeInEnabled = true;
    renderBadges();
  }

  const PolicyBadges = {
    init() {
      if (initialized) {
        return;
      }
      initialized = true;
      ensureBadgeElements();
      ensureMicGateBadge();
      renderBadges();
      window.addEventListener("policy.interaction", handlePolicyInteraction);
      window.addEventListener("tts.start", handleTtsStart);
      window.addEventListener("tts.end", handleTtsEnd);
      window.addEventListener("ws.close", reset);
    }
  };

  window.PolicyBadges = PolicyBadges;
})();
