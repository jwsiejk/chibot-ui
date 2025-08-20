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
  const chipCanvas = document.getElementById("chipCanvas");
  const chipSprite = document.getElementById("chipSprite");

  (function ensureSprite(){
    const url = "/static/chip/img/chip.png";
    chipSprite.src = url;
    chipSprite.onerror = () => { chipSprite.style.display="none"; };
  })();
  Viseme.init(chipCanvas);

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
        try { const audio = new Audio(res.audioUrl); await audio.play(); } catch (e) {}
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
    const res = await API.chat(prompt);
    if (res.ok) {
      const reply = res.reply || "";
      UI.appendBubble("assistant", reply);
      UI.setStatus("Ready");
    } else {
      UI.appendBubble("assistant", res.error || "Something went wrong.");
      UI.setStatus("Error");
    }
  });

  // Voice: mic capture + TTS with visemes
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
    r.lang = 'en-US'; r.interimResults = false; r.maxAlternatives = 1;
    return r;
  }

  async function speakWithVisemes(text){
    try {
      const res = await fetch('/api/tts_with_visemes', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ text })});
      const data = await res.json();
      if (data.ok) {
        let audioEl = null;
        if (data.audio) {
          const url = "data:audio/mpeg;base64," + data.audio;
          audioEl = new Audio(url);
          await new Promise((resolve)=>{ audioEl.onloadedmetadata = resolve; audioEl.onerror = resolve; });
          UI.setStatus("Speaking…");
          micBtn.classList.add('speaking');
          audioEl.play().catch(()=>{});
        } else if ('speechSynthesis' in window) {
          const u = new SpeechSynthesisUtterance(text);
          UI.setStatus("Speaking…"); micBtn.classList.add('speaking');
          speechSynthesis.speak(u);
        }
        const schedule = (data.visemes || []).map(x => ({t: x.t, v: x.v}));
        Viseme.animate(schedule, audioEl, {relative: data.relative !== false});
        if (audioEl) {
          await new Promise(resolve => { audioEl.onended = resolve; audioEl.onerror = resolve; });
        } else {
          await new Promise(r => setTimeout(r, Math.max(800, (text.trim().split(/\s+/).length)*160)));
        }
        Viseme.stop();
        micBtn.classList.remove('speaking');
        UI.setStatus("Ready");
        return;
      }
    } catch(e){}
    if ('speechSynthesis' in window) {
      const u = new SpeechSynthesisUtterance(text);
      speechSynthesis.speak(u);
    }
  }

  function toggleMic() {
    if (!supportsSpeechRecognition()) { UI.setStatus("Browser speech recognition not available"); return; }
    if (recognizing) {
      recognizer?.stop(); recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); UI.setStatus("Ready"); return;
    }
    recognizer = getRecognizer();
    if (!recognizer) { UI.setStatus("SpeechRecognition unavailable"); return; }
    recognizing = true; micBtn.setAttribute('aria-pressed','true'); micBtn.classList.add('listening'); UI.setStatus("Listening…");

    recognizer.onresult = async (ev) => {
      const transcript = (ev.results?.[0]?.[0]?.transcript || "").trim();
      recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening');
      if (!transcript) return;

      UI.appendBubble("user", transcript);
      UI.setStatus("Thinking…");
      const res = await API.chat(transcript);
      if (res.ok) {
        const reply = res.reply || ""; UI.appendBubble("assistant", reply);
        await speakWithVisemes(reply);
      } else {
        UI.appendBubble("assistant", res.error || "Something went wrong."); UI.setStatus("Error");
      }
    };
    recognizer.onerror = () => { recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); UI.setStatus("Mic error"); };
    recognizer.onend = () => { recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); UI.setStatus("Ready"); };
    recognizer.start();
  }

  micBtn.addEventListener("click", toggleMic);

  async function refreshState() {
    const me = await API.me();
    if (!me.logged_in) { UI.show("login"); UI.setUser(""); return; }
    UI.setUser(me.user?.email || "");
    if (!me.profile_complete) {
      UI.show("profile");
      const prof = await API.getProfile();
      const u = prof.user || {};
      profileName.value = u.name || ""; profileTitle.value = u.title || ""; profileRegion.value = u.region || "";
      document.getElementById("startBtn").disabled = true;
      return;
    }
    UI.show("chat");
    document.getElementById("startBtn").disabled = false;
  }

  function autoGrow(el){ const min=38; el.style.height="auto"; el.style.height=Math.max(min, el.scrollHeight)+"px"; }
});
