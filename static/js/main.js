// static/js/main.js — anti-duplicate typed sends, single-init, in-flight lock, voice dedupe — 2025‑08‑24d
document.addEventListener("DOMContentLoaded", async () => {
  // Prevent double-initialization if the script is loaded twice
  if (window.__CHIP_MAIN_INITIALIZED__) {
    try { console.debug("[Chip] main.js already initialized; skipping re-init"); } catch (_){}
    return;
  }
  window.__CHIP_MAIN_INITIALIZED__ = true;

  // --- State ---
  let greeted = false;
  let recognizer = null;
  let recognizing = false;
  let lastFollowUpAt = 0;
  let inFlight = false;

  // Voice dedupe
  const DUP_WINDOW_MS = 1800;
  let lastHeard = "";
  let lastHeardAt = 0;
  let lastUserBubble = "";

  // Lightweight conversation context (persisted for the tab only)
  let AC_CTX = {};
  try { AC_CTX = JSON.parse(sessionStorage.getItem("AC_CTX") || "{}"); } catch (_) { AC_CTX = {}; }
  function saveCtx() { try { sessionStorage.setItem("AC_CTX", JSON.stringify(AC_CTX)); } catch (_) {} }

  // --- Elements ---
  const loginForm    = document.getElementById("loginForm");
  const loginEmail   = document.getElementById("loginEmail");
  const logoutBtn    = document.getElementById("logoutBtn");
  const profileBtn   = document.getElementById("profileBtn");

  const profileForm  = document.getElementById("profileForm");
  const profileName  = document.getElementById("profileName");
  const profileTitle = document.getElementById("profileTitle");
  const profileRegion= document.getElementById("profileRegion");
  const profileEmail = document.getElementById("profileEmail");

  const composer      = document.getElementById("composer");
  const composerInput = document.getElementById("composerInput");
  const sendBtn       = document.getElementById("sendBtn");

  const micBtn     = document.getElementById("micBtn");
  const endBtn     = document.getElementById("endBtn");
  const chipCanvas = document.getElementById("chipCanvas");
  const chipSprite = document.getElementById("chipSprite");

  // --- Enforce view: only one of login/profile/chat visible ---
  function ac_show(id) {
    const LV = document.getElementById("loginView");
    const PV = document.getElementById("profileView");
    const CV = document.getElementById("chatView");
    if (LV) LV.hidden = (id !== "login");
    if (PV) PV.hidden = (id !== "profile");
    if (CV) CV.hidden = (id !== "chat");
  }
  try {
    if (window.UI && typeof UI.show === "function") {
      const _origShow = UI.show.bind(UI);
      UI.show = function(view) { ac_show(view); return _origShow(view); };
    } else {
      window.UI = window.UI || {};
      UI.show = ac_show;
    }
  } catch (_) {}

  // --- Sprite / Viseme init ---
  (function ensureSprite() {
    if (!chipSprite) return;
    const url = "/static/chip/img/chip.png";
    chipSprite.src = url;
    chipSprite.onerror = () => { chipSprite.style.display = "none"; };
  })();

  if (chipCanvas && typeof Viseme !== "undefined") {
    Viseme.init(chipCanvas);
  }

  // --- Helpers (UI) ---
  function scrollChatToBottom() {
    const el = document.getElementById("chatLog");
    if (el) el.scrollTop = el.scrollHeight;
  }

  async function ac_resumeListening() {
    // Re-arm mic after TTS; guard against stale recognizer events.
    try {
      if (!supportsSpeechRecognition()) return;
      try {
        if (recognizer) { recognizer.onend = null; recognizer.onerror = null; recognizer.stop(); }
      } catch (_) {}
      recognizing = false;
      await new Promise(r => setTimeout(r, 250)); // give audio stack a breath
      await toggleMic();
    } catch (_) {}
  }

  // --- Helpers (context & style) ---
  function ac_detectContext(text) {
    const t = (text || "").toLowerCase();
    // Product
    if (/(^|\W)flash\s*blade(s)?(\W|$)|(^|\W)flashblade(\W|$)/i.test(t)) AC_CTX.product = "FlashBlade";
    else if (/(^|\W)flash\s*array(\W|$)|(^|\W)flasharray(\W|$)/i.test(t)) AC_CTX.product = "FlashArray";
    else if (/(^|\W)portworx(\W|$)/i.test(t)) AC_CTX.product = "Portworx";

    // Task
    if (/(^|\W)(install|installation|set\s*up|setup|deploy|deployment|walk\s*me\s*through)/i.test(t)) AC_CTX.task = "installation";
    else if (/(^|\W)(troubleshoot|troubleshooting|error|fail|issue|debug|diagnose)/i.test(t)) AC_CTX.task = "troubleshooting";
    else if (/(^|\W)(design|architecture|size|sizing|capacity|plan|planning)/i.test(t)) AC_CTX.task = "design";
    else if (/(^|\W)(upgrade|update|patch)/i.test(t)) AC_CTX.task = "upgrade";

    // Depth hints
    if (/(high\s*level|overview|summary)/i.test(t)) AC_CTX.depth = "high";
    if (/(step\s*by\s*step|walk\s*through|detailed|deep)/i.test(t)) AC_CTX.depth = "deep";

    // Continuation markers
    if (/(go ahead|continue|keep going|walk me through it|then|next)/i.test(t)) AC_CTX.continue = true;

    saveCtx();
  }

  // Keep the helper for compatibility; callers now pass raw text to API.chat
  function ac_applyStyleToPrompt(s) { ac_detectContext(s); return s; }

  function shouldAddFollowUp(userPrompt, reply) {
    if (!reply || reply.trim().length < 12) return false;
    if (/\b(fallback|error|sorry)\b/i.test(reply)) return false;
    if (/\?\s*$/.test(reply)) return false; // already ends with a question
    const now = Date.now();
    if (now - lastFollowUpAt < 3500) return false;
    lastFollowUpAt = now;
    return true;
  }

  
  function ac_contextualFollowUp(userPrompt, reply) {
    // Disabled to prevent static follow‑ups that felt repetitive.
    return "";
  }
` : "";
      return `Want a step-by-step install checklist${p}? I can cover prerequisites, network, and validation.`;
    }
    if (/design|architecture|size|sizing|capacity|plan/.test(lower)) {
      const p = prod ? ` for ${prod}` : "";
      return `Should I sketch a simple reference design${p}, or jump to sizing guidance?`;
    }
    return "";
  }

  // --- Conversation helpers (existing) ---
  function chipFollowUp(prompt, reply) { return ""; }

  async function dynamicGreet() {
    UI.setStatus("Greeting…");
    let greetText = "Hey—Chip here. What are we tackling today?";
    try {
      const res = await API.greet();
      if (res && res.text) greetText = res.text;
    } catch (_) {}
    UI.appendBubble("assistant", greetText);
    scrollChatToBottom();
    try { await speakWithVisemes(greetText); } catch (_) {}
    greeted = true;
    UI.setStatus("Listening…");
  }

  async function speakWithVisemes(text) {
    try {
      const resp = await fetch("/api/voice/tts_with_visemes", { credentials: 'include',
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      const data = await resp.json();
      if (data && data.ok && data.audio) {
        const url = "data:audio/mpeg;base64," + data.audio;
        const audioEl = new Audio(url);
        await new Promise((resolve) => { audioEl.onloadedmetadata = resolve; audioEl.onerror = resolve; });
        UI.setStatus("Speaking…");
        if (micBtn) micBtn.classList.add("speaking");
        document.body.classList.add("speaking");
        if (typeof Viseme !== "undefined") {
          const schedule = (data.visemes || []).map(x => ({ t: x.t, v: x.v }));
          Viseme.animate(schedule, audioEl, { relative: data.relative !== false });
        }
        audioEl.play().catch(() => {});
        await new Promise((resolve) => { audioEl.onended = resolve; audioEl.onerror = resolve; });
        if (typeof Viseme !== "undefined") Viseme.stop();
        if (micBtn) micBtn.classList.remove("speaking");
        document.body.classList.remove("speaking");
        UI.setStatus("Ready");
        return;
      }
    } catch (_) {}
    UI.setStatus("Audio unavailable — check ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID");
  }

  // ---------------- Account Team intent ----------------
  const _ac_ACCOUNT_PATTERNS = [
    /^\s*(?:do\s+you\s+know\s+)?(?:can\s+you\s+)?(?:what(?:'s| is)\s+)?(?:the\s+)?account\s+team(?:\s+(?:info(?:rmation)?|details)?)?\s+(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$/i,
    /^\s*who\s+(?:covers|owns)\s+(.+?)\s*[?.!]*$/i,
    /^\s*who\s+is\s+the\s+(?:pure\s+rep|account\s+owner)\s+(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$/i,
    /^\s*(?:team|owner|rep)\s+(.+?)\s*[?.!]*$/i
  ];

  function _ac_matchAccountLookup(text) {
    const t = (text || "").trim();
    if (!t) return null;
    for (let i = 0; i < _ac_ACCOUNT_PATTERNS.length; i++) {
      const m = t.match(_ac_ACCOUNT_PATTERNS[i]);
      if (m && m[1]) return m[1].trim();
    }
    if (/account\s+team/i.test(t)) {
      const m = t.match(/(?:for|at|on|about|regarding)\s+(.+?)\s*[?.!]*$/i);
      if (m && m[1]) return m[1].trim();
    }
    return null;
  }

  function _ac_pickTeamShape(j) {
    if (!j) return null;
    let o = null;
    if (Array.isArray(j)) o = j[0];
    else if (Array.isArray(j.results)) o = j.results[0];
    else if (j.data && Array.isArray(j.data)) o = j.data[0];
    else if (j.data && typeof j.data === "object") o = j.data;
    else if (typeof j === "object") o = j;
    if (!o) return null;
    return {
      name: o.account_name || o.AccountName || o.Account || o.name || o.customer || "",
      owner: o.account_owner || o.AccountOwner || o.owner || "",
      rep: o.pure_rep || o.PureRep || o.rep || "",
      type: o.type || o.Type || o.segment || ""
    };
  }

  async function ac_tryAccountTeam(userText) {
    const q = _ac_matchAccountLookup(userText);
    if (!q) return false;

    let say = "";
    try {
      const res = await fetch(`/api/account_team?name=${encodeURIComponent(q)}`);
      if (res.ok) {
        const j = await res.json();
        if (j && j.ok && j.found) {
          say = j.rendered || "";
        } else if (j && typeof j.rendered === "string") {
          say = j.rendered;
        }
      }
    } catch (_) {}

    if (!say) {
      try {
        const r2 = await fetch(`/api/accounts/search?q=${encodeURIComponent(q)}`);
        if (r2.ok) {
          const j2 = await r2.json();
          const t = _ac_pickTeamShape(j2);
          if (t && (t.name || t.owner || t.rep || t.type)) {
            say = `Account team for ${t.name || q}${t.owner ? `; Account Owner — ${t.owner}` : ""}${t.rep ? `; Pure Rep — ${t.rep}` : ""}${t.type ? `; Type — ${t.type}` : ""}. Want me to email that to you?`;
          }
        }
      } catch (_) {}
    }

    if (!say) say = `I couldn’t find an account team for ${q}. Want to try another name?`;

    UI.appendBubble("assistant", say);
    scrollChatToBottom();
    try { await speakWithVisemes(say); } catch (_) {}
    return true; // handled
  }
  // ---------------- /Account Team intent ----------------

  function supportsSpeechRecognition() {
    return "webkitSpeechRecognition" in window || "SpeechRecognition" in window;
  }

  function getRecognizer() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return null;
    const r = new SR();
    r.lang = "en-US";
    r.interimResults = false;
    r.continuous = false;  // one result event
    r.maxAlternatives = 1;
    return r;
  }

  function beginSend() {
    if (inFlight) { console.debug("[Chip] send suppressed (in-flight)"); return false; }
    inFlight = true;
    if (sendBtn) sendBtn.disabled = true;
    return true;
  }
  function endSend() {
    inFlight = false;
    if (sendBtn) sendBtn.disabled = false;
  }

  // Centralized typed-send handler (prevents duplicates)
  async function handleTypedSend(rawText) {
    const prompt = (rawText || "").trim();
    if (!prompt) return;
    if (!beginSend()) return;

    ac_detectContext(prompt);
    if (prompt !== lastUserBubble) {
      UI.appendBubble("user", prompt);
      lastUserBubble = prompt;
    }
    scrollChatToBottom();

    // EARLY EXIT: account-team lookup before LLM
    try {
      if (await ac_tryAccountTeam(prompt)) { UI.setStatus("Ready"); endSend(); return; }
    } catch (_) {}

    UI.setStatus("Thinking…");
    try {
      const res = await API.chat(prompt);  // send raw text
      if (res && res.ok && (res.reply || "").trim()) {
        const reply = (res.reply || "").trim();
        ac_detectContext(reply);
        UI.appendBubble("assistant", reply);
        scrollChatToBottom();
        await speakWithVisemes(reply);
        const fu = ac_contextualFollowUp(prompt, reply);
        if (fu) { UI.appendBubble("assistant", fu); scrollChatToBottom(); await speakWithVisemes(fu); }
        UI.setStatus("Ready");
      } else {
        const err = (res && (res.error || res.body)) || "Something went wrong.";
        UI.appendBubble("assistant", err);
        scrollChatToBottom();
        UI.setStatus("Error");
      }
    } finally {
      endSend();
      if (composerInput) composerInput.focus();
    }
  }

  async function toggleMic() {
    if (!supportsSpeechRecognition()) { UI.setStatus("Browser speech recognition not available"); return; }
    if (recognizing) {
      try { recognizer && recognizer.stop(); } catch (_) {}
      recognizing = false;
      if (micBtn) { micBtn.setAttribute("aria-pressed", "false"); micBtn.classList.remove("listening"); }
      document.body.classList.remove("listening");
      UI.setStatus("Ready");
      return;
    }
    recognizer = getRecognizer();
    if (!recognizer) { UI.setStatus("SpeechRecognition unavailable"); return; }
    recognizing = true;
    if (micBtn) { micBtn.setAttribute("aria-pressed", "true"); micBtn.classList.add("listening"); }
    document.body.classList.add("listening");
    UI.setStatus("Listening — go ahead.");

    recognizer.onresult = async (ev) => {
      let transcript = (ev.results && ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript || "").trim();
      const now = Date.now();
      if (transcript && transcript.toLowerCase() === (lastHeard || "").toLowerCase() && (now - lastHeardAt) < DUP_WINDOW_MS) {
        console.debug("[Chip] dedup voice repeat:", transcript);
        transcript = ""; // ignore duplicate
      } else { lastHeard = transcript; lastHeardAt = now; }

      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      if (!transcript) { UI.setStatus("Ready"); await ac_resumeListening(); return; }

      if (inFlight) { console.debug("[Chip] ignoring voice input while a request is in flight"); return; }

      ac_detectContext(transcript);
      if (transcript !== lastUserBubble) {
        UI.appendBubble("user", transcript);
        lastUserBubble = transcript;
      }
      scrollChatToBottom();

      // EARLY EXIT: account-team lookup before LLM
      try {
        if (await ac_tryAccountTeam(transcript)) { await ac_resumeListening(); return; }
      } catch (_) {}

      UI.setStatus("Thinking…");
      if (!beginSend()) return;
      try {
        const res = await API.chat(transcript);
        if (res && res.ok && (res.reply || "").trim()) {
          const reply = (res.reply || "").trim();
          ac_detectContext(reply);
          UI.appendBubble("assistant", reply);
          scrollChatToBottom();
          await speakWithVisemes(reply);
          const fu = ac_contextualFollowUp(transcript, reply);
          if (fu) { UI.appendBubble("assistant", fu); scrollChatToBottom(); await speakWithVisemes(fu); }
          await ac_resumeListening();
        } else {
          const err = (res && (res.error || res.body)) || "Something went wrong.";
          UI.appendBubble("assistant", err);
          scrollChatToBottom();
          UI.setStatus("Error");
        }
      } finally {
        endSend();
      }
    };

    recognizer.onerror = (e) => {
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      document.body.classList.remove("listening");
      const code = e && e.error || "error";
      UI.setStatus(code === "no-speech" ? "Didn't catch that—try again." : "Mic error");
      if (code === "no-speech" || code === "audio-capture") {
        setTimeout(() => { ac_resumeListening(); }, 300);
      }
    };

    recognizer.onend = () => {
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      document.body.classList.remove("listening");
      UI.setStatus("Ready");
    };

    recognizer.start();
  }

  // --- Auth / Profile / UI wiring ---
  if (loginForm) {
    loginForm.addEventListener("submit", async (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const email = (loginEmail && loginEmail.value || "").trim();
      if (!email) return;
      UI.setStatus("Signing in…");
      try {
        const res = await API.login(email);
        if (res && res.ok) {
          UI.setUser(email);
          ac_show("chat");
          await refreshState();
          UI.setStatus("Ready");
        } else {
          UI.setStatus((res && res.error) || "Login failed");
        }
      } catch (e) {
        UI.setStatus("Login failed");
      }
    }, true); // capture
  }

  if (profileBtn) {
    profileBtn.addEventListener("click", async () => {
      try {
        const res = await API.getProfile();
        if (res && res.ok && res.user) {
          const u = res.user || {};
          if (profileEmail) profileEmail.value = u.email || "";
          if (profileName)  profileName.value  = u.name  || "";
          if (profileTitle) profileTitle.value = u.title || "";
          if (profileRegion)profileRegion.value= u.region|| "";
        }
      } catch(_) {}
      UI.show("profile");
      UI.setStatus("Edit your profile and Save to continue");
    });
  } // end if (profileBtn)

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await API.logout(); UI.setUser(""); ac_show("login"); UI.setStatus("Logged out");
    });
  }

  if (profileForm) {
    profileForm.addEventListener("submit", async (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const payload = {
        name:   (profileName  && profileName.value  || "").trim(),
        title:  (profileTitle && profileTitle.value || "").trim(),
        region: (profileRegion&& profileRegion.value|| "").trim()
      };
      UI.setStatus("Saving profile…");
      const res = await API.saveProfile(payload);
      if (res.ok) { await refreshState(); UI.setStatus("Profile saved"); }
      else { UI.setStatus(res.error || "Save failed"); }
    }, true);
  }

  // --- Typed input handlers (CAPTURE PHASE to suppress other listeners) ---
  if (composer) {
    // Keep input autosizing + button enable
    composerInput.addEventListener("input", () => {
      const hasText = (composerInput.value || "").trim().length > 0;
      sendBtn.disabled = !hasText || inFlight;
      autoGrow(composerInput);
    });

    // Capture submit to stop other handlers, then route to our unified sender
    composer.addEventListener("submit", (ev) => {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const text = (composerInput.value || "").trim();
      if (!text) return;
      composerInput.value = "";
      handleTypedSend(text);
    }, true); // capture

    // Also capture button clicks in case some code is bound to 'click'
    if (sendBtn) {
      sendBtn.addEventListener("click", (ev) => {
        ev.preventDefault(); ev.stopImmediatePropagation();
        const text = (composerInput.value || "").trim();
        if (!text) return;
        composerInput.value = "";
        handleTypedSend(text);
      }, true);
    }
  }

  if (micBtn) {
    // First press greets; subsequent presses toggle mic
    micBtn.addEventListener("click", async () => {
      if (!greeted) {
        micBtn.setAttribute("aria-pressed","true");
        micBtn.classList.add("speaking");
        await dynamicGreet();
        try { await toggleMic(); } catch(_) {}
        return;
      }
      await toggleMic();
    });
  }

  if (endBtn) {
    endBtn.addEventListener("click", () => {
      try { if (recognizer) { recognizer.onend = null; recognizer.onerror = null; recognizer.stop(); } } catch(_) {}
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed","false");
      if (micBtn) { micBtn.classList.remove("listening"); micBtn.classList.remove("speaking"); }
      document.body.classList.remove("speaking");
      UI.setStatus("Ended — press “Talk to Chip” to start again.");
    });
  }

  async function refreshState() {
    const me = await API.me();
    if (!me.logged_in) { ac_show("login"); UI.setUser(""); return; }
    UI.setUser(me.user && me.user.email || "");
    if (!me.profile_complete) {
      ac_show("profile");
      UI.setStatus("Please fill out your profile to continue");
      const prof = await API.getProfile();
      const u = prof.user || {};
      if (profileName)  profileName.value  = u.name  || "";
      if (profileTitle) profileTitle.value = u.title || "";
      if (profileRegion)profileRegion.value= u.region|| "";
      if (micBtn) micBtn.disabled = true;
      return;
    }
    ac_show("chat");
    if (micBtn) micBtn.disabled = false;
    UI.setStatus("Ready");
  }

  function autoGrow(el) { const min = 38; el.style.height = "auto"; el.style.height = Math.max(min, el.scrollHeight) + "px"; }

  // Initial state sync
  await refreshState();

  // EOF marker to prove full file loaded:
  try { console.log("[AskChip] main.js EOF 2025-08-24d"); } catch (_) {}
});
