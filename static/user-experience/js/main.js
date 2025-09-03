// main.js — DIAGNOSTIC BUILD (adds deep logging + fixes VAD→recording→STT chain)
console.log("[AC][BOOT] main.js diagnostic build loaded");

// Core & modules
import { $, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import { _chipGuide, _chipSetState, _chipStep } from "./core/state.js";
import { appendMessage } from "./chat/ui.js";
import { sendChat, handleVoiceOnceResponse, wireChatLane } from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks, isArmed as vadIsArmed } from "./voice/vad.js";
import { setStream as setRecordStream, _vm_startRecording, _vm_stopRecording, isRecording as recIsRecording } from "./voice/record.js";
import { loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

// -------------------- Debug HUD --------------------
const HUD_ID = "ac-debug-hud";
function hud(){
  let d = document.getElementById(HUD_ID);
  if (!d){
    d = document.createElement("div");
    d.id = HUD_ID;
    d.style.position = "fixed";
    d.style.bottom = "8px";
    d.style.right = "8px";
    d.style.zIndex = "99999";
    d.style.font = "12px/1.3 system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
    d.style.background = "rgba(0,0,0,0.7)";
    d.style.color = "#cbe4ff";
    d.style.border = "1px solid rgba(255,255,255,0.2)";
    d.style.padding = "8px 10px";
    d.style.borderRadius = "6px";
    d.style.maxWidth = "34vw";
    d.style.pointerEvents = "none";
    document.body.appendChild(d);
  }
  return d;
}
const _dbg = {
  lane: "text",
  mic: { haveStream:false, permission:"unknown", armed:false, rec:false },
  counts: { vadStart:0, vadStop:0, stt:0, wsSends:0 },
  last: { ws:"", err:"" }
};
function log(msg, data){
  try { console.log("[AC]", msg, data||""); } catch {}
  try { 
    const d = hud();
    let h = "<b>Ask Chip · Debug</b><br>";
    h += "lane="+_dbg.lane+" | mic="+(_dbg.mic.armed?"armed":"off")+", rec="+(_dbg.mic.rec?"on":"off")+" | perm="+_dbg.mic.permission+"<br>";
    h += "vadStart="+_dbg.counts.vadStart+" vadStop="+_dbg.counts.vadStop+" sttPosts="+_dbg.counts.stt+" wsSends="+_dbg.counts.wsSends+"<br>";
    if (_dbg.last.ws) h += "ws:"+_dbg.last.ws+"<br>";
    if (_dbg.last.err) h += "<span style='color:#ff9b9b'>err:"+_dbg.last.err+"</span><br>";
    if (msg) h += "<span style='color:#e1ffd2'>"+String(msg)+"</span>";
    d.innerHTML = h;
  } catch {}
}
window.acDebug = { state:_dbg, log, hud };

// -------------------- DOM helpers --------------------
const el = (id) => document.getElementById(id);
function onReady(fn){ if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once:true }); else fn(); }
function bindClick(node, handler){
  if (!node) return false;
  node.addEventListener("click", handler);
  node.addEventListener("keydown", function(e){ if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(e); } });
  node.setAttribute("data-bound", "1");
  return true;
}

// -------------------- Elements --------------------
let BTN_START, BTN_AUDIO, BTN_END, BTN_CHAT, CHAT_MENU, BADGE;
let chatPanel, chatText, chatTTS;

// -------------------- Lanes --------------------
let chatLane = (function(){ try { return (localStorage.getItem("chatLane") || "text") === "live" ? "live" : "text"; } catch(e) { return "text"; } })();
_dbg.lane = chatLane;
const getChatLane = function(){ return chatLane; };
const setChatLane = function(lane){ chatLane = lane === "live" ? "live" : "text"; _dbg.lane = chatLane; try { localStorage.setItem("chatLane", chatLane); } catch(e){} refreshLaneUI(); };

function badge(){ if (BADGE) BADGE.textContent = chatLane === "live" ? "Live" : "Text"; }

