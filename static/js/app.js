// static/js/app.js — voice capture + barge-in + robust wiring

/* ---------- tiny helpers ---------- */
const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const log = (...a) => { try { console.log("[AskChip]", ...a); } catch {} };
function showErr(msg){ const b=$("#errorBanner"); if(!b) return; b.textContent=String(msg||"Error"); b.classList.add("show"); }
function clearErr(){ const b=$("#errorBanner"); if(!b) return; b.classList.remove("show"); b.textContent=""; }

/* ---------- CSRF (cookie → header) ---------- */
function getCookie(name){
  const m = document.cookie.match(new RegExp("(^|; )" + name.replace(/[-.$?*|{}()[]\\/+^]/g, "\\$&") + "=([^;]*)"));
  return m ? decodeURIComponent(m[2]) : null;
}
function csrfHeader(){
  const v = getCookie("csrf_token") || getCookie("csrftoken") || getCookie("XSRF-TOKEN") || getCookie("csrf");
  return v ? { "X-CSRFToken": v } : {};
}

/* ---------- state dots ---------- */
const dots = (() => {
  const set = (phase) => {
    $$(".state-dots .dot").forEach(d => d.classList.remove("active"));
    const dot = $(`.dot[data-state="${phase}"]`);
    if (dot) dot.classList.add("active");
    const s = $("#statusText"); if (s) s.textContent = phase[0].toUpperCase()+phase.slice(1);
  };
  return { set };
})();

/* ---------- globals ---------- */
let ws = null;
let hb = null;
let assistantSpeaking = false;      // server "assistant_speaking" state
let bargeTimer = null;              // pending 420ms confirm before interrupt
let stopVoice = null;               // function to stop mic/VAD/recorder

/* ---------- websocket ---------- */
function wsConnect() {
  const url = window.ASKCHIP?.api?.ws;
  if (!url) { showErr("WS url missing"); return; }

  log("WS connect →", url);
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  ws.addEventListener("open", () => {
    clearErr();
    $("#btnEnd") && ($("#btnEnd").disabled = false);

    // heartbeat ping
    const interval = (window.ASKCHIP?.config?.ws_ping_interval_ms) || 25000;
    hb = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    }, interval);

    dots.set("ready");
  });

  ws.addEventListener("message", (e) => {
    let m = null;
    try { m = JSON.parse(e.data); } catch {}
    if (!m) return;

    if (m.type === "state") {
      if (m.phase === "assistant_speaking") { assistantSpeaking = true; dots.set("responding"); }
      else if (m.phase === "assistant_end" || m.phase === "ready") { assistantSpeaking = false; dots.set("ready"); }
    } else if (m.type === "text") {
      addMsg("assistant", m.content || "");
    } else if (m.type === "error") {
      showErr(m.message || "Server error");
    } else if (m.type === "pong") {
      /* heartbeat ok */
    }
  });

  ws.addEventListener("close", () => {
    if (hb) clearInterval(hb);
    $("#btnEnd") && ($("#btnEnd").disabled = true);
    log("WS closed");
  });

  ws.addEventListener("error", () => showErr("WebSocket error"));
}

/* ---------- greet + chat ---------- */
async function greet() {
  const url = window.ASKCHIP?.api?.greet;
  if (!url) { showErr("greet url missing"); return; }
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`/api/v1/greet → ${r.status}`);
    const j = await r.json().catch(()=>({}));
    if (j?.text) addMsg("assistant", j.text);
  } catch (e) { showErr(e.message || String(e)); }
}

async function sendChat(text) {
  const url = window.ASKCHIP?.api?.chat;
  if (!url) { showErr("chat url missing"); return; }
  const body = JSON.stringify({ text: String(text||"") });
  const headers = Object.assign({ "Content-Type": "application/json" }, csrfHeader());
  try {
    const r = await fetch(url, { method: "POST", headers, credentials: "include", body });
    if (!r.ok) throw new Error(`/api/v1/chat → ${r.status}`);
    // assistant reply comes over WS
  } catch (e) { showErr(e.message || String(e)); }
}

/* ---------- chat UI ---------- */
function addMsg(role, text){
  const body = $("#chatBody"); if (!body) return;
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.textContent = text;
  body.appendChild(d);
  body.scrollTop = body.scrollHeight;
}

