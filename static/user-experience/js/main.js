// main.js — Chat Live/Text + Static/Dynamic + guide/debug/suggestions; 2025-08-10
document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  // --- Dynamically size the chip box above the toolbar ---
  function setToolbarHeightVar(extra = 16) {
    const el = document.getElementById('askChipToolbar');
    if (!el) return;
    const h = Math.ceil(el.getBoundingClientRect().height) || 0;
    const finalPx = Math.max(h + extra, 64); // little breathing room
    document.documentElement.style.setProperty('--toolbar-h', finalPx + 'px');
  }
  window.addEventListener('load', setToolbarHeightVar);
  window.addEventListener('resize', setToolbarHeightVar);
  window.addEventListener('orientationchange', setToolbarHeightVar);

  // ---------- Static audio location + filenames ----------
  const STATIC_AUDIO_BASE = "/static/chip/audio/";
  const GREETING_FILES = ["greeting-static.mp3", "greeting.mp3", "Greeting.mp3"];
  const ANSWER_FILES   = ["answer-static.mp3", "answer.mp3", "Answer.mp3"]; // reserved for later

  // Core elements
  const loginModal   = $("loginModal");
  const profileModal = $("profileModal");
  const loginForm    = $("loginForm");
  const profileForm  = $("profileForm");
  const saveBtn      = $("saveProfileBtn");
  const profileHint  = $("profileHint");
  const appEl        = $("app");
  const chipBox      = $("chipBox");

  // Chat UI
  const chatPanel    = $("chatPanel");
  const chatLog      = $("chatLog");
  const chatInput    = $("chatInput");
  const chatSendBtn  = $("chatSendBtn");

  // Bottom toolbar
  const toolbar      = $("askChipToolbar");
  const btnStatic    = $("btnModeStatic");
  const btnDynamic   = $("btnModeDynamic");
  const btnMic       = $("btnMic");
  const btnHistory   = $("btnHistory");
  const btnLogout    = $("btnLogout");

  // Chat lane dropdown elements
  const chatDropdown = $("chatDropdown");
  const chatMenuBtn  = $("chatMenuBtn");
  const chatMenu     = $("chatMenu");

  // Top-right nav (Ask Chip ▾ → Profile)
  const navMenuBtn   = $("navMenuBtn");
  const navMenu      = $("navMenu");
  const navProfile   = $("navProfile");

  // ----- NEW: Guide & Debug overlays -----
  const guidePanelEl = $("guidePanel");
  const debugPanelEl = $("debugPanel");

  const _chipUX = {
    isAdmin: false,
    debugVisible: false,
    guideVisible: false,
    state: "idle",            // idle | greeting | waiting | listening | thinking | responding | followup
    waitingTimer: null,
    waitingSeconds: 7
  };

  function _getQueryParam(key) {
    try {
      const url = new URL(window.location.href);
      return url.searchParams.get(key);
    } catch { return null; }
  }

  function _chipSetAdmin(val) {
    _chipUX.isAdmin = !!val;
    _chipUX.debugVisible = _chipUX.isAdmin || _getQueryParam("debug") === "1";
    if (debugPanelEl) debugPanelEl.style.display = _chipUX.debugVisible ? "block" : "none";
  }

  function _chipStep(phase, details) {
    if (!_chipUX.debugVisible || !debugPanelEl) return;
    const time = new Date().toLocaleTimeString();
    let line = `[${time}] ${phase}`;
    if (details && typeof details === "object") {
      try { line += `\n${JSON.stringify(details, null, 2)}`; } catch {}
    } else if (details) {
      line += `\n${details}`;
    }
    debugPanelEl.textContent += ((debugPanelEl.textContent ? "\n" : "") + line);
    debugPanelEl.scrollTop = debugPanelEl.scrollHeight;
  }

  function _chipGuide(text) {
    if (!guidePanelEl) return;
    guidePanelEl.textContent = text || "";
    const showNow = !!text;
    guidePanelEl.style.display = showNow ? "block" : "none";
    _chipUX.guideVisible = showNow;
  }

  function _openChatComposer(hintText) {
    if (chatPanel) chatPanel.hidden = false;
    if (chatInput) {
      if (hintText) chatInput.placeholder = hintText;
      chatInput.focus();
    }
    _chipStep("composer", "opened");
  }

  function _chipSetState(next) {
    _chipUX.state = next;
    _chipStep("state", next);
    switch (next) {
      case "greeting":
        _chipGuide("Chip is saying hello — how can he help?");
        break;
      case "waiting":
        _chipGuide(`Ask your question — Chip waiting (${_chipUX.waitingSeconds}s)`);
        break;
      case "listening":
        _chipGuide("Listening… ask your question.");
        _openChatComposer("Type your question…");
        break;
      case "thinking":
        _chipGuide("Chip is thinking…");
        break;
      case "responding":
        _chipGuide("Chip is responding…");
        break;
      case "followup":
        _chipGuide("Do you have a follow-up?");
        _chipScheduleIdleNudge();
        break;
      default:
        _chipGuide("Press Start or Chat to speak with Chip.");
    }
  }

  function _chipStartWaitingCountdown() {
    if (_chipUX.waitingTimer) { clearInterval(_chipUX.waitingTimer); _chipUX.waitingTimer = null; }
    let t = _chipUX.waitingSeconds;
    _chipSetState("waiting");
    _chipUX.waitingTimer = setInterval(() => {
      t -= 1;
      if (t <= 0) {
        clearInterval(_chipUX.waitingTimer);
        _chipUX.waitingTimer = null;
        _chipSetState("listening");
      } else {
        _chipGuide(`Ask your question — Chip waiting (${t}s)`);
      }
    }, 1000);
  }

  // Nudge if idle on followup/listening
  let _chipIdleTimer = null;
  function _chipScheduleIdleNudge(ms = 20000) {
    if (_chipIdleTimer) { clearTimeout(_chipIdleTimer); _chipIdleTimer = null; }
    _chipIdleTimer = setTimeout(() => {
      if (_chipUX.state !== "followup" && _chipUX.state !== "listening") return;
      _chipGuide("Still there? Keep chatting or end?");
      _chipRenderSuggestions(["Explain a bit more", "Give me a quick example", "End chat"]);
    }, ms);
  }
  function _chipClearIdleNudge() {
    if (_chipIdleTimer) { clearTimeout(_chipIdleTimer); _chipIdleTimer = null; }
  }

  // Session mode (null until user clicks a mode)
  let sessionMode = null; // 'static' | 'dynamic' | null

  // Chat lane (persisted): 'live' (TTS) or 'text'
  let chatLane = (localStorage.getItem("chatLane") === "text") ? "text" : "live";

  // Helpers
  const show = (el, d) => { if (!el) return; d ? el.style.display = d : el.style.removeProperty("display"); };
  const hide = (el) => { if (el) el.style.display = "none"; };
  async function j(path, opts = {}) {
    const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json", ...(opts.headers||{}) }, ...opts });
    const ct = r.headers.get("content-type") || "";
    let data = null; try { data = ct.includes("application/json") ? await r.json() : null; } catch {}
    return { ok: r.ok, status: r.status, data, raw: r };
  }

  // ---------- Profile modal modes ----------
  function setProfileModalMode(mode) {
    if (!profileModal) return;
    profileModal.dataset.mode = mode; // 'gate' | 'edit'
    const titleEl = profileModal.querySelector("h2");
    if (titleEl) titleEl.textContent = (mode === "gate") ? "Complete Your Profile" : "Your Profile";
    if (profileHint) {
      if (mode === "gate") { profileHint.textContent = "Please fill out your profile to continue."; profileHint.style.display = "block"; }
      else { profileHint.textContent = ""; profileHint.style.display = "none"; }
    }
    if (saveBtn) saveBtn.textContent = (mode === "gate") ? "Save & Continue" : "Save changes";
  }
  async function loadProfileIntoForm() {
    if (!profileForm) return;
    const getI = (n) => profileForm.querySelector(`input[name="${n}"]`);
    const nameI = getI("name"), titleI = getI("title"), emailI = getI("email");
    try {
      const r = await fetch("/api/profile", { credentials: "include" });
      if (r.ok) {
        const js = await r.json();
        const p = (js && js.profile) || {};
        if (nameI)  nameI.value  = p.name  || "";
        if (titleI) titleI.value = p.title || "";
        if (emailI) emailI.value = p.email || "";
        return;
      }
    } catch {}
    try {
      if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
      if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
      if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
    } catch {}
  }

  function applyAuthedLayout() {
    show(appEl, "block");
    show(chipBox, "grid");
    show(toolbar, "flex");
    setToolbarHeightVar();
    // Position the mouth overlay relative to the current avatar size
    if (window.ChipViseme && typeof window.ChipViseme.layout === "function") {
      // anchor = center of mouth as % of avatar (x, y)
      window.ChipViseme.setAnchor(0.49, 0.46);
      // size = mouth box as % of avatar width (w, h)
      window.ChipViseme.setSize(0.095, 0.075);
      // reflow after applying the calibration
      window.ChipViseme.layout();
    }
  }

  async function enforceProfileCompleteness({ applyLayout = true } = {}) {
    try {
      const { ok, status, data } = await j("/api/me");
      // NEW: set admin + initial debug step + initial idle guide
      if (data) {
        _chipSetAdmin(!!data.isAdmin);
        _chipStep("me", data);
      }
      if (!ok) {
        if (status === 401) { hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"unauthenticated" }; }
        hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"server" };
      }
      if (!data?.profileComplete) {
        setProfileModalMode("gate");
        await loadProfileIntoForm();
        show(profileModal, "flex");
        hide(toolbar);
        return { ok:false, reason:"incomplete" };
      }
      if (applyLayout) {
        applyAuthedLayout();
        _chipSetState("idle");
        _chipGuide("Press Start or Chat to speak with Chip.");
      }
      return { ok:true };
    } catch {
      hide(appEl); show(loginModal, "flex");
      return { ok:false, reason:"error" };
    }
  }

  async function gate() {
    hide(profileModal);
    return await enforceProfileCompleteness({ applyLayout: true });
  }

  // ---------- Login ----------
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;
      const { ok, data, status } = await j("/api/login", { method: "POST", body: JSON.stringify({ email }) });
      if (!ok) { alert((data && data.error) || `Login failed (${status})`); return; }
      try { localStorage.setItem("profileEmail", email); } catch {}
      const emailInput = profileForm?.querySelector('input[name="email"]');
      if (emailInput) emailInput.value = email;
      hide(loginModal);
      await gate();
      _chipGuide("Press Start or Chat to speak with Chip.");
    });
  }

  // ---------- Save Profile ----------
  if (saveBtn && !saveBtn.dataset.wired) {
    saveBtn.dataset.wired = "1";
    saveBtn.addEventListener("click", async () => {
      if (!profileForm) return;
      const fd = new FormData(profileForm);
      const name  = (fd.get("name")  || "").toString().trim();
      const title = (fd.get("title") || "").toString().trim();
      const email = (fd.get("email") || "").toString().trim();
      if (!name || !title || !email) { alert("Please complete all fields."); return; }
      try { localStorage.setItem("profileName", name); localStorage.setItem("profileTitle", title); localStorage.setItem("profileEmail", email); } catch {}
      const r = await j("/api/profile", { method:"POST", body: JSON.stringify({ name, title, email }) });
      if (!r.ok || !r.data?.ok) { alert(r.data?.error || "Could not save profile. Please try again."); return; }
      hide(profileModal);
      if ((profileModal?.dataset.mode || "edit") === "gate") applyAuthedLayout();
      _chipGuide("Press Start or Chat to speak with Chip.");
      _chipSetState("idle");
      alert("Profile saved.");
    });
  }

  // ---------- Session logic ----------
  function reflectMode(mode) {
    sessionMode = mode; // 'static' | 'dynamic'
    btnStatic?.classList.toggle("mode-active", mode === "static");
    btnDynamic?.classList.toggle("mode-active", mode === "dynamic");
    document.documentElement.setAttribute("data-chip-mode", mode);
  }

  async function tryPlayWithMouth(url, opts) {
    if (window.ChipViseme && typeof window.ChipViseme.play === "function") {
      await window.ChipViseme.play(url, opts || {});
      return url;
    }
    // Fallback: just play the audio
    await new Audio(url).play();
    return url;
  }

  async function startStaticSession() {
    try {
      for (let i = 0; i < GREETING_FILES.length; i++) {
        const name = GREETING_FILES[i];
        const url = STATIC_AUDIO_BASE + name;
        try { await tryPlayWithMouth(url); return; } catch (_) {}
      }
      throw new Error("No static audio found.");
    } catch (e) {
      console.warn(e?.message || e);
      alert((e && e.message) || "Couldn’t play the static greeting. Check your /static/chip/audio/ files.");
    }
  }

  async function startDynamicSession() {
    try {
      _chipSetState("greeting");
      _chipStep("POST /greet →", {});
      const { ok, data, status } = await j("/greet", { method: "POST", body: JSON.stringify({}) });
      if (!ok) { _chipStep("greet-failed", { status, data }); alert((data && data.error) || `Greeting failed (${status})`); _chipSetState("idle"); return; }
      _chipStep("← /greet", data);
      const audioUrl = data?.audio;
      const text = data?.reply || "Hello!";
      if (audioUrl) {
        try { await tryPlayWithMouth(audioUrl); }
        catch (e) { console.warn("Dynamic audio failed:", e); alert(text); }
      } else {
        alert(text); // TTS disabled globally
      }
      _openChatComposer("Type your question…");
      _chipStartWaitingCountdown();
    } catch (e) {
      console.error("Dynamic session error:", e);
      _chipStep("greet-error", String(e));
      _chipSetState("idle");
      alert("Couldn’t start dynamic session. Try again.");
    }
  }

  // ---------- Toolbar: start sessions ----------
  btnStatic?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    reflectMode("static");
    _chipGuide("Press Start or Chat to speak with Chip.");
    _chipSetState("idle");
    await startStaticSession();
  });

  btnDynamic?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    reflectMode("dynamic");
    await startDynamicSession();
  });

  // Mic placeholder (kept)
  btnMic?.addEventListener("click", () => alert("Voice input coming soon."));

  // ---------- Chat dropdown + panel ----------
  function updateChatButtonLabel() {
    if (!chatMenuBtn) return;
    chatMenuBtn.textContent = (chatLane === "text") ? "💬 Chat (Text) ▾" : "💬 Chat (Live) ▾";
  }
  updateChatButtonLabel();

  function toggleChatMenu(forceOpen) {
    if (!chatMenu) return;
    if (typeof forceOpen === "boolean") {
      chatMenu.style.display = forceOpen ? "block" : "none";
      return;
    }
    chatMenu.style.display = (chatMenu.style.display === "block") ? "none" : "block";
  }

  // open/close menu
  chatMenuBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    // also reveal the chat panel when clicking Chat (so user sees the composer)
    if (chatPanel) {
      chatPanel.hidden = false;
      chatInput?.focus();
    }
    toggleChatMenu();
  });
  document.addEventListener("click", (e) => {
    if (!chatMenu) return;
    if (chatMenu.style.display === "block" && !chatMenu.contains(e.target) && e.target !== chatMenuBtn) {
      toggleChatMenu(false);
    }
  });

  // choose lane
  chatMenu?.addEventListener("click", (e) => {
    const t = e.target;
    if (!t || !t.getAttribute) return;
    const lane = t.getAttribute("data-lane");
    if (!lane) return;
    chatLane = (lane === "text") ? "text" : "live";
    try { localStorage.setItem("chatLane", chatLane); } catch {}
    updateChatButtonLabel();
    toggleChatMenu(false);
    if (chatPanel) { chatPanel.hidden = false; chatInput?.focus(); }
  });

  // ---------- Chat plumbing ----------
  function appendMessage(role, text, lane) {
    if (!chatLog) return null;
    const el = document.createElement("div");
    el.className = "msg " + role; // "user" | "assistant"
    const icon = lane ? (lane === "text" ? "💬 " : "🔊 ") : (role === "user" ? "🧑 " : "");
    el.textContent = icon + (text || "");
    chatLog.appendChild(el);
    chatLog.scrollTop = chatLog.scrollHeight;
    return el;
  }

  function appendActions(actions) {
    if (!actions || !actions.length || !chatLog) return;
    const wrap = document.createElement("div");
    wrap.className = "action-row";
    for (let i = 0; i < actions.length; i++) {
      const a = actions[i];
      if (!a || !a.type) continue;
      const btn = document.createElement("button");
      btn.className = "action";
      btn.textContent = a.title || (a.type === "download" ? "Download" : "Open");
      if (a.type === "download") {
        btn.addEventListener("click", () => triggerDownload(a.url, a.filename));
      } else if (a.type === "open_url") {
        btn.addEventListener("click", () => window.open(a.url, "_blank", "noopener"));
      } else if (a.type === "show_toast") {
        btn.addEventListener("click", () => alert(a.message || "Done"));
      }
      wrap.appendChild(btn);
    }
    chatLog.appendChild(wrap);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function triggerDownload(url, filename) {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    if (filename) a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function playAudioFromBase64(b64, onended) {
    if (!b64) { if (onended) onended(); return null; }
    const audio = new Audio("data:audio/mpeg;base64," + b64);
    if (onended) audio.addEventListener("ended", onended, { once: true });
    audio.play().catch(console.error);
    return audio;
  }

  function driveVisemes(visemes) {
    if (!visemes || !visemes.length) return;
    if (window.ChipViseme && typeof window.ChipViseme.drive === "function") {
      try { window.ChipViseme.drive(visemes); } catch (e) { console.warn("Viseme drive failed:", e); }
    }
  }

  // Suggestions (chips) under assistant replies
  function _chipRenderSuggestions(suggestions) {
    if (!Array.isArray(suggestions) || !suggestions.length || !chatLog) return;
    const wrap = document.createElement("div");
    wrap.className = "suggestion-row";
    suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.className = "suggestion";
      b.textContent = s;
      b.addEventListener("click", () => {
        if (/end chat/i.test(s)) { _chipEndConversation(); return; }
        // Let server handle intent if it's a doc/account action; otherwise just send the text
        sendChat(s);
      });
      wrap.appendChild(b);
    });
    chatLog.appendChild(wrap);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  async function _chipEndConversation() {
    try {
      _chipStep("end", "user requested");
      // Friendly audible wrap-up (fire/forget)
      fetch("/api/speak", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ prompt: "Anytime. I’ll be right here when you need me." }) }).catch(()=>{});
    } finally {
      _chipClearIdleNudge();
      _chipSetState("idle");
      _chipGuide("Press Start or Chat to speak with Chip.");
    }
  }

  async function sendChat(message) {
    if (!message || !message.trim()) return;
    const okGate = await gate(); if (!okGate.ok) return;
    if (chatPanel) chatPanel.hidden = false;

    _chipClearIdleNudge();

    appendMessage("user", message, null);
    const thinking = appendMessage("assistant", "…", chatLane);

    try {
      _chipSetState("thinking");
      _chipStep("POST /chat →", { message: message.trim(), lane: chatLane });

      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message.trim(), lane: chatLane })
      });
      const data = await res.json();

      _chipStep("← /chat", data);
      _chipSetState("responding");

      // text
      thinking.textContent = (chatLane === "live" ? "🔊 " : "💬 ") + (data.reply_text || "");

      // audio + visemes (Live lane)
      let audioObj = null;
      if (data.audio_b64) {
        audioObj = new Audio("data:audio/mpeg;base64," + data.audio_b64);
        audioObj.addEventListener("ended", () => { _chipSetState("followup"); }, { once: true });
        audioObj.play().catch(console.error);
      }
      if (data.visemes && data.visemes.length) {
        driveVisemes(data.visemes);
      }

      // actions (download/open_url/etc.)
      appendActions(data.actions || []);

      // suggestions under reply
      if (Array.isArray(data.suggestions)) {
        _chipRenderSuggestions(data.suggestions);
      }

      // respect server end-of-conversation
      if (data.end === true) {
        _chipEndConversation();
        return;
      }

      if (!audioObj && !data.audio_b64) {
        _chipSetState("followup");
      }
    } catch (e) {
      thinking.textContent = "Sorry—something went sideways.";
      console.error(e);
      _chipStep("chat-error", String(e));
      _chipSetState("idle");
    }
  }

  // Compose handlers
  chatSendBtn?.addEventListener("click", () => {
    if (!chatInput) return;
    const val = chatInput.value;
    if (val && val.trim()) { sendChat(val); chatInput.value = ""; }
  });
  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const val = chatInput.value;
      if (val && val.trim()) { sendChat(val); chatInput.value = ""; }
    }
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      // quick override to Live (TTS) for this message
      const prev = chatLane;
      chatLane = "live";
      updateChatButtonLabel();
      const val = chatInput.value;
      if (val && val.trim()) { sendChat(val); chatInput.value = ""; }
      chatLane = prev;
      updateChatButtonLabel();
    }
  });

  // ---------- “Ask Chip ▾” (Profile) ----------
  function toggleNavMenu(forceOpen) {
    if (!navMenu) return;
    if (typeof forceOpen === "boolean") { navMenu.hidden = !forceOpen; return; }
    navMenu.hidden = !navMenu.hidden;
  }
  navMenuBtn?.addEventListener("click", (e) => { e.stopPropagation(); toggleNavMenu(); });
  document.addEventListener("click", (e) => {
    if (!navMenu?.hidden && !navMenu.contains(e.target) && e.target !== navMenuBtn) toggleNavMenu(false);
  });
  navProfile?.addEventListener("click", async () => {
    toggleNavMenu(false);
    setProfileModalMode("edit");
    await loadProfileIntoForm();
    show(profileModal, "flex");
  });

  // ---------- Boot ----------
  (async () => {
    const g = await gate();
    if (g && g.ok) {
      _chipGuide("Press Start or Chat to speak with Chip.");
      _chipStep("boot", "ready");
    }
  })();
});
