// static/js/main.js — Known-good copy (no triage). EOF marker at end for verification.
document.addEventListener("DOMContentLoaded", async () => {
  // --- State ---
  let greeted = false;
  let recognizer = null;
  let recognizing = false;
  let lastFollowUpAt = 0;

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

  function ac_stylePrefix() {
    // Pure-first guardrails and current context for the model
    let lines = [
      "STYLE: You are Chip, a Pure Storage expert. Keep personality minimal (short flourish occasionally), 90% product substance.",
      "Be clear, concise, and conversational. Use short sentences. Respect prior context—continue the current topic without re-asking.",
      "If the user asks to 'walk me through it', provide a step-by-step with prerequisites, actions, and validation.",
      "End with one concise follow-up that keeps momentum (e.g., 'Want the checklist emailed?' or 'Should I go deeper on step 2?')."
    ];
    if (AC_CTX.product) lines.push(`Product focus: ${AC_CTX.product}.`);
    if (AC_CTX.task) lines.push(`Current task: ${AC_CTX.task}.`);
    if (AC_CTX.depth) lines.push(`Depth: ${AC_CTX.depth}.`);
    if (AC_CTX.continue) lines.push("This is a continuation; do not switch products unless the user asks.");
    return `[[${lines.join(" ")}]]`;
  }

  function ac_applyStyleToPrompt(prompt) {
    ac_detectContext(prompt); // update context before applying
    return `${ac_stylePrefix()}\n${prompt}`;
  }

  function ac_contextualFollowUp(userPrompt, reply) {
    // Avoid double questions
    if (/\?\s*$/.test(reply || "")) return "";
    const now = Date.now();
    if (now - lastFollowUpAt < 3500) return ""; // avoid stacking
    lastFollowUpAt = now;

    const prod = AC_CTX.product || "";
    const task = AC_CTX.task || "";
    const lower = (userPrompt || "").toLowerCase();

    // Prefer installation guidance if user hinted at it
    if (task === "installation" || /install|walk.*through|step.*by.*step/.test(lower)) {
      const p = prod ? ` for ${prod}` : "";
      return `Want a step-by-step install checklist${p}? I can cover prerequisites, network, and validation.`;
    }

    if (/design|architecture|size|sizing|capacity|plan/.test(lower)) {
      const p = prod ? ` for ${prod}` : "";
      return `Should I sketch a simple reference design${p}, or jump to sizing guidance?`;
    }

    // Generic nudge
    return "Want me to go deeper on any part, or keep it high level?";
  }

  // --- Conversation helpers (existing) ---
  function chipFollowUp(prompt, reply) {
    // Retain for backward compatibility; replaced by ac_contextualFollowUp
    return "";
  }

  async function dynamicGreet() {
    UI.setStatus("Greeting…");
    let greetText = "Hey—Chip here. What are we tackling today?";
    try {
      const res = await API.greet({ dynamic: true });
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
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

  // ---------------- Account Team intent (robust) ----------------
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

    if (!say) {
      say = `I couldn’t find an account team for ${q}. If you want, I can try another name or different spelling.`;
    }

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
    r.maxAlternatives = 1;
    return r;
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
      const transcript = (ev.results && ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript || "").trim();
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      if (!transcript) { UI.setStatus("Ready"); await ac_resumeListening(); return; }

      ac_detectContext(transcript);
      UI.appendBubble("user", transcript);
      scrollChatToBottom();

      // EARLY EXIT: account-team lookup before LLM
      try {
        if (await ac_tryAccountTeam(transcript)) { await ac_resumeListening(); return; }
      } catch (_) {}

      UI.setStatus("Thinking…");
      const styledPrompt = ac_applyStyleToPrompt(transcript);
      const res = await API.chat(userText);
      if (res.ok) {
        const reply = res.reply || "";
        ac_detectContext(reply);
        UI.appendBubble("assistant", reply);
        scrollChatToBottom();
        await speakWithVisemes(reply);

        const fu = ac_contextualFollowUp(transcript, reply);
        if (fu) { UI.appendBubble("assistant", fu); scrollChatToBottom(); await speakWithVisemes(fu); }
        await ac_resumeListening();
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong.");
        scrollChatToBottom();
        UI.setStatus("Error");
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
      ev.preventDefault();
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
    });
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
      ev.preventDefault();
      const payload = {
        name:   (profileName  && profileName.value  || "").trim(),
        title:  (profileTitle && profileTitle.value || "").trim(),
        region: (profileRegion&& profileRegion.value|| "").trim()
      };
      UI.setStatus("Saving profile…");
      const res = await API.saveProfile(payload);
      if (res.ok) { await refreshState(); UI.setStatus("Profile saved"); }
      else { UI.setStatus(res.error || "Save failed"); }
    });
  }

  if (composer) {
    composerInput.addEventListener("input", () => {
      const hasText = (composerInput.value || "").trim().length > 0;
      sendBtn.disabled = !hasText;
      autoGrow(composerInput);
    });

    composer.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const prompt = (composerInput.value || "").trim();
      if (!prompt) return;
      composerInput.value = "";
      sendBtn.disabled = true;
      ac_detectContext(prompt);
      UI.appendBubble("user", prompt);
      scrollChatToBottom();

      // EARLY EXIT: account-team lookup before LLM (typed path too)
      try {
        if (await ac_tryAccountTeam(prompt)) { UI.setStatus("Ready"); return; }
      } catch (_) {}

      UI.setStatus("Thinking…");
      const styledPrompt = ac_applyStyleToPrompt(prompt);
      const res = await API.chat(userText);
      if (res.ok) {
        const reply = res.reply || "";
        ac_detectContext(reply);
        UI.appendBubble("assistant", reply);
        scrollChatToBottom();
        await speakWithVisemes(reply);
        const fu = ac_contextualFollowUp(prompt, reply);
        if (fu) { UI.appendBubble("assistant", fu); scrollChatToBottom(); await speakWithVisemes(fu); }
        UI.setStatus("Ready");
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong.");
        scrollChatToBottom();
        UI.setStatus("Error");
      }
    });
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
  try { console.log("[AskChip] main.js EOF 2025-08-23"); } catch (_) {}
});
