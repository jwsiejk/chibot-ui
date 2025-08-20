document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById("loginForm");
  const loginEmail = document.getElementById("loginEmail");
  const logoutBtn = document.getElementById("logoutBtn");

  const profileForm = document.getElementById("profileForm");
  const profileName = document.getElementById("profileName");
  const profileTitle = document.getElementById("profileTitle");
  const profileRegion = document.getElementById("profileRegion");

  const startBtn = document.getElementById("startBtn");
  const composer = document.getElementById("composer");
  const composerInput = document.getElementById("composerInput");
  const sendBtn = document.getElementById("sendBtn");

  const micBtn = document.getElementById("micBtn");
  const visemeCanvas = document.getElementById("visemeCanvas");
  const stage = new VisemeStage(visemeCanvas);

  refreshState();

  loginForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const email = (loginEmail.value || "").trim();
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

  logoutBtn.addEventListener("click", async () => {
    await API.logout();
    UI.setUser("");
    UI.show("login");
    UI.setStatus("Logged out");
  });

  profileForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = {
      name: (profileName.value || "").trim(),
      title: (profileTitle.value || "").trim(),
      region: (profileRegion.value || "").trim(),
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

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    UI.setStatus("Starting…");
    const res = await API.greet();
    if (res.ok) {
      if (res.text) UI.appendBubble("assistant", res.text);
      if (res.audioUrl) {
        try {
          const audio = new Audio(res.audioUrl);
          await audio.play();
        } catch (e) {}
      }
      UI.setStatus("Ready");
    } else {
      UI.setStatus(res.error || "Greeting failed");
    }
    startBtn.disabled = false;
  });

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
    const chatRes = await API.chat(prompt);
    if (chatRes.ok) {
      const reply = chatRes.reply || "";
      UI.appendBubble("assistant", reply);
      UI.setStatus("Speaking…");
      await speakWithVisemes(reply);
      UI.setStatus("Ready");
    } else {
      UI.appendBubble("assistant", chatRes.error || "Something went wrong.");
      UI.setStatus("Error");
    }
  });

  // Voice: STT + TTS with visemes + fallbacks
  let recognizer = null;
  let recognizing = false;
  let playingAudio = null;

  function supportsSpeechRecognition() {
    return 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window;
  }

  function getRecognizer() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return null;
    const r = new SR();
    r.lang = 'en-US';
    r.interimResults = false;
    r.maxAlternatives = 1;
    return r;
  }

  async function speakWithVisemes(text) {
    try {
      const res = await API.ttsWithVisemes(text);
      if (res.ok && !res.fallback && res.audio) {
        const blob = b64ToBlob(res.audio, 'audio/mpeg');
        const url = URL.createObjectURL(blob);
        if (playingAudio) { try { playingAudio.pause(); } catch(e){} }
        const audio = new Audio(url);
        playingAudio = audio;
        micBtn.classList.add('speaking');
        const schedule = Array.isArray(res.visemes) ? res.visemes : [];
        stage.animate(schedule, audio);
        await audio.play();
        await new Promise(resolve => { audio.onended = resolve; audio.onerror = resolve; });
        micBtn.classList.remove('speaking');
        URL.revokeObjectURL(url);
        stage.stop();
        return;
      }
      await speakBrowser(text);
    } catch(e) {
      await speakBrowser(text);
    }
  }

  async function speakBrowser(text) {
    const u = new SpeechSynthesisUtterance(text);
    try {
      micBtn.classList.add('speaking');
      const audio = { duration: Math.min(8, Math.max(1.5, text.split(/\s+/).length * 0.35)), currentTime: 0, ended: false };
      let start = null;
      stage.animate(makeHeuristicSchedule(text), audio);
      await new Promise(resolve => {
        const tick = (ts) => {
          if (!start) start = ts;
          const elapsed = (ts - start) / 1000;
          audio.currentTime = Math.min(audio.duration, elapsed);
          if (elapsed >= audio.duration) { audio.ended = True; resolve(); return; }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
        window.speechSynthesis.speak(u);
        u.onend = () => { resolve(); };
        u.onerror = () => { resolve(); };
      });
    } finally {
      micBtn.classList.remove('speaking');
      stage.stop();
    }
  }

  function makeHeuristicSchedule(text) {
    const letters = (text || "").split("");
    const base = ["REST","AI","E","O","S","R","M","F","L","N","REST"];
    const steps = Math.min(10, Math.max(3, Math.floor(letters.length/6)+3));
    const out = [];
    for (let i=0;i<steps;i++){
      out.push({ t: i/Math.max(1,steps-1), id: base[i % base.length] });
    }
    if (out[out.length-1].id !== "REST") out.push({t:1, id:"REST"});
    return out;
  }

  function stopSpeaking() {
    try { if (playingAudio) { playingAudio.pause(); } } catch(e){}
    if ('speechSynthesis' in window && window.speechSynthesis.speaking) {
      try { window.speechSynthesis.cancel(); } catch(e){}
    }
    micBtn.classList.remove('speaking');
    stage.stop();
  }

  function toggleMic() {
    if (!supportsSpeechRecognition()) {
      UI.setStatus("Browser speech recognition not available");
      return;
    }
    if (recognizing) {
      recognizer?.stop();
      recognizing = false;
      micBtn.setAttribute('aria-pressed', 'false');
      micBtn.classList.remove('listening');
      UI.setStatus("Ready");
      return;
    }
    recognizer = getRecognizer();
    if (!recognizer) {
      UI.setStatus("SpeechRecognition unavailable");
      return;
    }
    recognizing = true;
    micBtn.setAttribute('aria-pressed', 'true');
    micBtn.classList.add('listening');
    UI.setStatus("Listening…");

    recognizer.onresult = async (ev) => {
      const transcript = (ev.results?.[0]?.[0]?.transcript || "").trim();
      if (!transcript) return;
      recognizing = false;
      micBtn.setAttribute('aria-pressed', 'false');
      micBtn.classList.remove('listening');

      UI.appendBubble("user", transcript);
      UI.setStatus("Thinking…");
      const res = await API.chat(transcript);
      if (res.ok) {
        const reply = res.reply || "";
        UI.appendBubble("assistant", reply);
        UI.setStatus("Speaking…");
        await speakWithVisemes(reply);
        UI.setStatus("Ready");
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong.");
        UI.setStatus("Error");
      }
    };
    recognizer.onerror = () => {
        recognizing = false;
        micBtn.setAttribute('aria-pressed', 'false');
        micBtn.classList.remove('listening');
        UI.setStatus("Mic error");
    };
    recognizer.onend = () => {
        recognizing = false;
        micBtn.setAttribute('aria-pressed', 'false');
        micBtn.classList.remove('listening');
        UI.setStatus("Ready");
    };
    recognizer.start();
  }

  micBtn.addEventListener("click", () => {
    if (micBtn.classList.contains('speaking')) {
      stopSpeaking();
      return;
    }
    toggleMic();
  });

  async function refreshState() {
    const me = await API.me();
    if (!me.logged_in) {
      UI.show("login"); UI.setUser(""); return;
    }
    UI.setUser(me.user?.email || "");
    if (!me.profile_complete) {
      UI.show("profile");
      const prof = await API.getProfile();
      const u = prof.user || {};
      profileName.value = u.name || "";
      profileTitle.value = u.title || "";
      profileRegion.value = u.region || "";
      document.getElementById("startBtn").disabled = true;
      return;
    }
    UI.show("chat");
    document.getElementById("startBtn").disabled = false;
  }

  function autoGrow(el) {
    const min = 38;
    el.style.height = "auto";
    el.style.height = Math.max(min, el.scrollHeight) + "px";
  }

  function b64ToBlob(b64Data, contentType) {
    const byteCharacters = atob(b64Data);
    const byteArrays = [];
    const sliceSize = 1024;
    for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
      const slice = byteCharacters.slice(offset, offset + sliceSize);
      const byteNumbers = new Array(slice.length);
      for (let i = 0; i < slice.length; i++) {
        byteNumbers[i] = slice.charCodeAt(i);
      }
      byteArrays.push(new Uint8Array(byteNumbers));
    }
    return new Blob(byteArrays, {type: contentType || ''});
  }
});
