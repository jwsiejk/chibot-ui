// main.js — final mic record patch: start/stop recording around VAD
console.log("UI build ⏱ patched-record-2025-09-03");

import { $, show, hide, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import { _chipGuide, _chipSetState, _chipStep } from "./core/state.js";
import { appendMessage } from "./chat/ui.js";
import { sendChat, handleVoiceOnceResponse, wireChatLane } from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks } from "./voice/vad.js";
import { setStream as setRecordStream, _vm_startRecording, _vm_stopRecording } from "./voice/record.js";
import { loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

const el = (id) => document.getElementById(id);
function onReady(fn){ if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once:true }); else fn(); }
function bindClick(node, handler){ if (!node) return false; node.addEventListener("click", handler); node.addEventListener("keydown", function(e){ if (e.key==="Enter"||e.key===" "){ e.preventDefault(); handler(e);} }); node.setAttribute("data-bound","1"); return true; }

let BTN_START, BTN_AUDIO, BTN_END, BTN_CHAT, CHAT_MENU, BADGE;
let chatPanel, chatText, chatTTS;

let chatLane = (function(){ try { return (localStorage.getItem("chatLane") || "text") === "live" ? "live" : "text"; } catch(e) { return "text"; } })();
const getChatLane = function(){ return chatLane; };
const setChatLane = function(lane){ chatLane = lane === "live" ? "live" : "text"; try { localStorage.setItem("chatLane", chatLane); } catch(e){} refreshLaneUI(); };
function badge(){ if (BADGE) BADGE.textContent = chatLane === "live" ? "Live" : "Text"; }

function initUI(){
  BTN_START=el("zStart"); BTN_AUDIO=el("zAudio"); BTN_END=el("zEnd"); BTN_CHAT=el("zChat"); CHAT_MENU=el("zChatMenu"); BADGE=el("laneBadge");
  chatPanel=el("chatPanel"); chatText=el("chatText"); chatTTS=el("chatTTS");
  try { setToolbarHeightVar(); } catch(e){}

  bindClick(BTN_CHAT, onChatToggle);
  bindClick(BTN_START, onStartClicked);
  bindClick(BTN_AUDIO, onAudioClicked);
  bindClick(BTN_END, endSession);

  function openMenu(ev){ if (!CHAT_MENU||!BTN_CHAT) return; const r=BTN_CHAT.getBoundingClientRect(); CHAT_MENU.style.left=Math.max(12, r.left+r.width/2-80)+"px"; CHAT_MENU.style.bottom=(window.innerHeight-r.top+10)+"px"; CHAT_MENU.classList.remove("hidden"); ev && ev.preventDefault(); }
  function closeMenu(){ if (CHAT_MENU) CHAT_MENU.classList.add("hidden"); }
  if (BTN_CHAT){ BTN_CHAT.addEventListener("contextmenu", function(e){ e.preventDefault(); openMenu(e); }); BTN_CHAT.addEventListener("auxclick", function(e){ if (e.button===1){ e.preventDefault(); openMenu(e);} }); }
  bindClick(el("laneText"), function(){ setChatLane("text"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); });
  bindClick(el("laneLive"), function(){ setChatLane("live"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); });
  document.addEventListener("click", function(e){ if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  bindClick(el("chatSendBtn"), function(){ const input=el("chatInput"); if (!input) return; const v=input.value; if (v && v.trim()) { sendChat(v); input.value=""; } });
  const chatInputEl = el("chatInput");
  if (chatInputEl) chatInputEl.addEventListener("keydown", function(e){ const input=el("chatInput"); if (!input) return; if (e.key==="Enter" && !e.shiftKey){ e.preventDefault(); const v=input.value; if (v&&v.trim()){ sendChat(v); input.value=""; } } });

  setMicUIUpdater(function(on, recording){
    if (!BTN_AUDIO) return;
    BTN_AUDIO.classList.toggle("primary", !!on);
    BTN_AUDIO.classList.toggle("recording", !!recording);
    const lbl = BTN_AUDIO.querySelector("span:last-child"); if (lbl) lbl.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
  });
  setVoiceGuide(function(text){ _chipGuide(text); });

  // IMPORTANT: start and stop actual recording around VAD
  setRecordCallbacks(
    async function onStart(){
      try { window.dispatchEvent(new Event("chip:bargein")); } catch(e){}
      try { await _vm_startRecording(); } catch(e){ console.warn("startRecording failed", e); }
    },
    async function onStop(){
      try {
        await _vm_stopRecording(async function(blob, durMs){
          try { await handleVoiceOnceResponse({ blob: blob, durMs: durMs }); } catch(e){ console.warn("voice handler failed", e); }
        });
      } catch(e){ console.warn("stopRecording failed", e); }
    }
  );

  wireChatLane(getChatLane, setChatLane);
  refreshLaneUI();
}

function refreshLaneUI(){
  if (!chatPanel) return;
  if (chatText) chatText.classList.remove("hidden");
  if (chatLane==="live"){ if (chatTTS) chatTTS.classList.remove("hidden"); }
  else { if (chatTTS) chatTTS.classList.add("hidden"); }
  badge();
}

let sessionActive=false;
const setSessionActive=function(on){ sessionActive=!!on; if (BTN_START) BTN_START.disabled=on; if (BTN_END) BTN_END.disabled=!on; };
const setStatus=function(t){ const s=$("statusBanner"); if (s) s.textContent=t||""; };

function onChatToggle(){ if (!chatPanel) return; chatPanel.hidden=!chatPanel.hidden; if (!chatPanel.hidden) refreshLaneUI(); }

async function onStartClicked(){
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate || !okGate.ok) return;
  setSessionActive(true);
  setStatus("Connecting…");
  try {
    await startDynamicSession();
    // Auto-arm mic for Live lane only
    if (getChatLane() === "live"){
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        setRecordStream(stream);
        _vm_stopPlayback();
        await _vm_armVAD();
      } catch(e){
        console.warn("VAD arm failed", e);
        _chipGuide("Please allow microphone access to use Live conversation.");
      }
    }
    setStatus("Ready");
  } catch(e){
    console.warn("start failed", e);
    setStatus("Ready");
  }
}

