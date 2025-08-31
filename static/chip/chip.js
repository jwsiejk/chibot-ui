
(function () {
  const state = {
    mediaRecorder: null,
    audioChunks: [],
    currentAudio: null,
    mode: (localStorage.getItem("chip_mode") || "static").toLowerCase() === "dynamic" ? "dynamic" : "static"
  };

  const AUDIO = {
    greeting: "/static/chip/audio/greeting.mp3",
    answer:   "/static/chip/audio/answer.mp3"
  };

  function getMode() { return state.mode; }
  function setMode(m) {
    state.mode = (m === "dynamic") ? "dynamic" : "static";
    try { localStorage.setItem("chip_mode", state.mode); } catch {}
    return state.mode;
  }

  function stopAudio() {
    try {
      if (state.currentAudio) {
        state.currentAudio.pause();
        state.currentAudio.currentTime = 0;
      }
    } catch {}
    state.currentAudio = null;
  }

  async function playLocal(path) {
    stopAudio();
    const a = new Audio(path);
    state.currentAudio = a;
    try { await a.play(); } catch (e) { console.warn("[chip] Audio play failed:", e); }
    return a;
  }

  function scheduleVisemes(timeline, audioEl) {
    if (!Array.isArray(timeline) || !timeline.length || !audioEl) return;
    const vis = (window.ChipViseme || window.chipViseme);
    if (vis && typeof vis.schedule === "function") vis.schedule(timeline, audioEl);
  }

  async function j(path, bodyObj) {
    const opts = bodyObj
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(bodyObj) }
      : { method: "POST" };
    const r = await fetch(path, opts);
    let data = null; try { data = await r.json(); } catch {}
    return { ok: r.ok, status: r.status, data };
  }

  async function playGreeting() {
    if (getMode() === "static") return playLocal(AUDIO.greeting);

    const { ok, data } = await j("/api/greet", {});
    if (!ok || !data) return null;

    const url = data.audio;
    const vis = data.viseme_timestamps || data.visemes || [];
    if (url) {
      stopAudio();
      const a = new Audio(url);
      state.currentAudio = a;
      scheduleVisemes(vis, a);
      try { await a.play(); } catch {}
      return a;
    }
    return null;
  }

  async function playAnswer(questionText) {
    if (getMode() === "static") return playLocal(AUDIO.answer);

    const { ok, data } = await j("/api/voice/tts_with_visemes", { question: (questionText || "") });
    if (!ok || !data) return null;

    const url = data.audio;
    const vis = data.viseme_timestamps || data.visemes || [];
    if (url) {
      stopAudio();
      const a = new Audio(url);
      state.currentAudio = a;
      scheduleVisemes(vis, a);
      try { await a.play(); } catch {}
      return a;
    }
    return null;
  }

  function initMic() {
    const recordBtn    = document.getElementById("recordBtn");
    const recordPrompt = document.getElementById("recordPrompt");
    const caption      = document.getElementById("caption");
    const statusDiv    = document.getElementById("status");
    if (!recordBtn) return;

    recordBtn.addEventListener("click", async () => {
      recordBtn.disabled = true;
      if (recordPrompt) {
        recordPrompt.classList.remove("idle");
        recordPrompt.classList.add("listening");
        recordBtn.classList.remove("blinking");
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.mediaRecorder = new MediaRecorder(stream);
        state.audioChunks = [];

        state.mediaRecorder.ondataavailable = (e) => state.audioChunks.push(e.data);

        state.mediaRecorder.onstop = async () => {
          const audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
          const formData = new FormData();
          formData.append("audio", audioBlob, "input.webm");

          if (statusDiv) statusDiv.textContent = "🧠 Chip is thinking...";
          if (recordPrompt) {
            recordPrompt.innerText = "🤔 Processing...";
            recordPrompt.classList.remove("listening");
          }

          try {
            const res = await fetch("/api/voice/tts_with_visemes", { method: "POST", body: formData });
            const reader = res.body && res.body.getReader ? res.body.getReader() : null;
            const decoder = new TextDecoder();

            let audioUrl = "";
            let visemes = [];
            let textTranscript = "";

            if (reader) {
              while (true) {
                const r = await reader.read();
                if (r.done) break;

                const text = decoder.decode(r.value);
                const parts = text.split("--frame").filter(Boolean);

                for (let i = 0; i < parts.length; i++) {
                  const seg = parts[i];
                  const pieces = seg.split(/\r?\n\r?\n/);
                  const headers = pieces.length === 3 ? pieces[1] : pieces[0];
                  const content = pieces.length === 3 ? pieces[2] : pieces[1];

                  const m = (headers || "").match(/Content-Type: (.+)/);
                  const contentType = m ? m[1] : null;
                  if (contentType === "application/json") {
                    const data = JSON.parse(content);
                    if (data.viseme_timestamps) visemes = data.viseme_timestamps;
                    if (data.audio) audioUrl = data.audio;
                    if (data.transcript) {
                      textTranscript = data.transcript;
                      if (caption) {
                        caption.textContent = textTranscript;
                        caption.style.visibility = "visible";
                        caption.style.display = "block";
                        setTimeout(() => { caption.style.display = "none"; }, 4000);
                      }
                      if (statusDiv) statusDiv.textContent = "🗣️ You said: " + textTranscript;
                    }
                  }
                }
              }
            }

            if (audioUrl) {
              stopAudio();
              const a = new Audio(audioUrl);
              state.currentAudio = a;
              scheduleVisemes(visemes, a);
              try { await a.play(); } catch {}
            } else {
              if (statusDiv) statusDiv.textContent = "⚠️ Chip could not respond.";
            }
          } catch (err) {
            console.error("❌ Chip error:", err);
            if (statusDiv) statusDiv.textContent = "❌ Chip failed to respond.";
          } finally {
            recordBtn.disabled = false;
            if (recordPrompt) recordPrompt.classList.add("idle");
          }
        };

        state.mediaRecorder.start();
        setTimeout(() => { try { state.mediaRecorder.stop(); } catch {} }, 5000);
      } catch (err) {
        console.error("❌ Mic access error:", err);
        if (statusDiv) statusDiv.textContent = "❌ Mic access failed.";
        recordBtn.disabled = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => { initMic(); });

  window.chip = window.chip || {};
  window.chip.playGreeting = playGreeting;
  window.chip.playAnswer   = playAnswer;
  window.chip.stopAudio    = stopAudio;
  window.chip.getMode      = getMode;
  window.chip.setMode      = setMode;
  window.chip.playLocal    = playLocal;
  window.chip.scheduleVisemes = scheduleVisemes;
})();
