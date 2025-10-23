(() => {
  const LEVELS = {
    info: {
      background: "rgba(36, 41, 58, 0.95)",
      color: "#f7fafc"
    },
    warning: {
      background: "rgba(217, 119, 6, 0.92)",
      color: "#0f0f0f"
    },
    danger: {
      background: "rgba(220, 38, 38, 0.92)",
      color: "#fff"
    }
  };

  const ERROR_CATALOG = {
    rate_limited: {
      tone: "toast",
      level: "warning",
      title: "Rate limit reached",
      body: "Too many concurrent connections."
    },
    provider_down: {
      tone: "toast",
      level: "warning",
      title: "Service unavailable",
      body: "A provider outage is preventing new sessions."
    },
    invalid_message: {
      tone: "toast",
      level: "danger",
      title: "Unsupported request",
      body: "The server rejected a message from the client."
    },
    auth_failed: {
      tone: "banner",
      level: "danger",
      title: "Authentication failed",
      body: "The access token is missing or invalid. Update your credentials and reload."
    },
    origin_blocked: {
      tone: "banner",
      level: "danger",
      title: "Origin blocked",
      body: "This origin is not authorized for AskChip. Use an approved domain or update the allow-list."
    },
    version_mismatch: {
      tone: "banner",
      level: "danger",
      title: "Client version mismatch",
      body: "Refresh the page to negotiate the chat.v2 protocol."
    },
    resume_invalid: {
      tone: "banner",
      level: "danger",
      title: "Resume token invalid",
      body: "The saved session can no longer be resumed. Start a new session."
    },
    schema_invalid: {
      tone: "banner",
      level: "danger",
      title: "Request schema invalid",
      body: "The client sent an unsupported payload. Reload and try again."
    }
  };

  let toastRoot = null;
  let bannerRoot = null;
  let styleTag = null;
  let rateLimitState = null;

  function ensureStyleTag() {
    if (styleTag) return;
    styleTag = document.createElement("style");
    styleTag.id = "ws-error-styles";
    styleTag.textContent = `
#toast-root.toast-container {
  position: fixed;
  bottom: 24px;
  right: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 4000;
  pointer-events: none;
}

#toast-root .toast {
  pointer-events: auto;
  min-width: 260px;
  max-width: 360px;
  padding: 14px 18px;
  border-radius: 12px;
  box-shadow: 0 18px 40px rgba(12, 14, 24, 0.35);
  font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: opacity 160ms ease;
}

#toast-root .toast.toast-exit {
  opacity: 0;
  transform: translateY(12px);
}

#toast-root .toast-title {
  font-weight: 600;
  font-size: 0.95rem;
}

#toast-root .toast-body {
  font-size: 0.88rem;
  line-height: 1.4;
}

#toast-root .toast-meta {
  font-size: 0.75rem;
  opacity: 0.85;
}

#toast-root .toast a {
  color: inherit;
  text-decoration: underline;
}

#wserror-banner-root {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  padding: 16px;
  z-index: 5000;
  pointer-events: none;
}

#wserror-banner-root .banner {
  pointer-events: auto;
  width: min(720px, 92vw);
  border-radius: 14px;
  padding: 16px 22px;
  box-shadow: 0 20px 45px rgba(12, 14, 24, 0.32);
  font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
  }

#wserror-banner-root .banner.toast-exit {
  opacity: 0;
  transform: translateY(-12px);
}

#wserror-banner-root .banner-title {
  font-weight: 700;
  font-size: 1.05rem;
}

#wserror-banner-root .banner-body {
  font-size: 0.95rem;
  line-height: 1.45;
}

#wserror-banner-root .banner-meta {
  font-size: 0.8rem;
  opacity: 0.9;
}
`;
    document.head.appendChild(styleTag);
  }

  function ensureRoots() {
    if (!toastRoot) {
      toastRoot = document.getElementById("toast-root");
      if (!toastRoot) {
        toastRoot = document.createElement("div");
        toastRoot.id = "toast-root";
        toastRoot.className = "toast-container";
        document.body.appendChild(toastRoot);
      }
    }
    if (!bannerRoot) {
      bannerRoot = document.getElementById("wserror-banner-root");
      if (!bannerRoot) {
        bannerRoot = document.createElement("div");
        bannerRoot.id = "wserror-banner-root";
        document.body.appendChild(bannerRoot);
      }
    }
  }

  function applyLevelStyles(element, level) {
    const palette = LEVELS[level] || LEVELS.info;
    element.style.background = palette.background;
    element.style.color = palette.color;
  }

  function removeElement(el) {
    if (!el || !el.parentNode) return;
    el.classList.add("toast-exit");
    setTimeout(() => {
      if (el.parentNode) {
        el.parentNode.removeChild(el);
      }
    }, 220);
  }

  function createToast({ title, body, meta, level = "info", persistent = false }) {
    ensureStyleTag();
    ensureRoots();
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "alert");
    applyLevelStyles(toast, level);

    if (title) {
      const heading = document.createElement("div");
      heading.className = "toast-title";
      heading.textContent = title;
      toast.appendChild(heading);
    }
    if (body) {
      const bodyEl = document.createElement("div");
      bodyEl.className = "toast-body";
      bodyEl.textContent = body;
      toast.appendChild(bodyEl);
    }
    if (meta) {
      const metaEl = document.createElement("div");
      metaEl.className = "toast-meta";
      if (meta instanceof Node) {
        metaEl.appendChild(meta);
      } else {
        metaEl.textContent = meta;
      }
      toast.appendChild(metaEl);
    }

    toastRoot.appendChild(toast);

    if (!persistent) {
      setTimeout(() => removeElement(toast), 6500);
    }

    return toast;
  }

  function createBanner({ code, title, body, meta, level = "danger" }) {
    ensureStyleTag();
    ensureRoots();
    let banner = bannerRoot.querySelector(`[data-error-code="${code}"]`);
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "banner";
      banner.dataset.errorCode = code;
      banner.setAttribute("role", "alert");
      bannerRoot.appendChild(banner);
    } else {
      banner.innerHTML = "";
    }

    applyLevelStyles(banner, level);

    const heading = document.createElement("div");
    heading.className = "banner-title";
    heading.textContent = title;
    banner.appendChild(heading);

    if (body) {
      const bodyEl = document.createElement("div");
      bodyEl.className = "banner-body";
      bodyEl.textContent = body;
      banner.appendChild(bodyEl);
    }

    const metaEl = document.createElement("div");
    metaEl.className = "banner-meta";
    metaEl.textContent = meta || `Error code: ${code}`;
    banner.appendChild(metaEl);

    return banner;
  }

  function clearRateLimitState(reason = "resolved") {
    if (!rateLimitState) return;
    if (rateLimitState.intervalId) {
      clearInterval(rateLimitState.intervalId);
    }
    if (rateLimitState.toast) {
      if (reason === "manual") {
        const body = rateLimitState.toast.querySelector(".toast-body");
        if (body) {
          body.textContent = "Retry cancelled.";
        }
        setTimeout(() => removeElement(rateLimitState.toast), 1800);
      } else {
        removeElement(rateLimitState.toast);
      }
    }
    rateLimitState = null;
  }

  function showRateLimitToast(frame, controls) {
    const retryIn = Number(frame.retry_in_ms);
    const descriptor = ERROR_CATALOG.rate_limited;
    const bodyText = descriptor.body;
    const toast = createToast({
      title: descriptor.title,
      body: bodyText,
      level: descriptor.level,
      persistent: true
    });

    const countdown = document.createElement("span");
    countdown.setAttribute("aria-live", "polite");
    const metaEl = document.createElement("div");
    metaEl.className = "toast-meta";
    metaEl.appendChild(countdown);
    toast.appendChild(metaEl);

    let scheduled = false;
    const callbacks = {
      onRetryStart: () => {
        countdown.textContent = "Retrying now…";
        if (rateLimitState && rateLimitState.intervalId) {
          clearInterval(rateLimitState.intervalId);
          rateLimitState.intervalId = null;
        }
      }
    };

    if (controls && typeof controls.scheduleRetry === "function" && Number.isFinite(retryIn) && retryIn > 0) {
      try {
        scheduled = controls.scheduleRetry(retryIn, callbacks);
      } catch (err) {
        console.warn("Failed to schedule auto-retry", err);
        scheduled = false;
      }
    }

    if (!scheduled) {
      countdown.textContent = "Retry not scheduled. Try reconnecting manually.";
      rateLimitState = { toast, intervalId: null };
      return;
    }

    const deadline = Date.now() + retryIn;

    function updateCountdown() {
      const remaining = Math.max(0, deadline - Date.now());
      const seconds = Math.ceil(remaining / 1000);
      if (seconds > 0) {
        countdown.textContent = `Retrying in ${seconds}s…`;
      } else {
        countdown.textContent = "Retrying now…";
        if (rateLimitState && rateLimitState.intervalId) {
          clearInterval(rateLimitState.intervalId);
          rateLimitState.intervalId = null;
        }
      }
    }

    updateCountdown();
    const intervalId = setInterval(updateCountdown, 1000);
    rateLimitState = { toast, intervalId };
  }

  function handleErrorFrame(frame, controls = {}) {
    if (!frame || frame.type !== "error") return;
    ensureStyleTag();
    ensureRoots();

    const code = typeof frame.code === "string" ? frame.code : "unknown";
    const descriptor = ERROR_CATALOG[code] || {
      tone: frame.retryable ? "toast" : "banner",
      level: frame.retryable ? "warning" : "danger",
      title: `Server error (${code})`,
      body: frame.message || "The server reported an unexpected error."
    };

    const body = frame.message && frame.message !== descriptor.body
      ? `${descriptor.body} ${frame.message}`.trim()
      : descriptor.body;

    if (descriptor.tone === "banner") {
      createBanner({
        code,
        title: descriptor.title,
        body,
        meta: frame.retryable ? "This error can be retried after addressing the issue." : undefined,
        level: descriptor.level
      });
      return;
    }

    if (code === "rate_limited" && Number.isFinite(Number(frame.retry_in_ms))) {
      showRateLimitToast(frame, controls);
      return;
    }

    createToast({
      title: descriptor.title,
      body,
      meta: `Error code: ${code}`,
      level: descriptor.level
    });
  }

  window.WSErrorUI = {
    handleFrame: handleErrorFrame,
    clearRateLimitToast(reason = "resolved") {
      clearRateLimitState(reason);
    },
    cancelRateLimitCountdown(reason = "manual") {
      clearRateLimitState(reason);
    }
  };
})();