// -------------------- Wiring / boot --------------------
function initUI(){
  BTN_START=el("zStart"); BTN_AUDIO=el("zAudio"); BTN_END=el("zEnd"); BTN_CHAT=el("zChat"); CHAT_MENU=el("zChatMenu"); BADGE=el("laneBadge");
  chatPanel=el("chatPanel"); chatText=el("chatText"); chatTTS=el("chatTTS");
  try { setToolbarHeightVar(); } catch(e){}

  bindClick(BTN_CHAT, onChatToggle);
  bindClick(BTN_START, onStartClicked);
  bindClick(BTN_AUDIO, onAudioClicked);
  bindClick(BTN_END, endSession);

  // Lane menu
  function openMenu(ev){
    if (!CHAT_MENU||!BTN_CHAT) return;
    const r=BTN_CHAT.getBoundingClientRect();
    CHAT_MENU.style.left=Math.max(12, r.left+r.width/2-80)+"px";
    CHAT_MENU.style.bottom=(window.innerHeight-r.top+10)+"px";
    CHAT_MENU.classList.remove("hidden");
    ev && ev.preventDefault();
  }
  function closeMenu(){ if (CHAT_MENU) CHAT_MENU.classList.add("hidden"); }
  if (BTN_CHAT){
    BTN_CHAT.addEventListener("contextmenu", function(e){ e.preventDefault(); openMenu(e); });
    BTN_CHAT.addEventListener("auxclick", function(e){ if (e.button===1){ e.preventDefault(); openMenu(e);} });
  }
  bindClick(el("laneText"), function(){ setChatLane("text"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); log("Lane set → text"); });
  bindClick(el("laneLive"), function(){ setChatLane("live"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); log("Lane set → live"); });
  document.addEventListener("click", function(e){ if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  // Compose
  bindClick(el("chatSendBtn"), function(){ const input=el("chatInput"); if (!input) return; const v=input.value; if (v && v.trim()) { sendChat(v); input.value=""; } });
  const chatInputEl = el("chatInput");
  if (chatInputEl) chatInputEl.addEventListener("keydown", function(e){ const input=el("chatInput"); if (!input) return; if (e.key==="Enter" && !e.shiftKey){ e.preventDefault(); const v=input.value; if (v&&v.trim()){ sendChat(v); input.value=""; } } });

  // Mic UI + guide
  setMicUIUpdater(function(on, recording){
    _dbg.mic.armed = !!on; _dbg.mic.rec = !!recording;
    const lbl = BTN_AUDIO && BTN_AUDIO.querySelector("span:last-child");
    if (lbl) lbl.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
    log("Mic UI update", { on, recording });
  });
  setVoiceGuide(function(text){ _chipGuide(text); });

  // Wire VAD → recorder
  setRecordCallbacks(
    async function onStart(){
      _dbg.counts.vadStart++; log("VAD start");
      try { window.dispatchEvent(new Event("chip:bargein")); } catch {}
      try { await _vm_startRecording(); log("Recorder started"); } catch (e) { log("startRecording failed", e && e.message); }
    },
    async function onStop(){
      _dbg.counts.vadStop++; log("VAD stop");
      try {
        await _vm_stopRecording(async function(blob, durMs){
          log("Recorder stopped", { durMs: durMs, size: blob && blob.size });
          _dbg.counts.stt++;
          try { await handleVoiceOnceResponse({ blob: blob, durMs: durMs }); } catch(e){ log("voice handler failed", e && e.message); }
        });
      } catch(e){ log("stopRecording failed", e && e.message); }
    }
  );

  wireChatLane(getChatLane, setChatLane);
  refreshLaneUI();

  // Permission observer (best effort)
  try {
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: "microphone" }).then(function(p){
        _dbg.mic.permission = p.state; log("Mic permission="+p.state);
        p.onchange = function(){ _dbg.mic.permission = p.state; log("Mic permission="+p.state); };
      }).catch(function(e){ _dbg.mic.permission = "unknown"; });
    }
  } catch {}
}

function refreshLaneUI(){
  if (!chatPanel) return;
  if (chatText) chatText.classList.remove("hidden");
  if (chatLane==="live"){ if (chatTTS) chatTTS.classList.remove("hidden"); }
  else { if (chatTTS) chatTTS.classList.add("hidden"); }
  badge();
}

// -------------------- Session UX --------------------
let sessionActive=false;
const setSessionActive=function(on){ sessionActive=!!on; if (BTN_START) BTN_START.disabled=on; if (BTN_END) BTN_END.disabled=!on; };
const setStatus=function(t){ const s=$("statusBanner"); if (s) s.textContent=t||""; };

