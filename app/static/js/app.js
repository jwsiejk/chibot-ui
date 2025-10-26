(() => {
  const STATIC_JS_BASE = (() => {
    if (typeof document === "undefined") {
      return "/static/js/";
    }

    const script = document.currentScript || document.querySelector('script[src$="/static/js/app.js"]');
    if (script && script.src) {
      try {
        const url = new URL(script.src, window.location.href);
        return url.pathname.replace(/[^/]+$/, "");
      } catch (err) {
        console.warn("Failed to parse static script URL", err);
      }
    }

    return "/static/js/";
  })();

  const VERSION_QUERY = (() => {
    if (typeof document === "undefined") {
      return "";
    }
    try {
      const current =
        document.currentScript ||
        document.querySelector('script[src*="/static/js/app.js"]') ||
        null;
      if (!current || !current.src) {
        return "";
      }
      const url = new URL(current.src, window.location.href);
      return url.search || "";
    } catch (err) {
      console.warn("Failed to derive version query", err);
      return "";
    }
  })();

  function isSameOrigin(url) {
    try {
      if (!/^(?:[a-z]+:)?\/\//i.test(url)) {
        return true;
      }
      const parsed = new URL(url, window.location.href);
      return parsed.origin === window.location.origin;
    } catch (err) {
      return false;
    }
  }

  function isSpecialScheme(url) {
    return /^(data:|blob:)/i.test(url);
  }

  function appendVersionQuery(url) {
    if (!VERSION_QUERY || typeof url !== "string" || url.includes("?")) {
      return url;
    }
    const hashIndex = url.indexOf("#");
    if (hashIndex === -1) {
      return `${url}${VERSION_QUERY}`;
    }
    return `${url.slice(0, hashIndex)}${VERSION_QUERY}${url.slice(hashIndex)}`;
  }

  function resolveScriptSrc(src) {
    if (!src || typeof src !== "string") {
      return src;
    }

    if (isSpecialScheme(src)) {
      return src;
    }

    if (/^(?:[a-z]+:)?\/\//i.test(src) || src.startsWith("/")) {
      if (!isSameOrigin(src)) {
        return src;
      }
      return appendVersionQuery(src);
    }

    const normalized = src.replace(/^\.\//, "");
    const base = STATIC_JS_BASE.replace(/\/?$/, "/");
    const joined = `${base}${normalized}`;

    return appendVersionQuery(joined);
  }

  function loadScript(src) {
    const resolvedSrc = resolveScriptSrc(src);
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-dynamic="${resolvedSrc}"]`);
      if (existing) {
        if (existing.dataset.loaded === "true") {
          resolve();
        } else {
          existing.addEventListener("load", resolve, { once: true });
          existing.addEventListener("error", reject, { once: true });
        }
        return;
      }
      const el = document.createElement("script");
      el.src = resolvedSrc;
      el.async = false;
      el.dataset.dynamic = resolvedSrc;
      el.addEventListener("load", () => {
        el.dataset.loaded = "true";
        resolve();
      }, { once: true });
      el.addEventListener("error", reject, { once: true });
      document.head.appendChild(el);
    });
  }

  async function bootstrapClientConfig() {
    if (typeof window === "undefined") {
      return {};
    }
    const existing = window.__CFG__;
    if (existing && typeof existing === "object") {
      return existing;
    }
    try {
      const response = await fetch("/api/v1/admin/config", {
        method: "GET",
        credentials: "include"
      });
      if (response && response.ok) {
        try {
          const data = await response.json();
          if (data && typeof data === "object") {
            window.__CFG__ = data;
            return data;
          }
        } catch (err) {
          console.warn("Failed to parse admin config", err);
        }
      }
    } catch (err) {
      // Swallow network/config bootstrap failures silently; defaults will apply.
    }
    if (!window.__CFG__ || typeof window.__CFG__ !== "object") {
      window.__CFG__ = {};
    }
    return window.__CFG__;
  }

  function diagHudEnabled() {
    if (typeof window === "undefined") {
      return true;
    }
    const cfg = window.__CFG__;
    if (cfg && typeof cfg.DIAG_CLIENT_HUD === "boolean") {
      return cfg.DIAG_CLIENT_HUD;
    }
    return true;
  }

  function diagChunkSampleN() {
    if (typeof window === "undefined") {
      return 10;
    }
    const cfg = window.__CFG__;
    const candidate = cfg ? Number(cfg.DIAG_CHUNK_SAMPLE_N) : NaN;
    if (Number.isFinite(candidate) && candidate > 0) {
      return Math.floor(candidate);
    }
    return 10;
  }

  let diagBadgeEl = null;
  let diagBadgePendingText = null;
  let diagBadgeAwaitingDom = false;

  function ensureDiagBadge() {
    if (!diagHudEnabled()) {
      return null;
    }
    if (typeof document === "undefined") {
      return null;
    }
    if (diagBadgeEl && diagBadgeEl.isConnected) {
      return diagBadgeEl;
    }
    const existing = document.getElementById("diagBadge");
    if (existing) {
      diagBadgeEl = existing;
      if (diagBadgePendingText != null) {
        existing.textContent = diagBadgePendingText;
        diagBadgePendingText = null;
      }
      return existing;
    }
    if (!document.body) {
      if (!diagBadgeAwaitingDom) {
        diagBadgeAwaitingDom = true;
        document.addEventListener("DOMContentLoaded", () => {
          diagBadgeAwaitingDom = false;
          const badge = ensureDiagBadge();
          if (badge && diagBadgePendingText != null) {
            badge.textContent = diagBadgePendingText;
            diagBadgePendingText = null;
          }
        }, { once: true });
      }
      return null;
    }
    const badge = document.createElement("div");
    badge.id = "diagBadge";
    badge.setAttribute("role", "status");
    const style = badge.style;
    style.position = "fixed";
    style.right = "12px";
    style.bottom = "12px";
    style.padding = "6px 10px";
    style.borderRadius = "10px";
    style.background = "rgba(17,24,39,0.78)";
    style.color = "#f8fafc";
    style.font = "12px/1.4 system-ui, -apple-system, \"Segoe UI\", sans-serif";
    style.zIndex = "5000";
    style.pointerEvents = "none";
    style.boxShadow = "0 6px 24px rgba(15, 23, 42, 0.25)";
    badge.textContent = diagBadgePendingText != null ? diagBadgePendingText : "diag";
    diagBadgePendingText = null;
    document.body.appendChild(badge);
    diagBadgeEl = badge;
    return badge;
  }

  function setBadge(text) {
    if (!diagHudEnabled()) {
      return;
    }
    const value = text == null ? "" : String(text);
    const badge = ensureDiagBadge();
    if (badge) {
      badge.textContent = value;
      diagBadgePendingText = null;
    } else {
      diagBadgePendingText = value;
    }
  }

  async function getMicStream() {
    if (!navigator || !navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      throw new Error("mediaDevices.getUserMedia unavailable");
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (diagHudEnabled()) {
      const tracks = typeof stream.getAudioTracks === "function"
        ? stream.getAudioTracks().map((track) => ({ label: track.label, enabled: track.enabled }))
        : [];
      console.info("DIAG_MEDIA_OK", tracks);
      setBadge("mic:ok");
    }
    return stream;
  }

  function attachRecorder(stream, onChunk) {
    if (!stream) {
      throw new Error("MediaStream required");
    }
    if (typeof MediaRecorder === "undefined") {
      throw new Error("MediaRecorder unsupported");
    }
    const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    let sentBytes = 0;
    let chunkCount = 0;
    const sampleEvery = Math.max(1, diagChunkSampleN());
    recorder.addEventListener("start", () => {
      if (diagHudEnabled()) {
        console.info("DIAG_RECORDER_STARTED", Date.now());
        setBadge("rec:start");
      }
    });
    recorder.addEventListener("dataavailable", async (event) => {
      if (!event.data || !event.data.size) {
        return;
      }
      chunkCount += 1;
      const shouldSample = chunkCount % sampleEvery === 0;
      if (diagHudEnabled() && shouldSample) {
        console.debug("DIAG_CHUNK", { size: event.data.size, chunkCount });
        setBadge(`chunks:${chunkCount}`);
      }
      try {
        const buffer = await event.data.arrayBuffer();
        if (typeof onChunk === "function") {
          onChunk(buffer);
        }
        sentBytes += buffer.byteLength;
        if (diagHudEnabled() && shouldSample) {
          console.debug("DIAG_WS_BIN_SENT", { chunkCount, sentBytes });
        }
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_CHUNK_ERROR", err);
        }
      }
    });
    return recorder;
  }

  const DiagRecorder = (() => {
    let stream = null;
    let recorder = null;
    let startPromise = null;

    function cleanupStream() {
      if (!stream) {
        return;
      }
      try {
        const tracks = typeof stream.getTracks === "function" ? stream.getTracks() : [];
        tracks.forEach((track) => {
          try {
            track.stop();
          } catch (err) {
            if (diagHudEnabled()) {
              console.warn("DIAG_TRACK_STOP_ERROR", err);
            }
          }
        });
      } finally {
        stream = null;
      }
    }

    function stop(reason) {
      if (recorder) {
        try {
          if (recorder.state !== "inactive") {
            recorder.stop();
          }
        } catch (err) {
          if (diagHudEnabled()) {
            console.warn("DIAG_RECORDER_STOP_ERROR", err);
          }
        }
        recorder = null;
      }
      cleanupStream();
      if (diagHudEnabled()) {
        if (reason) {
          setBadge(`stop:${reason}`);
        } else {
          setBadge("stop");
        }
      }
    }

    async function startInternal(trigger) {
      if (!diagHudEnabled()) {
        return null;
      }
      if (recorder && recorder.state !== "inactive") {
        return recorder;
      }
      const wsClient = window.WSClient;
      const ws = wsClient && wsClient.socket;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return null;
      }
      try {
        stream = await getMicStream();
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_MEDIA_ERROR", err);
          setBadge("mic:fail");
        }
        stream = null;
        return null;
      }
      try {
        recorder = attachRecorder(stream, (buffer) => {
          if (!ws || ws.readyState !== WebSocket.OPEN) {
            return;
          }
          try {
            ws.send(buffer);
          } catch (err) {
            if (diagHudEnabled()) {
              console.warn("DIAG_WS_SEND_ERROR", err);
            }
          }
        });
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_RECORDER_ERROR", err);
          setBadge("rec:fail");
        }
        cleanupStream();
        recorder = null;
        return null;
      }
      recorder.addEventListener("stop", () => {
        if (diagHudEnabled()) {
          setBadge("rec:stop");
        }
      });
      try {
        recorder.start(250);
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_RECORDER_START_ERROR", err);
          setBadge("rec:fail");
        }
        stop("start_err");
        return null;
      }
      if (diagHudEnabled()) {
        setBadge(trigger ? `rec:${trigger}` : "rec");
      }
      return recorder;
    }

    function maybeStart(trigger) {
      if (!diagHudEnabled()) {
        return;
      }
      if (recorder && recorder.state !== "inactive") {
        return;
      }
      if (startPromise) {
        return;
      }
      const wsClient = window.WSClient;
      const ws = wsClient && wsClient.socket;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return;
      }
      const promise = startInternal(trigger);
      if (!promise) {
        return;
      }
      startPromise = promise;
      promise.finally(() => {
        if (startPromise === promise) {
          startPromise = null;
        }
      }).catch(() => {
        if (diagHudEnabled()) {
          console.warn("DIAG_START_REJECTED");
        }
      });
    }

    return {
      maybeStart,
      stop,
      hudEnabled: diagHudEnabled
    };
  })();

  async function ensureRuntimeModules() {
    if (!window.AppState) {
      await loadScript("state.js");
    }
    if (!window.AudioPlayer) {
      await loadScript("audio_player.js");
    }
    if (!window.WSClient) {
      await loadScript("ws_client.js");
    }
    if (!window.AudioRecorder) {
      await loadScript("audio_recorder.js");
    }
    if (!window.PolicyBadges) {
      await loadScript("policy_badges.js");
    }
    if (!window.TranscriptView) {
      await loadScript("transcript_view.js");
    }
    if (!window.WSErrorUI) {
      await loadScript("errors.js");
    }
  }

  async function init() {
    await bootstrapClientConfig();
    await ensureRuntimeModules();

    const urlParams = new URLSearchParams(window.location ? window.location.search : '');
    const AppState = window.AppState;
    const WSClient = window.WSClient;
    if (window.PolicyBadges && typeof window.PolicyBadges.init === "function") {
      try {
        window.PolicyBadges.init();
      } catch (err) {
        console.warn("Failed to initialize PolicyBadges", err);
      }
    }

    // --- App context (server-injected) ---
    function readAppContext() {
      const node = document.getElementById('appContext');
      if (!node) return {};
      try {
        const raw = node.textContent || node.innerText || '{}';
        return JSON.parse(raw);
      } catch (err) {
        console.warn('Failed to parse app context', err);
        return {};
      }
    }

    const serverContext = readAppContext();
    const currentUserEmail = typeof serverContext.userEmail === 'string' && serverContext.userEmail
      ? serverContext.userEmail
      : 'user@example.com';
    const isAdmin = Boolean(serverContext.isAdmin);
    window.__ASKCHIP_CTX__ = Object.freeze({
      isAdmin,
      userEmail: currentUserEmail,
    });

    // --- Top-right brand dropdown ---
    const brandBtn = document.getElementById('brandBtn');
    const brandMenu = document.getElementById('brandMenu');
    const adminItem = document.getElementById('adminItem');
    brandMenu.setAttribute('aria-hidden', 'true');
    function syncAdminItem() {
      if (isAdmin) {
        adminItem.removeAttribute('aria-disabled');
        adminItem.disabled = false;
        adminItem.tabIndex = 0;
        adminItem.title = "Open Admin UI";
      } else {
        adminItem.setAttribute('aria-disabled', 'true');
        adminItem.disabled = true;
        adminItem.tabIndex = -1;
        adminItem.title = "Admins only";
      }
    }
    syncAdminItem();
    function closeMenu() {
      brandMenu.classList.remove('open');
      brandMenu.setAttribute('aria-hidden', 'true');
      brandBtn.setAttribute('aria-expanded', 'false');
    }
    brandBtn.addEventListener('click', (e) => {
      const open = brandMenu.classList.toggle('open');
      brandBtn.setAttribute('aria-expanded', String(open));
      brandMenu.setAttribute('aria-hidden', String(!open));
      if (!open) return;
      const firstItem = brandMenu.querySelector('.menu-item:not([aria-disabled="true"])');
      if (firstItem) firstItem.focus();
    });
    document.addEventListener('click', (e) => {
      if (!brandMenu.contains(e.target) && !brandBtn.contains(e.target)) closeMenu();
    });
    brandMenu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeMenu();
        brandBtn.focus();
      }
    });
    adminItem.addEventListener('click', (event) => {
      if (!isAdmin) {
        event.preventDefault();
        return;
      }
      window.location.href = '/admin/logs';
      closeMenu();
    });
    document.getElementById('profileItem').addEventListener('click', () => {
      alert(`Profile for ${currentUserEmail} (placeholder).`); closeMenu();
    });
    document.getElementById('logoutItem').addEventListener('click', () => {
      alert("Logging out… (placeholder)"); closeMenu();
    });    

    // --- Chat toggle ---
    const openChatBtn = document.getElementById('openChatBtn');
    const chatPanel = document.querySelector('.chat');
    openChatBtn.addEventListener('click', () => {
      const hidden = chatPanel.classList.toggle('hidden');
      openChatBtn.setAttribute('aria-pressed', String(!hidden));
    });

    // --- Start/End button wiring ---
    const startBtn = document.getElementById('startBtn');
    const endBtn = document.getElementById('endBtn');
    const sidText = document.getElementById('sid-text');
    const connLabel = document.getElementById('connLabel');
    const statusDot = document.querySelector('.status-dot');
    const latencyHint = document.getElementById('latencyHint');
    const voiceLabel = document.getElementById('voiceLabel');
    const localeLabel = document.getElementById('localeLabel');
    const textChatForm = document.getElementById('textChatForm');
    const textChatInput = document.getElementById('textChatInput');

    const voiceState = { voiceId: null, locale: null };

    const modeSuggestionsEl = document.getElementById('modeSuggestions');
    const modeSuggestionsLabelEl = document.getElementById('modeSuggestionsLabel');
    const modeSuggestionsChipsEl = document.getElementById('modeSuggestionsChips');
    const modeSuggestionsState = { mode: null, items: [] };

    function formatModeLabel(value) {
      if (typeof value !== 'string') return '';
      const normalized = value.trim().replace(/[_\s]+/g, ' ');
      if (!normalized) return '';
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }

    function resetSuggestions() {
      modeSuggestionsState.mode = null;
      modeSuggestionsState.items = [];
      renderSuggestions();
    }

    function renderSuggestions() {
      if (!modeSuggestionsEl || !modeSuggestionsChipsEl) {
        return;
      }
      const items = Array.isArray(modeSuggestionsState.items)
        ? modeSuggestionsState.items
        : [];
      const hasItems = items.length > 0;
      modeSuggestionsEl.classList.toggle('hidden', !hasItems);
      modeSuggestionsChipsEl.replaceChildren();
      if (!hasItems) {
        if (modeSuggestionsLabelEl) {
          modeSuggestionsLabelEl.textContent = '';
        }
        return;
      }
      for (const label of items) {
        const chip = document.createElement('span');
        chip.className = 'mode-chip';
        chip.textContent = label;
        chip.setAttribute('role', 'listitem');
        modeSuggestionsChipsEl.appendChild(chip);
      }
      if (modeSuggestionsLabelEl) {
        const formatted = formatModeLabel(modeSuggestionsState.mode);
        modeSuggestionsLabelEl.textContent = formatted
          ? `${formatted} suggestions`
          : 'Suggested actions';
      }
    }

    function applySuggestionsFrame(detail) {
      if (!detail || typeof detail !== 'object') {
        resetSuggestions();
        return;
      }
      const rawItems = Array.isArray(detail.items) ? detail.items : [];
      const cleaned = [];
      for (const item of rawItems) {
        if (!item || typeof item !== 'object') continue;
        const label = typeof item.label === 'string' ? item.label.trim() : '';
        if (!label) continue;
        cleaned.push(label);
      }
      modeSuggestionsState.mode = typeof detail.mode === 'string' ? detail.mode : null;
      modeSuggestionsState.items = cleaned;
      renderSuggestions();
    }

    function renderVoiceState() {
      if (voiceLabel) {
        voiceLabel.textContent = voiceState.voiceId || '—';
      }
      if (localeLabel) {
        localeLabel.textContent = voiceState.locale || '—';
      }
    }

    function resetVoiceState() {
      voiceState.voiceId = null;
      voiceState.locale = null;
      renderVoiceState();
    }

    function updateVoiceState(partial) {
      if (!partial || typeof partial !== 'object') {
        return;
      }
      let updated = false;
      if (typeof partial.voiceId === 'string') {
        const trimmed = partial.voiceId.trim();
        if (trimmed && trimmed !== voiceState.voiceId) {
          voiceState.voiceId = trimmed;
          updated = true;
        }
      }
      if (typeof partial.locale === 'string') {
        const trimmed = partial.locale.trim();
        if (trimmed && trimmed !== voiceState.locale) {
          voiceState.locale = trimmed;
          updated = true;
        }
      }
      if (updated) {
        renderVoiceState();
      }
    }

    function extractVoiceLocale(frame) {
      if (!frame || typeof frame !== 'object') {
        return {};
      }
      let voiceId = null;
      let locale = null;
      if (typeof frame.voice_id === 'string') {
        voiceId = frame.voice_id;
      }
      if (typeof frame.locale === 'string') {
        locale = frame.locale;
      }
      const meta = frame.meta && typeof frame.meta === 'object' ? frame.meta : null;
      if (meta) {
        if (!voiceId && typeof meta.voice_id === 'string') {
          voiceId = meta.voice_id;
        }
        if (!locale && typeof meta.locale === 'string') {
          locale = meta.locale;
        }
        const ttsMeta = meta.tts && typeof meta.tts === 'object' ? meta.tts : null;
        if (ttsMeta) {
          if (!voiceId && typeof ttsMeta.voice_id === 'string') {
            voiceId = ttsMeta.voice_id;
          }
          if (!locale && typeof ttsMeta.locale === 'string') {
            locale = ttsMeta.locale;
          }
        }
      }
      return { voiceId, locale };
    }

    renderVoiceState();
    renderSuggestions();

    let pttEnabled = false;
    let pttActive = false;
    let pttMaskState = null;

    function applyPttMask({ force = false } = {}) {
      if (!window.AudioRecorder || typeof window.AudioRecorder._setMask !== 'function') {
        return;
      }
      const shouldMask = pttEnabled && !pttActive;
      if (force || pttMaskState !== shouldMask) {
        try {
          window.AudioRecorder._setMask(shouldMask);
          pttMaskState = shouldMask;
        } catch (err) {
          console.warn('Failed to toggle push-to-talk mask', err);
        }
      }
    }

    function setPttEnabled(enabled) {
      if (pttEnabled === enabled) {
        applyPttMask({ force: true });
        return;
      }
      pttEnabled = enabled;
      if (!enabled && pttActive) {
        pttActive = false;
        if (document.body) {
          document.body.classList.remove('ptt-hold');
        }
      }
      pttMaskState = null;
      applyPttMask({ force: true });
    }

    function setPttActive(active) {
      if (!pttEnabled) {
        active = false;
      }
      if (pttActive === active) {
        return;
      }
      pttActive = active;
      if (document.body) {
        document.body.classList.toggle('ptt-hold', pttActive);
      }
      applyPttMask({ force: true });
    }

    function isInteractiveTarget(target) {
      if (!target || target === document.body || target === document.documentElement) {
        return false;
      }
      if (target.isContentEditable) {
        return true;
      }
      if (!(target instanceof HTMLElement)) {
        return false;
      }
      const tag = target.tagName;
      if (tag) {
        const normalized = tag.toUpperCase();
        if (normalized === 'INPUT' || normalized === 'TEXTAREA' || normalized === 'SELECT' || normalized === 'BUTTON') {
          return true;
        }
      }
      if (target.closest('input, textarea, select, button, a[href], [role="textbox"], [role="button"], [role="menuitem"]')) {
        return true;
      }
      return false;
    }

    function handleGlobalKeyDown(event) {
      if (event.defaultPrevented) return;
      const { key, code, ctrlKey, metaKey, altKey, repeat } = event;
      const isModifier = ctrlKey || metaKey || altKey;
      if ((key === ' ' || key === 'Spacebar' || code === 'Space') && !isModifier) {
        if (repeat || isInteractiveTarget(event.target)) {
          return;
        }
        if (!pttEnabled) {
          return;
        }
        event.preventDefault();
        setPttActive(true);
        return;
      }
      if (key === 'Enter' && !isModifier) {
        if (isInteractiveTarget(event.target)) {
          return;
        }
        if (textChatInput) {
          const value = textChatInput.value || '';
          if (value.trim()) {
            event.preventDefault();
            if (textChatForm && typeof textChatForm.requestSubmit === 'function') {
              textChatForm.requestSubmit();
            } else if (textChatForm) {
              textChatForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
          } else {
            event.preventDefault();
            textChatInput.focus();
          }
        }
        return;
      }
      if (key === 'Escape') {
        if (brandMenu && brandMenu.classList.contains('open')) {
          event.preventDefault();
          closeMenu();
          brandBtn.focus();
          return;
        }
        if (isInteractiveTarget(event.target)) {
          return;
        }
        const audioPlayer = window.AudioPlayer;
        if (audioPlayer && typeof audioPlayer.interrupt === 'function') {
          audioPlayer.interrupt();
        }
        return;
      }
    }

    function handleGlobalKeyUp(event) {
      const { key, code, ctrlKey, metaKey, altKey } = event;
      if ((key === ' ' || key === 'Spacebar' || code === 'Space') && !(ctrlKey || metaKey || altKey)) {
        if (!pttActive) {
          return;
        }
        event.preventDefault();
        setPttActive(false);
      }
    }

    window.addEventListener('keydown', handleGlobalKeyDown, true);
    window.addEventListener('keyup', handleGlobalKeyUp, true);
    window.addEventListener('blur', () => {
      if (pttActive) {
        setPttActive(false);
      }
    });

    const showToastMessage = (() => {
      let root = null;
      const styleId = 'inline-toast-styles';
      const styleText = '#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}';
      function ensureRoot() {
        root = root && root.isConnected ? root : document.getElementById('toast-root');
        if (!root) {
          root = document.createElement('div');
          root.id = 'toast-root';
          root.className = 'toast-container';
          document.body.appendChild(root);
        }
        if (!document.getElementById(styleId)) {
          document.head.appendChild(Object.assign(document.createElement('style'), { id: styleId, textContent: styleText }));
        }
        return root;
      }
      return (message) => {
        if (!message) return;
        const host = ensureRoot();
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.setAttribute('role', 'alert'); toast.innerHTML = '<div class="toast-body"></div>';
        toast.firstChild.textContent = message;
        host.appendChild(toast);
        setTimeout(() => {
          toast.classList.add('toast-exit');
          setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 220);
        }, 3600);
      };
    })();

    function csrf() {
      const cookies = document.cookie ? document.cookie.split(';') : [];
      for (const name of ['askchip_csrf', 'csrftoken', 'csrf_token']) {
        const prefix = `${name}=`;
        for (const part of cookies) {
          const trimmed = part.trim();
          if (!trimmed.startsWith(prefix)) continue;
          const raw = trimmed.slice(prefix.length);
          if (!raw) continue;
          try { return decodeURIComponent(raw); } catch (err) {
            console.warn('Failed to decode CSRF cookie', err);
            return raw;
          }
        }
      }
      return '';
    }

    async function getMe() {
      try {
        const r = await fetch('/api/v1/auth/me', { method: 'GET', credentials: 'include' });
        if (!r.ok) return { authenticated: false };
        return await r.json();
      } catch {
        return { authenticated: false };
      }
    }

    async function mintWsToken() {
      const headers = {};
      if (typeof csrf === 'function') headers['X-CSRF-Token'] = csrf();
      try {
        const r = await fetch('/api/v1/auth/ws-token', {
          method: 'POST',
          headers,
          credentials: 'include',
        });
        if (!r.ok) {
          return { ok: false, status: r.status };
        }
        try {
          const body = await r.json();
          return { ok: true, body };
        } catch (err) {
          console.error('Failed to parse ws-token response', err);
          return { ok: false, status: r.status };
        }
      } catch (err) {
        console.error('Failed to mint ws-token', err);
        return { ok: false, status: 0 };
      }
    }

    function rememberToken(sid, ttl_ms) {
      if (!window.AppState || typeof window.AppState.setState !== 'function') return;
      window.AppState.setState({
        sid,
        wsTokenIssuedAt: Date.now(),
        wsTokenTTL: ttl_ms
      });
    }

    function tokenFreshEnough() {
      const s = window.AppState && window.AppState.getState ? window.AppState.getState() : null;
      if (!s || !s.wsTokenIssuedAt || !s.wsTokenTTL) return false;
      const age = Date.now() - s.wsTokenIssuedAt;
      return age <= (s.wsTokenTTL - 1500);
    }

    function showStartToast(msg) {
      if (!msg) return;
      if (typeof showToast === 'function') {
        showToast(msg);
      } else if (typeof showToastMessage === 'function') {
        showToastMessage(msg);
      } else if (typeof alert === 'function') {
        alert(msg);
      } else {
        console.warn('Toast:', msg);
      }
    }

    function showLoginModal() {
      if (window.AuthUI && typeof window.AuthUI.showLoginModal === 'function') {
        window.AuthUI.showLoginModal();
      }
    }

    function showProfileModal() {
      if (window.AuthUI && typeof window.AuthUI.showProfileModal === 'function') {
        window.AuthUI.showProfileModal();
      }
    }

    async function handleStartSessionClick() {
      try {
        const me = await getMe();
        if (!me.authenticated) {
          if (typeof showLoginModal === 'function') showLoginModal();
          showStartToast('Please login first.');
          return;
        }
        if (me.profile_complete === false) {
          if (typeof showProfileModal === 'function') showProfileModal();
          showStartToast('Complete your profile to continue.');
          return;
        }
      } catch {
      }

      // 1) If a previously minted token is still fresh, you MAY skip re-minting.
      // For reliability we mint a fresh token every Start click. If you prefer
      // to skip re-minting when fresh, wrap mint with `if (!tokenFreshEnough()) {...}`
      const minted = await mintWsToken();
      if (!minted.ok) {
        if (minted.status === 401 || minted.status === 403) {
          showStartToast('Please login and complete your profile.');
        } else if (minted.status === 409) {
          showStartToast('Profile required. Please complete your profile.');
        } else {
          showStartToast('Could not start session. Try again.');
        }
        return;
      }

      const { access_token, sid, ttl_ms } = minted.body || {};
      if (!access_token || !sid || !ttl_ms) {
        console.error('ws-token response missing fields', minted.body);
        showStartToast('Could not start session. Try again.');
        return;
      }

      const ttlValue = Number(ttl_ms);
      if (!Number.isFinite(ttlValue) || ttlValue <= 0) {
        console.error('ws-token ttl invalid', ttl_ms);
        showStartToast('Could not start session. Try again.');
        return;
      }

      rememberToken(sid, ttlValue);

      if (!tokenFreshEnough()) {
        console.error('ws-token considered stale', { sid, ttl_ms: ttlValue });
        showStartToast('Could not start session. Try again.');
        return;
      }

      const params = new URLSearchParams({ access_token });
      const state = AppState && typeof AppState.getState === 'function' ? AppState.getState() : null;
      if (state && state.resume && typeof state.resume.token === 'string') {
        const resume = state.resume;
        if (Number.isFinite(resume.expiresAt) && Date.now() < resume.expiresAt) {
          params.set('resume', resume.token);
        }
      }
      const wsUrl = `/ws/v2/chat?${params.toString()}`;

      // Add token as SECOND subprotocol so the server can read it if a proxy
      // or any client path drops the query string.
      const subprotocols = ['chat.v2', `jwt.${access_token}`];

      console.log('evt=ws_open_attempt', { url: wsUrl, protocols: subprotocols, sid });

      try {
        WSClient.open(wsUrl, subprotocols);
      } catch (err) {
        console.error('WSClient.open failed', err);
        if (AppState && typeof AppState.setState === 'function') {
          AppState.setState({ connectionState: 'disconnected' });
        }
        showStartToast('Could not open a session. Please try again.');
      }
    }

    startBtn.addEventListener('click', (event) => {
      event.preventDefault();
      if (startBtn.disabled) return;
      startBtn.disabled = true;
      Promise.resolve(handleStartSessionClick()).finally(() => {
        startBtn.disabled = false;
      });
    });

    endBtn.addEventListener('click', () => {
      WSClient.close('user_requested');
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      if (window.AudioRecorder) {
        try {
          window.AudioRecorder.stop();
        } catch (err) {
          console.warn('Failed to stop audio recorder', err);
        }
      }
      if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
        window.AudioPlayer.interrupt();
      }
    });

    // --- Resume banner ---
    const resumeBanner = document.createElement('div');
    resumeBanner.setAttribute('role', 'status');
    resumeBanner.setAttribute('aria-live', 'polite');
    resumeBanner.style.position = 'fixed';
    resumeBanner.style.top = '16px';
    resumeBanner.style.right = '16px';
    resumeBanner.style.display = 'none';
    resumeBanner.style.alignItems = 'center';
    resumeBanner.style.gap = '12px';
    resumeBanner.style.padding = '10px 14px';
    resumeBanner.style.borderRadius = '10px';
    resumeBanner.style.background = 'rgba(12, 19, 35, 0.92)';
    resumeBanner.style.color = '#fff';
    resumeBanner.style.fontSize = '13px';
    resumeBanner.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.25)';
    resumeBanner.style.zIndex = '1000';
    resumeBanner.style.backdropFilter = 'blur(8px)';
    resumeBanner.style.webkitBackdropFilter = 'blur(8px)';

    const resumeText = document.createElement('span');
    resumeText.textContent = '';

    const resumeAction = document.createElement('button');
    resumeAction.type = 'button';
    resumeAction.textContent = 'Start new session';
    resumeAction.style.background = '#2251ff';
    resumeAction.style.color = '#fff';
    resumeAction.style.border = 'none';
    resumeAction.style.borderRadius = '6px';
    resumeAction.style.padding = '6px 10px';
    resumeAction.style.fontSize = '12px';
    resumeAction.style.cursor = 'pointer';
    resumeAction.style.fontWeight = '600';

    resumeBanner.append(resumeText, resumeAction);
    document.body.appendChild(resumeBanner);

    let resumeBannerMode = 'hidden';
    let resumeCountdownId = null;

    function stopResumeCountdown() {
      if (resumeCountdownId) {
        clearInterval(resumeCountdownId);
        resumeCountdownId = null;
      }
    }

    function hideResumeBanner() {
      if (resumeBannerMode === 'hidden') return;
      stopResumeCountdown();
      resumeBannerMode = 'hidden';
      resumeBanner.style.display = 'none';
    }

    function renderResumeCountdown() {
      const state = AppState.getState();
      const resume = state && typeof state.resume === 'object' ? state.resume : null;
      if (!resume || !Number.isFinite(resume.expiresAt)) {
        return false;
      }
      const remainingMs = Math.max(0, resume.expiresAt - Date.now());
      const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
      resumeText.textContent = `Reconnecting… (${seconds}s)`;
      return true;
    }

    function showResumeCountdown() {
      if (!renderResumeCountdown()) {
        hideResumeBanner();
        return;
      }
      if (resumeBannerMode !== 'countdown') {
        stopResumeCountdown();
        resumeBannerMode = 'countdown';
        resumeBanner.style.display = 'flex';
        resumeAction.disabled = false;
        resumeCountdownId = setInterval(() => {
          if (!renderResumeCountdown()) {
            hideResumeBanner();
          }
        }, 1000);
      }
    }

    function showResumeError() {
      stopResumeCountdown();
      resumeBannerMode = 'error';
      resumeBanner.style.display = 'flex';
      resumeText.textContent = 'Session resume unavailable. Start a new session to continue.';
      resumeAction.disabled = false;
    }

    function updateResumeBanner(state) {
      const hasCountdown = state.connectionState === 'resuming' && state.resume && Number.isFinite(state.resume.expiresAt);
      if (hasCountdown) {
        showResumeCountdown();
        return;
      }
      if (state.resumeError === 'invalid') {
        showResumeError();
        return;
      }
      hideResumeBanner();
    }

    resumeAction.addEventListener('click', () => {
      resumeAction.disabled = true;
      try {
        WSClient.close('user_restart');
      } catch (err) {
        console.warn('Resume banner close failed', err);
      }
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      AppState.setState({ resumeError: null });
      Promise.resolve(handleStartSessionClick()).catch((err) => {
        console.error('Failed to start new session', err);
        AppState.setState({ connectionState: 'disconnected' });
      }).finally(() => {
        resumeAction.disabled = false;
      });
    });

    // --- Waveform visual inside the Chip window ---
    const Waveform = (() => {
      const canvas = document.getElementById('waveCanvas');
      const ctx = canvas.getContext('2d', {alpha:false});
      let raf = 0, analyser = null, source = null, audioCtx = null, dataArray = null;
      let synthMode = true, t = 0;

      function resizeCanvas(){
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        canvas.width = canvas.clientWidth * dpr;
        canvas.height = canvas.clientHeight * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      window.addEventListener('resize', resizeCanvas, {passive:true});
      resizeCanvas();

      function drawBackground(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const g = ctx.createLinearGradient(0,0,w,h);
        g.addColorStop(0,'#0b1222'); g.addColorStop(1,'#0a0f1b');
        ctx.fillStyle = g; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 1;
        const gap = 24;
        ctx.beginPath();
        for(let x=0;x<w;x+=gap){ ctx.moveTo(x,0); ctx.lineTo(x,h); }
        for(let y=0;y<h;y+=gap){ ctx.moveTo(0,y); ctx.lineTo(w,y); }
        ctx.stroke();        
      }

      function drawSynth(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const cx = w/2, cy = h/2; const amp = Math.min(120, h*0.28);
        const bars = 96;
        ctx.save();
        ctx.translate(0, cy);
        const hueA = 22;
        const hueB = 204;
        for(let i=0;i<bars;i++){
          const k = i/(bars-1);
          const phase = t*0.015 + k*6.283;
          const value = Math.sin(phase) * Math.sin(k*Math.PI);
          const y = value * amp * (0.85 + 0.15*Math.sin(t*0.01 + k*12));
          const x = k * w;
          const hue = hueA*(1-k) + hueB*k;
          ctx.strokeStyle = `hsla(${hue}, 85%, ${40 + 15*Math.sin(t*0.02 + k*5)}%, 0.9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, y);
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, -y*0.6);
          ctx.stroke();
        }
        ctx.restore();
        t += 1;
      }

      function drawAnalyser(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!analyser || !dataArray){ drawSynth(); return; }
        analyser.getByteFrequencyData(dataArray);
        const bars = 96;
        const step = Math.max(1, Math.floor(dataArray.length / bars));
        ctx.save();
        ctx.translate(0, h/2);
        for(let i=0;i<bars;i++){
          const v = dataArray[i*step] / 255;
          const y = (v*v) * (h*0.35);
          const k = i/(bars-1);
          ctx.strokeStyle = `hsla(${22*(1-k) + 204*k}, 85%, ${45 + v*20}%, .9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), y); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), -y*0.6); ctx.stroke();
        }
        ctx.restore();
      }

      function loop(){
        drawBackground();
        if (synthMode) drawSynth(); else drawAnalyser();
        raf = requestAnimationFrame(loop);
      }

      async function start(){
        if (raf) return;
        try{
          const stream = await navigator.mediaDevices.getUserMedia({audio:true});
          audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 48000});
          analyser = audioCtx.createAnalyser();
          analyser.fftSize = 2048;
          dataArray = new Uint8Array(analyser.frequencyBinCount);
          source = audioCtx.createMediaStreamSource(stream);
          source.connect(analyser);
          synthMode = false;
        }catch(err){
          console.warn("Mic not available; using synth waveform.", err);
          synthMode = true;
        }
        loop();
      }
      function stop(){
        cancelAnimationFrame(raf); raf = 0;
        drawBackground(); drawSynth();
        if (source){ try{ source.disconnect(); }catch{} source = null; }
        if (audioCtx){ try{ audioCtx.close(); }catch{} audioCtx = null; }
      }
      drawBackground(); drawSynth();
      return { start, stop };
    })();

    let previousConnectionState = AppState.getState().connectionState;
    AppState.subscribe((state) => {
      sidText.textContent = state.sid || '—';
      let label = 'Disconnected';
      if (state.connectionState === 'connected') label = 'Connected';
      else if (state.connectionState === 'connecting') label = 'Connecting…';
      else if (state.connectionState === 'resuming') label = 'Resuming…';
      connLabel.textContent = label;
      const active = state.connectionState !== 'disconnected';
      statusDot.classList.toggle('on', active);
      startBtn.disabled = active;
      endBtn.disabled = !active;
      if (state.latencyMs != null) {
        latencyHint.textContent = `Latency: ${Math.round(state.latencyMs)} ms`;
      } else {
        latencyHint.textContent = 'Latency: —';
      }
      if (state.connectionState === 'disconnected') {
        resetVoiceState();
        resetSuggestions();
      } else if (state.infoFrame) {
        updateVoiceState(extractVoiceLocale(state.infoFrame));
      }
      setPttEnabled(state.connectionState === 'connected');
      const prevConnectionState = previousConnectionState;
      const nextConnectionState = state.connectionState;
      const becameConnected = prevConnectionState !== 'connected' && nextConnectionState === 'connected';
      const becameDisconnected = prevConnectionState !== 'disconnected' && nextConnectionState === 'disconnected';
      previousConnectionState = nextConnectionState;

      if (becameConnected) {
        Waveform.start();
        if (window.AudioRecorder && typeof window.AudioRecorder.start === 'function') {
          window.AudioRecorder.start()
            .then(() => {
              if (pttEnabled && !pttActive) {
                applyPttMask({ force: true });
              }
            })
            .catch((err) => {
              console.error('AudioRecorder start error', err);
            });
        }
      } else if (becameDisconnected) {
        Waveform.stop();
        if (window.AudioRecorder && typeof window.AudioRecorder.stop === 'function') {
          try {
            window.AudioRecorder.stop();
          } catch (err) {
            console.warn('AudioRecorder stop error', err);
          }
        }
        if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
          window.AudioPlayer.interrupt();
        }
        setPttActive(false);
      }
      updateResumeBanner(state);
    });

    const POST_TTS_RELEASE_MS = 150;

    window.addEventListener('tts.start', (event) => {
      const detail = event && event.detail;
      if (detail) {
        updateVoiceState(extractVoiceLocale(detail));
      }
      if (pttEnabled && !pttActive) {
        applyPttMask({ force: true });
      }
    });
    window.addEventListener('turn.state', (event) => {
      const detail = event && event.detail;
      if (!detail) {
        return;
      }
      const state = typeof detail.state === 'string'
        ? detail.state
        : (detail.meta && typeof detail.meta.state === 'string' ? detail.meta.state : null);
      if (!state || state.toLowerCase() !== 'ready') {
        return;
      }
      const reason = typeof detail.reason === 'string'
        ? detail.reason
        : (detail.meta && typeof detail.meta.reason === 'string' ? detail.meta.reason : null);
      if (!reason || reason.toLowerCase().includes('tts')) {
        DiagRecorder.maybeStart('turn');
      }
    });
    window.addEventListener('tts.end', () => {
      const release = () => {
        applyPttMask({ force: true });
        DiagRecorder.maybeStart('ready');
      };
      if (POST_TTS_RELEASE_MS > 0) {
        setTimeout(release, POST_TTS_RELEASE_MS);
      } else {
        release();
      }
    });
    window.addEventListener('asr.ready', () => {
      if (window.AudioRecorder && typeof window.AudioRecorder.start === 'function') {
        try {
          const maybePromise = window.AudioRecorder.start();
          if (maybePromise && typeof maybePromise.catch === 'function') {
            maybePromise.catch((err) => {
              console.error('AudioRecorder start on asr.ready failed', err);
            });
          }
        } catch (err) {
          console.error('AudioRecorder start on asr.ready failed', err);
        }
      }
      if (pttEnabled && !pttActive) {
        applyPttMask({ force: true });
      }
    });

    window.addEventListener('assistant.suggestions', (event) => {
      const detail = event && event.detail;
      applySuggestionsFrame(detail);
    });

    window.addEventListener('ws.close', () => {
      resetSuggestions();
      DiagRecorder.stop('ws');
    });

    // --- Smoke test harness (opt-in via ?wsSmoke=1) ---
    if (urlParams.get('wsSmoke') === '1') {
      const transitions = [];
      const unsubscribe = AppState.subscribe((s) => transitions.push(s.connectionState));

      class MockSocket extends EventTarget {
        constructor() {
          super();
          this.readyState = WebSocket.CONNECTING;
          setTimeout(() => {
            this.readyState = WebSocket.OPEN;
            this.dispatchEvent(new Event('open'));
          }, 0);
        }
        send(payload) {
          this.lastSent = payload;
        }
        close(code = 1000, reason = '') {
          this.readyState = WebSocket.CLOSED;
          const ev = new Event('close');
          ev.code = code;
          ev.reason = reason;
          ev.wasClean = true;
          this.dispatchEvent(ev);
        }
        simulateMessage(frame) {
          const payload = typeof frame === 'string' ? frame : JSON.stringify(frame);
          const ev = new Event('message');
          ev.data = payload;
          this.dispatchEvent(ev);
        }
      }

      const sockets = [];
      WSClient.__debug.setTransportFactory(() => {
        const mock = new MockSocket();
        sockets.push(mock);
        return mock;
      });

      startBtn.click();

      setTimeout(() => {
        const mock = sockets[0];
        if (!mock) return;
        mock.simulateMessage({
          type: 'info',
          meta: { sid: 'smoke-sid', resume_token: 'smoke-resume', resume_ttl_ms: 5000 }
        });
        WSClient.__debug.recordPing(Date.now() - 42);
        mock.simulateMessage({ type: 'pong', t: Date.now() });
        setTimeout(() => {
          endBtn.click();
          console.log('WSClient smoke transitions', transitions);
          console.log('WSClient smoke latency', AppState.getState().latencyMs);
          WSClient.__debug.resetTransportFactory();
          unsubscribe();
        }, 20);
      }, 20);
    }
  }

  init();
})();
