startBtn.disabled = false;

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
      const reply = res.reply || ""; UI.appendBubble("assistant", reply); await speakWithVisemes(reply); const fu = chipFollowUp(prompt, reply); if (fu) { UI.appendBubble("assistant", fu); await speakWithVisemes(fu); } UI.setStatus("Ready");
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
          UI.setStatus("Speaking…"); micBtn.classList.add('speaking'); document.body.classList.add('speaking');
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
        micBtn.classList.remove('speaking'); document.body.classList.remove('speaking');
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
      recognizer?.stop(); recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); document.body.classList.remove('listening'); UI.setStatus("Ready"); return;
    }
    recognizer = getRecognizer();
    if (!recognizer) { UI.setStatus("SpeechRecognition unavailable"); return; }
    recognizing = true; micBtn.setAttribute('aria-pressed','true'); micBtn.classList.add('listening'); document.body.classList.add('listening'); UI.setStatus("Listening — go ahead.");

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
    recognizer.onerror = () => { recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); document.body.classList.remove('listening'); UI.setStatus("Mic error"); };
    recognizer.onend = () => { recognizing = false; micBtn.setAttribute('aria-pressed','false'); micBtn.classList.remove('listening'); document.body.classList.remove('listening'); UI.setStatus("Ready"); };
    recognizer.start();
  }

  
      // Unified Talk to Chip button: greet on first press, then toggle mic
      micBtn.addEventListener("click", async () => {
        if (!greeted) {
          micBtn.setAttribute('aria-pressed','true'); micBtn.classList.add('speaking'); 
          await dynamicGreet();
          try { await toggleMic(); } catch(e){}
          return;
        }
        await toggleMic();
      });

      // End conversation: stop recognition & clear states
      if (endBtn) {
        endBtn.addEventListener("click", () => {
          try {
            if (recognizer) { recognizer.onend = null; recognizer.onerror = null; recognizer.stop(); }
          } catch(e) {}
          recognizing = false;
          micBtn.setAttribute('aria-pressed','false');
          micBtn.classList.remove('listening'); micBtn.classList.remove('speaking'); document.body.classList.remove('speaking');
          UI.setStatus("Ended — press “Talk to Chip” to start again.");
        });
      }
    async function refreshState() {
    const me = await API.me();
    if (!me.logged_in) { UI.show("login"); UI.setUser(""); return; }
    UI.setUser(me.user?.email || "");
    if (!me.profile_complete) {
      UI.show("profile");
      const prof = await API.getProfile();
      const u = prof.user || {};
      profileName.value = u.name || ""; profileTitle.value = u.title || ""; profileRegion.value = u.region || "";
      document.getElementById("micBtn").disabled = true;
      return;
    }
    UI.show("chat");
    document.getElementById("micBtn").disabled = false;
  }

  function autoGrow(el){ const min=38; el.style.height="auto"; el.style.height=Math.max(min, el.scrollHeight)+"px"; }
});