/* ---------- voice capture (VAD + one blob per turn) ---------- */
async function startVoiceCapture(onText){
  // 1) Mic with echo/noise control, 48 kHz
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { sampleRate: 48000, channelCount: 1, echoCancellation: true, noiseSuppression: true }
  });

  // 2) WebAudio for simple VAD (RMS threshold + trailing silence)
  const ctx   = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  const src   = ctx.createMediaStreamSource(stream);
  const proc  = ctx.createScriptProcessor(2048, 1, 1); // ok here; we can migrate to Worklet later

  // Tunables (match spec defaults)
  const START_RMS = 0.015; // speech starts above this
  const STOP_RMS  = 0.008; // consider silence below this
  const MIN_MS    = 220;   // min speech duration
  const SILENCE_MS= 320;   // trailing silence to end
  const BARGE_CONFIRM_MS = 420;

  let speaking = false, lastSpeech = 0, speechStart = 0, rmsSum = 0, rmsMax = 0, rmsN = 0;

  // 3) Recorder (one blob per turn)
  const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 128000 });
  let chunks = [];

  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstop = async () => {
    try {
      const blob = new Blob(chunks, { type: "audio/webm" });
      chunks = [];
      const speech_ms = Date.now() - speechStart;
      if (speech_ms < MIN_MS) return; // too short

      // 4) POST to /api/v1/voice/stt
      const fd = new FormData();
      fd.append("file", blob, "turn.webm");
      fd.append("mime", "audio/webm;codecs=opus");
      fd.append("meta", JSON.stringify({
        speech_ms,
        avg_rms: rmsSum / Math.max(1, rmsN),
        max_rms: rmsMax,
        language: "en"
      }));
      const headers = Object.assign({}, csrfHeader()); // do NOT set Content-Type with FormData
      const r = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, headers, credentials: "include" });
      if (!r.ok) throw new Error(`/api/v1/voice/stt → ${r.status}`);
      const j = await r.json();
      if (j?.text && typeof onText === "function") onText(j.text);
    } catch (e) { showErr(e.message || String(e)); }
  };

  proc.onaudioprocess = (ev) => {
    const buf = ev.inputBuffer.getChannelData(0);
    // quick RMS
    let sum = 0;
    for (let i = 0; i < buf.length; i++) { const v = buf[i]; sum += v*v; }
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now();

    // ----- soft barge-in (if assistant is speaking) -----
    if (!speaking && assistantSpeaking && rms >= START_RMS && !bargeTimer) {
      // Start confirm window; if speech persists, send interrupt
      bargeTimer = setTimeout(() => {
        if (assistantSpeaking) {
          try {
            ws && ws.readyState === WebSocket.OPEN &&
              ws.send(JSON.stringify({ type:"control", cmd:"interrupt", reason:"vad" }));
          } catch {}
        }
        bargeTimer = null;
      }, BARGE_CONFIRM_MS);
    }
    if (!assistantSpeaking && bargeTimer) { clearTimeout(bargeTimer); bargeTimer = null; }

    // ----- turn VAD -----
    if (!speaking && rms >= START_RMS) {
      speaking = true;
      lastSpeech = now;
      speechStart = Date.now();
      rmsSum = 0; rmsN = 0; rmsMax = 0;
      chunks = [];
      try { rec.start(); } catch {}
      dots.set("listening");
    }

    if (speaking) {
      rmsSum += rms; rmsN++; if (rms > rmsMax) rmsMax = rms;
      if (rms >= STOP_RMS) lastSpeech = now;

      // end after trailing silence
      if ((now - lastSpeech) > SILENCE_MS) {
        speaking = false;
        try { rec.stop(); } catch {}
        dots.set("thinking");
      }
    }
  };

  src.connect(proc); proc.connect(ctx.destination);

  // Stop function to clean up
  return () => {
    try { proc.disconnect(); src.disconnect(); ctx.close(); } catch {}
    try { stream.getTracks().forEach(t => t.stop()); } catch {}
    if (bargeTimer) { clearTimeout(bargeTimer); bargeTimer = null; }
  };
}

/* ---------- wire buttons ---------- */
async function onStartClicked(){
  try {
    // 1) WS + greet
    wsConnect();
    await greet();

    // 2) Voice capture (once)
    if (!stopVoice) {
      stopVoice = await startVoiceCapture((text) => {
        addMsg("user", text);
        // optional: also post to /api/v1/chat to drive LLM lane explicitly
        sendChat(text);
      });
    }
  } catch (e) { showErr(e.message || String(e)); }
}