async function onAudioClicked(){
  const armed = BTN_AUDIO && BTN_AUDIO.classList.contains("primary");
  if (armed){ _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate || !okGate.ok) return;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    setRecordStream(s);
  } catch(err){
    console.warn("getUserMedia failed:", err);
    _chipGuide("I couldn't access your mic. Check browser permissions and try again.");
    return;
  }
  _vm_stopPlayback();
  await _vm_armVAD();
}

function endSession(){
  try{
    _vm_disarmVAD(); _vm_stopPlayback(); _chipSetState("idle"); setSessionActive(false);
    setStatus("Disconnected. Press Start to begin a new session.");
  } catch(e){ console.warn("disconnect error", e); }
}

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
      return;
    }

    const reply = String(res.reply ?? res.reply_text ?? res.text ?? res.message ?? "").trim();
    if (reply) appendMessage("assistant", reply);

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
          try { await tryPlayWithMouth(tts.audio); } catch(e){ console.warn("Greet TTS (url) failed", e); }
        } else if (tts.audio_base64 || tts.audio_b64) {
          try {
            const url = "data:audio/mpeg;base64," + (tts.audio_base64 || tts.audio_b64);
            await tryPlayWithMouth(url);
          } catch(e){ console.warn("Greet TTS (b64) failed", e); }
        }
      }
    }

    if (chatPanel) chatPanel.hidden = false; refreshLaneUI();
    const chatInput = $("chatInput");
    if (chatInput){
      chatInput.placeholder = "Ask me anything about Pure Storage…";
      try { chatInput.focus(); } catch(e){}
    }
    _chipSetState("idle");
  } catch(e){
    console.warn("startDynamicSession failed", e);
    _chipSetState("idle");
  }
}

wireLoginAndProfileHandlers();
onReady(initUI);
(async function(){
  const g = await gate({ applyLayout: true });
  if (g && g.ok){
    const s=$("statusBanner"); if (s) s.textContent="Disconnected. Press Start to begin a new session.";
  } else {
    if (chatPanel) chatPanel.hidden=true;
  }
})();
