// main.js — Chat Live/Text + existing Static/Dynamic; 2025-08-10 chat
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
      if (!ok) { if (status === 401) { hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"unauthenticated" }; } hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"server" }; }
      if (!data?.profileComplete) {
        setProfileModalMode("gate");
        await loadProfileIntoForm();
        show(profileModal, "flex");
        hide(toolbar);
        return { ok:false, reason:"incomplete" };
      }
      if (applyLayout) applyAuthedLayout();
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
      const { ok, data, status } = await j("/greet", { method: "POST", body: JSON.stringify({}) });
      if (!ok) { alert((data && data.error) || `Greeting failed (${status})`); return; }
      const audioUrl = data?.audio;
      const text = data?.reply || "Hello!";
      if (audioUrl) {
        try { await tryPlayWithMouth(audioUrl); }
        catch (e) { console.warn("Dynamic audio failed:", e); alert(text); }
      } else {
        alert(text); // TTS disabled globally
      }
    } catch (e) {
      console.error("Dynamic session error:", e);
      alert("Couldn’t start dynamic session. Try again.");
    }
  }

  // ---------- Toolbar: start sessions ----------
  btnStatic?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    reflectMode("static");
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
    // If you prefer to use ChipViseme.play, convert to Blob URL and pass it in:
    // const blob = b64ToBlob(b64, "audio/mpeg"); const url = URL.createObjectURL(blob); window.ChipViseme.play(url);
  }

  function driveVisemes(visemes) {
    if (!visemes || !visemes.length) return;
    if (window.ChipViseme && typeof window.ChipViseme.drive === "function") {
      try { window.ChipViseme.drive(visemes); } catch (e) { console.warn("Viseme drive failed:", e); }
    }
  }

  async function sendChat(message) {
    if (!message || !message.trim()) return;
    const okGate = await gate(); if (!okGate.ok) return;
    if (chatPanel) chatPanel.hidden = false;

    appendMessage("user", message, null);
    const thinking = appendMessage("assistant", "…", chatLane);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message.trim(), lane: chatLane })
      });
      const data = await res.json();

      // text
      thinking.textContent = (chatLane === "live" ? "🔊 " : "💬 ") + (data.reply_text || "");

      // audio + visemes (Live lane)
      if (data.audio_b64) {
        playAudioFromBase64(data.audio_b64, function(){});
      }
      if (data.visemes && data.visemes.length) {
        driveVisemes(data.visemes);
      }

      // actions (download/open_url/etc.)
      appendActions(data.actions || []);
    } catch (e) {
      thinking.textContent = "Sorry—something went sideways.";
      console.error(e);
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
  (async () => { await gate(); })();
});