function wireUI(){
  const start = $("#btnStart");
  const end   = $("#btnEnd");
  const mute  = $("#btnMute");
  const chatT = $("#btnChat");
  const send  = $("#chatSend");
  const input = $("#chatInput");

  if (start) start.addEventListener("click", async () => {
    start.disabled = true;
    await onStartClicked();
    start.disabled = false;
  });

  if (end) end.addEventListener("click", () => {
    try { ws && ws.close(); } catch {}
    if (stopVoice) { try { stopVoice(); } catch {} stopVoice = null; }
    dots.set("ready");
  });

  if (mute) {
    mute.addEventListener("click", (ev) => {
      const pressed = ev.currentTarget.getAttribute("aria-pressed") === "true";
      ev.currentTarget.setAttribute("aria-pressed", (!pressed).toString());
      ev.currentTarget.textContent = (!pressed) ? "Audio: Off" : "Audio: On";
      log("Mute toggled →", !pressed ? "off" : "on");
    });
  }

  if (chatT) {
    chatT.addEventListener("click", (ev) => {
      const pressed = ev.currentTarget.getAttribute("aria-pressed") === "true";
      ev.currentTarget.setAttribute("aria-pressed", (!pressed).toString());
      $("#chatPane").style.display = pressed ? "none" : "flex";
    });
  }

  if (send && input) {
    send.addEventListener("click", async () => {
      const text = input.value.trim();
      if (!text) return;
      addMsg("user", text);
      input.value = "";
      await sendChat(text);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send.click();
      }
    });
  }

  // initial UI
  dots.set("ready");
}

/* ---------- boot ---------- */
window.addEventListener("DOMContentLoaded", () => {
  try {
    // Initial mouth sprite (your 2D pack naming)
    const base = window.ASKCHIP?.assets?.visemeBase || "/static/visemes/chip-2d-pack/";
    const m = $("#chipMouth");
    if (m) { m.src = base + "mouth_neutral.png"; m.style.display = "block"; }
    wireUI();
    clearErr();
  } catch (e) { showErr(e.message || String(e)); }
});



// Phase1 patch: wire audio playback, visemes, and live apply signals
import { ChunkedAudioPlayer } from './audio_player.js';
import { VisemeAnimator } from './viseme_animator.js';

window.__chip = window.__chip || {};

(function() {
  const audioEl = document.getElementById('chipAudio') || (function(){
    const a = document.createElement('audio');
    a.id = 'chipAudio';
    a.autoplay = true;
    a.style.display = 'none';
    document.body.appendChild(a);
    return a;
  })();

  const mouthEl = document.getElementById('chipMouth') || null;
  const player = new ChunkedAudioPlayer(audioEl);
  const visemes = new VisemeAnimator(mouthEl);

  let ws = null;
  let pendingVisemes = null;

  function connectWS() {
    if (ws && ws.readyState === WebSocket.OPEN) return ws;
    const url = (window.CHAT_WS_URL || (location.origin.replace(/^http/,'ws') + '/ws/v1/chat'));
    ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => console.log('[WS] open');
    ws.onclose = () => console.log('[WS] close');
    ws.onerror = (e) => console.error('[WS] error', e);
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data);
          handleMessage(msg);
        } catch (e) {
          console.warn('Non-JSON WS message', ev.data);
        }
      } else {
        // If server sends raw binary frames, treat them as audio chunks
        player.append(ev.data);
      }
    };
    return ws;
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'state':
        if (msg.phase === 'assistant_speaking') {
          player.start();
          if (pendingVisemes) visemes.play(pendingVisemes, 0);
        }
        if (msg.phase === 'assistant_end') {
          // end of turn
          setTimeout(() => player.stop(), 50);
          visemes.stop();
        }
        break;
      case 'text':
        // no-op here
        break;
      case 'audio_chunk':
        player.append(base64ToArrayBuffer(msg.data));
        break;
      case 'visemes':
        pendingVisemes = msg.items || msg.visemes || null;
        break;
      case 'end':
        setTimeout(() => player.stop(), 50);
        visemes.stop();
        break;
      case 'config_updated':
        applyConfigToUI(msg.config || {});
        break;
      case 'layout_updated':
        applyLayout(msg.layout || {});
        break;
      default:
        // ignore
        break;
    }
  }

  function base64ToArrayBuffer(b64) {
    const binary_string = window.atob(b64);
    const len = binary_string.length;
    const bytes = new Uint8Array(len);
    for (let i=0; i<len; i++) bytes[i] = binary_string.charCodeAt(i);
    return bytes.buffer;
  }

  function applyConfigToUI(cfg) {
    // Minimal: show/hide instruction strip or dots if present
    const strip = document.getElementById('instructionStrip');
    if (strip && 'show_instruction_strip' in cfg) {
      strip.style.display = cfg.show_instruction_strip ? '' : 'none';
    }
    const dots = document.getElementById('stateDots');
    if (dots && 'show_state_dots' in cfg) {
      dots.style.display = cfg.show_state_dots ? '' : 'none';
    }
    console.log('[Config] live applied', cfg);
  }

  function applyLayout(layout) {
    // Placeholder: could set CSS vars or toggle classes
    if (layout && layout.theme) document.documentElement.setAttribute('data-theme', layout.theme);
    console.log('[Layout] live applied');
  }

  // Expose a start method if not already
  window.__chip.ensureWS = connectWS;

  // Auto-connect if Start button not required here; otherwise UI should call ensureWS on Start
  setTimeout(connectWS, 0);
})();