function onChatToggle(){ if (!chatPanel) return; chatPanel.hidden=!chatPanel.hidden; if (!chatPanel.hidden) refreshLaneUI(); }

async function onStartClicked(){
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate || !okGate.ok) { log("Gate failed"); return; }
  setSessionActive(true);
  setStatus("Connecting…");
  try {
    await startDynamicSession();
    log("Greet complete");
    // Auto-arm mic only for Live lane; request permission properly
    if (getChatLane() === "live"){
      try {
        log("Auto-arming mic…");
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        _dbg.mic.haveStream = !!stream;
        setRecordStream(stream);
        _vm_stopPlayback();
        await _vm_armVAD();
        _dbg.mic.armed = vadIsArmed();
        log("VAD armed="+_dbg.mic.armed+", rec="+recIsRecording());
      } catch(e){
        log("VAD arm failed", e && (e.message || String(e)));
        _chipGuide("Please allow microphone access to use Live conversation.");
      }
    } else {
      log("Lane is Text; mic not auto-armed");
    }
    setStatus("Ready");
  } catch(e){
    log("Start failed", e && e.message);
    setStatus("Ready");
  }
}

async function onAudioClicked(){
  const armed = BTN_AUDIO && BTN_AUDIO.classList.contains("primary");
  if (armed){ log("Audio clicked: disarm"); _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate || !okGate.ok) { log("Gate failed (audio)"); return; }
  try {
    log("Audio clicked: getUserMedia");
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    _dbg.mic.haveStream = !!s;
    setRecordStream(s);
  } catch(err){
    log("getUserMedia failed", err && (err.message || String(err)));
    _chipGuide("I couldn't access your mic. Check browser permissions and try again.");
    return;
  }
  _vm_stopPlayback();
  await _vm_armVAD();
  _dbg.mic.armed = vadIsArmed();
  log("Manual arm done; armed="+_dbg.mic.armed);
}

function endSession(){
  try{
    _vm_disarmVAD(); _vm_stopPlayback(); _chipSetState("idle"); setSessionActive(false);
    setStatus("Disconnected. Press Start to begin a new session.");
    log("End session");
  } catch(e){ log("Disconnect error", e && e.message); }
}

// -------------------- Greeting sequence --------------------
export async function startDynamicSession(){
  try{
    _chipSetState("greeting");
    _chipStep("GET /api/greet →", {});
    const res = await j("/api/greet");
    const ok  = res && (res.ok !== false);
    const status = res && res.status;
    if (!ok){
      _chipStep("greet-failed",{status: status});
      _chipSetState("idle");
      alert("Could not start the greeting. Try again?");
      setSessionActive(false);
      log("Greet failed", status);
      return;
    }

    const reply = String(res.reply ?? res.reply_text ?? res.text ?? res.message ?? "").trim();
    if (reply) appendMessage("assistant", reply);

    // Always synthesize greet audio via TTS
    if (reply){
      const ttsRes = await fetch("/api/v1/voice/tts-with-visemes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: reply })
      });
      const tts = await ttsRes.json().catch(function(){ return null; });
      if (tts){
        if (tts.audio) {
          try { await tryPlayWithMouth(tts.audio); } catch(e){ log("Greet TTS (url) failed", e && e.message); }
        } else if (tts.audio_base64 || tts.audio_b64) {
          try {
            const url = "data:audio/mpeg;base64," + (tts.audio_base64 || tts.audio_b64);
            await tryPlayWithMouth(url);
          } catch(e){ log("Greet TTS (b64) failed", e && e.message); }
        }
      }
    }

    if (chatPanel) chatPanel.hidden = false; refreshLaneUI();
    const chatInput = $("chatInput");
    if (chatInput){
      chatInput.placeholder = "Ask me anything about Pure Storage…";
      try { chatInput.focus(); } catch{}
    }
    _chipSetState("idle");
    log("Greeting sequence done");
  } catch(e){
    log("startDynamicSession failed", e && e.message);
    _chipSetState("idle");
  }
}

// -------------------- Boot --------------------
wireLoginAndProfileHandlers();
onReady(initUI);
(async function(){
  const g = await gate({ applyLayout: true });
  if (g && g.ok){
    const s=$("statusBanner"); if (s) s.textContent="Disconnected. Press Start to begin a new session.";
    log("Boot ok");
  } else {
    if (chatPanel) chatPanel.hidden=true;
    log("Boot gate failed");
  }
})();
