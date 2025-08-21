document.addEventListener("DOMContentLoaded", async () => {
  // --- State ---
  let greeted = false;
  let recognizer = null;
  let recognizing = false;

  // --- Elements ---
  const loginForm    = document.getElementById("loginForm");
  const loginEmail   = document.getElementById("loginEmail");
  const logoutBtn    = document.getElementById("logoutBtn");
  const profileBtn   = document.getElementById("profileBtn");

  const profileForm  = document.getElementById("profileForm");
  const profileName  = document.getElementById("profileName");
  const profileTitle = document.getElementById("profileTitle");
  const profileRegion= document.getElementById("profileRegion");

  const composer      = document.getElementById("composer");
  const composerInput = document.getElementById("composerInput");
  const sendBtn       = document.getElementById("sendBtn");

  const micBtn     = document.getElementById("micBtn");
  const endBtn     = document.getElementById("endBtn");
  const chipCanvas = document.getElementById("chipCanvas");
  const chipSprite = document.getElementById("chipSprite");

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

  // --- Conversation helpers ---

  function chipFollowUp(prompt, reply) {
    try {
      if (!prompt) return "";
      const p = (prompt || "").toLowerCase();
      const replyEndsQuestion = /[?]$/.test(reply || "");
      if (replyEndsQuestion) return "";
      if (Math.random() < 0.6) return ""; // be selective

      const nudges = [
        "Want me to go deeper on that?",
        "Should I lay out the steps?",
        "Want a quick checklist?",
        "Need the gotchas before you start?",
        "Want me to sanity‑check your plan?"
      ];

      if (p.includes("install") || p.includes("setup") || p.includes("configure")) {
        return "Want the exact install steps?";
      }
      if (p.includes("troubleshoot") || p.includes("error") || p.includes("fail")) {
        return "Want the quick triage path?";
      }
      if (p.includes("design") || p.includes("architecture")) {
        return "Want a simple diagram of the flow?";
      }
      return nudges[Math.floor(Math.random() * nudges.length)];
    } catch {
      return "";
    }
  }

  async function dynamicGreet() {
    UI.setStatus("Greeting…");
    // Ask backend for a dynamic greeting, but ALWAYS synthesize client-side speech
    let greetText = "Hey—Chip here. What are we tackling today?";
    try {
      const res = await API.greet({ dynamic: true });
      if (res && res.text) greetText = res.text;
    } catch (_) {}
    UI.appendBubble("assistant", greetText);
    try { await speakWithVisemes(greetText); } catch (_) {}
    greeted = true;
    UI.setStatus("Listening…");
  }

  async function speakWithVisemes(text) {
    try {
      const resp = await fetch("/api/tts_with_visemes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      const data = await resp.json();
      if (data.ok) {
        let audioEl = null;
        if (data.audio) {
          const url = "data:audio/mpeg;base64," + data.audio;
          audioEl = new Audio(url);
          await new Promise((resolve) => {
            audioEl.onloadedmetadata = resolve;
            audioEl.onerror = resolve;
          });
          UI.setStatus("Speaking…");
          if (micBtn) micBtn.classList.add("speaking");
          audioEl.play().catch(() => {});
        } else if ("speechSynthesis" in window) {
          const u = new SpeechSynthesisUtterance(text);
          UI.setStatus("Speaking…");
          if (micBtn) micBtn.classList.add("speaking");
          document.body.classList.add("speaking");
          speechSynthesis.speak(u);
        }

        if (typeof Viseme !== "undefined") {
          const schedule = (data.visemes || []).map(x => ({ t: x.t, v: x.v }));
          Viseme.animate(schedule, audioEl, { relative: data.relative !== false });
        }

        if (audioEl) {
          await new Promise((resolve) => {
            audioEl.onended = resolve; audioEl.onerror = resolve;
          });
        } else {
          const ms = Math.max(800, (text.trim().split(/\s+/).length) * 160);
          await new Promise(r => setTimeout(r, ms));
        }

        if (typeof Viseme !== "undefined") Viseme.stop();
        if (micBtn) micBtn.classList.remove("speaking");
        document.body.classList.remove("speaking");
        UI.setStatus("Ready");
        return;
      }
    } catch (_) {}

    // Final fallback: browser TTS
    if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(text);
      speechSynthesis.speak(u);
    }
  }

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
    if (!supportsSpeechRecognition()) {
      UI.setStatus("Browser speech recognition not available");
      return;
    }
    if (recognizing) {
      try { recognizer && recognizer.stop(); } catch (_) {}
      recognizing = false;
      if (micBtn) {
        micBtn.setAttribute("aria-pressed", "false");
        micBtn.classList.remove("listening");
      }
      document.body.classList.remove("listening");
      UI.setStatus("Ready");
      return;
    }

    recognizer = getRecognizer();
    if (!recognizer) {
      UI.setStatus("SpeechRecognition unavailable");
      return;
    }

    recognizing = true;
    if (micBtn) {
      micBtn.setAttribute("aria-pressed", "true");
      micBtn.classList.add("listening");
    }
    document.body.classList.add("listening");
    UI.setStatus("Listening — go ahead.");

    recognizer.onresult = async (ev) => {
      const transcript = (ev.results && ev.results[0] && ev.results[0][0] && ev.results[0][0].transcript || "").trim();
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      if (!transcript) return;

      UI.appendBubble("user", transcript);
      UI.setStatus("Thinking…");
      const res = await API.chat(transcript);
      if (res.ok) {
        const reply = res.reply || "";
        UI.appendBubble("assistant", reply);
        await speakWithVisemes(reply);
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong.");
        UI.setStatus("Error");
      }
    };

    recognizer.onerror = () => {
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) micBtn.classList.remove("listening");
      document.body.classList.remove("listening");
      UI.setStatus("Mic error");
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
      const res = await API.login(email);
      if (res.ok) {
        UI.setUser(email);
        await refreshState();
      } else {
        UI.setStatus(res.error || "Login failed");
      }
    });
  }

  if (profileBtn) {
    profileBtn.addEventListener("click", () => {
      UI.show("profile");
      UI.setStatus("Please fill out your profile to continue");
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      await API.logout();
      UI.setUser("");
      UI.show("login");
      UI.setStatus("Logged out");
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
      if (res.ok) {
        await refreshState();
        UI.setStatus("Profile saved");
      } else {
        UI.setStatus(res.error || "Save failed");
      }
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
      UI.appendBubble("user", prompt);
      UI.setStatus("Thinking…");
      const res = await API.chat(prompt);
      if (res.ok) {
        const reply = res.reply || "";
        UI.appendBubble("assistant", reply);
        await speakWithVisemes(reply);
        const fu = chipFollowUp(prompt, reply);
        if (fu) { UI.appendBubble("assistant", fu); await speakWithVisemes(fu); }
        UI.setStatus("Ready");
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong.");
        UI.setStatus("Error");
      }
    });
  }

  if (micBtn) {
    // First press greets; subsequent presses toggle mic
    micBtn.addEventListener("click", async () => {
      if (!greeted) {
        micBtn.setAttribute("aria-pressed", "true");
        micBtn.classList.add("speaking");
        await dynamicGreet();
        try { await toggleMic(); } catch (_) {}
        return;
      }
      await toggleMic();
    });
  }

  if (endBtn) {
    endBtn.addEventListener("click", () => {
      try {
        if (recognizer) { recognizer.onend = null; recognizer.onerror = null; recognizer.stop(); }
      } catch (_) {}
      recognizing = false;
      if (micBtn) micBtn.setAttribute("aria-pressed", "false");
      if (micBtn) {
        micBtn.classList.remove("listening");
        micBtn.classList.remove("speaking");
      }
      document.body.classList.remove("speaking");
      UI.setStatus("Ended — press “Talk to Chip” to start again.");
    });
  }

  async function refreshState() {
    const me = await API.me();
    if (!me.logged_in) {
      UI.show("login");
      UI.setUser("");
      return;
    }
    UI.setUser(me.user && me.user.email || "");
    if (!me.profile_complete) {
      UI.show("profile");
      UI.setStatus("Please fill out your profile to continue");
      const prof = await API.getProfile();
      const u = prof.user || {};
      if (profileName)  profileName.value  = u.name  || "";
      if (profileTitle) profileTitle.value = u.title || ""
      if (profileRegion) profileRegion.value = u.region || "";
      if (micBtn) micBtn.disabled = true;
      return;
    }
    UI.show("chat");
    UI.setStatus("Ready");
    if (micBtn) micBtn.disabled = false;
  }

  function autoGrow(el) {
    const min = 38;
    el.style.height = "auto";
    el.style.height = Math.max(min, el.scrollHeight) + "px";
  }

  // Initial state sync
  await refreshState();
});
