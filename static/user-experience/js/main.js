// main.js — r3: fix ReferenceError(rehydrateChip), robust binding, lane picker, calibrated mouth
console.log("UI build ⏱ 2025-08-31-ui-r3");

import { $, show, hide, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import {
  _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep,
  setRenderSuggestions, setArmVADHook, _chipClearIdleNudge
} from "./core/state.js";
import {
  appendMessage, appendActions, _chipRenderSuggestions, updateChatButtonLabel, wireChatMenu
} from "./chat/ui.js";
import {
  sendChat, handleVoiceOnceResponse, wireChatLane, setArmVAD as setArmVADForSend, _chipEndConversation
} from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks } from "./voice/vad.js";
import { _vm_stopRecording, setStream as setRecordStream } from "./voice/record.js";
import { setProfileModalMode, loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

/* --------------------------- Small utilities ---------------------------- */
const el = (id) => document.getElementById(id);
function onReady(fn){ if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once:true }); else fn(); }
function bindClick(node, handler){
  if (!node) return false;
  node.addEventListener("click", handler);
  node.addEventListener("keydown", (e)=>{ if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(e); } });
  node.setAttribute("data-bound", "1");
  return true;
}

/* ------------------------------- Elements -------------------------------- */
let BTN_START, BTN_AUDIO, BTN_END, BTN_CHAT, CHAT_MENU, BADGE;
let chatPanel, chatText, chatTTS, chipImage, chipMouth;


// ---- Feature gating (canonical) ----
async function loadFeatures() {
  try {
    const r = await fetch("/api/features", { credentials: "include" });
    const j = await r.json();
    return (j && j.features) ? j.features : {};
  } catch { return {}; }
}

/* -------------------------------- Lanes ---------------------------------- */
let chatLane = "text";
const getChatLane = () => chatLane;
const setChatLane = (lane) => { chatLane = (lane === "live") ? "live" : "text"; try{ localStorage.setItem("chatLane", chatLane); }catch{}; refreshLaneUI(); };
const badge = () => { if (BADGE) BADGE.textContent = chatLane === "live" ? "Live" : "Text"; };

/* ------------------------- Mouth overlay / calibration ------------------- */
const CHIP_SRC   = "/static/chip/img/chip.png";
const MOUTH_BASE = "/static/chip/img/visemes";
const normalizeMouthFile = (n)=> !n ? "mouth_neutral.png" : (/^neutral(\.png)?$/i.test(n) ? "mouth_neutral.png" : (/\.(png|webp|svg)$/i.test(n) ? n : n+".png"));
function setMouth(name){ if (chipMouth) chipMouth.src = `${MOUTH_BASE}/${normalizeMouthFile(name)}`; }
function applyMouthFromStorage(){ if (!chipMouth) return; const t=localStorage.getItem("mouthTopPct"); const l=localStorage.getItem("mouthLeftPct"); const w=localStorage.getItem("mouthWidthPx"); if (t) chipMouth.style.top=t; if (l) chipMouth.style.left=l; if (w) chipMouth.style.width=w; }
function calibrateMouth(){ if (!chipImage || !chipMouth) return; applyMouthFromStorage(); const cs=getComputedStyle(document.documentElement); chipMouth.style.top=chipMouth.style.top||cs.getPropertyValue("--mouth-top-pct")||"62%"; chipMouth.style.left=chipMouth.style.left||cs.getPropertyValue("--mouth-left-pct")||"50%"; chipMouth.style.width=chipMouth.style.width||cs.getPropertyValue("--mouth-width")||"120px"; }

// ✅ Missing in r2 — restore rehydrateChip so initUI can call it safely.
function rehydrateChip(){
  const chip  = el("chipImage");
  const mouth = el("chipMouthImg");
  if (!chip) return;
  chip.classList.remove("hidden");
  chip.style.display    = "block";
  chip.style.visibility = "visible";
  chip.style.opacity    = "1";
  if (!chip.getAttribute("src") || chip.getAttribute("src").trim() === "") chip.src = `${CHIP_SRC}?v=${Date.now()}`;
  chip.onerror = () => { chip.src = `${CHIP_SRC}?v=${Date.now()}`; chip.classList.remove("hidden"); };
  if (mouth) mouth.onerror = () => setMouth("mouth_neutral.png");
  calibrateMouth();
}

function enableCalibration(){
  const stage = el("chipStage"); if (!stage || !chipMouth) return;
  stage.classList.add("calibrating");
  let dragging=false, startX=0, startY=0, startTop=0, startLeft=0;
  stage.addEventListener("mousedown", (ev)=>{
    if (!ev.shiftKey) return;
    dragging=true; startX=ev.clientX; startY=ev.clientY;
    startTop=parseFloat((chipMouth.style.top||"50%").replace('%',''));
    startLeft=parseFloat((chipMouth.style.left||"50%").replace('%','')); ev.preventDefault();
  });
  window.addEventListener("mousemove", (ev)=>{
    if (!dragging) return;
    const dy=((ev.clientY-startY)/window.innerHeight)*100;
    const dx=((ev.clientX-startX)/window.innerWidth)*100;
    const t=Math.max(0, Math.min(100, startTop+dy));
    const l=Math.max(0, Math.min(100, startLeft+dx));
    chipMouth.style.top=t.toFixed(2)+"%"; chipMouth.style.left=l.toFixed(2)+"%";
  });
  window.addEventListener("mouseup", ()=>{
    if (!dragging) return; dragging=false;
    localStorage.setItem("mouthTopPct", chipMouth.style.top);
    localStorage.setItem("mouthLeftPct", chipMouth.style.left);
  });
  chipMouth.addEventListener("dblclick", ()=>{
    const cur=parseInt(chipMouth.style.width||"120",10);
    const next=(cur>=140)?120:140; chipMouth.style.width=next+"px";
    localStorage.setItem("mouthWidthPx", chipMouth.style.width);
  });
}

/* ----------------------------- Wiring / boot ----------------------------- */
function initUI(){
  BTN_START=el("zStart"); BTN_AUDIO=el("zAudio"); BTN_END=el("zEnd"); BTN_CHAT=el("zChat"); CHAT_MENU=el("zChatMenu"); BADGE=el("laneBadge");
  chatPanel=el("chatPanel"); chatText=el("chatText"); chatTTS=el("chatTTS"); chipImage=el("chipImage"); chipMouth=el("chipMouthImg");

  try { setToolbarHeightVar(); } catch {}
  // Chip visuals
  rehydrateChip(); requestAnimationFrame(rehydrateChip); setTimeout(rehydrateChip, 200);

  // Bottom bar wiring
  bindClick(BTN_CHAT, onChatToggle);
  bindClick(BTN_START, onStartClicked);
  bindClick(BTN_AUDIO, onAudioClicked);
  bindClick(BTN_END, endSession);

  // Lane picker (open on right-click or middle-click on Chat)
  const openMenu=(ev)=>{
    if (!CHAT_MENU||!BTN_CHAT) return;
    const r=BTN_CHAT.getBoundingClientRect();
    CHAT_MENU.style.left=Math.max(12, r.left+r.width/2-80)+"px";
    CHAT_MENU.style.bottom=(window.innerHeight-r.top+10)+"px";
    CHAT_MENU.classList.remove("hidden");
    ev?.stopPropagation();
  };
  const closeMenu=()=>CHAT_MENU&&CHAT_MENU.classList.add("hidden");
  BTN_CHAT && BTN_CHAT.addEventListener("contextmenu",(e)=>{ e.preventDefault(); openMenu(e);});
  BTN_CHAT && BTN_CHAT.addEventListener("auxclick",(e)=>{ if (e.button===1){ e.preventDefault(); openMenu(e);} });
  bindClick(el("laneText"), ()=>{ setChatLane("text"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  bindClick(el("laneLive"), ()=>{ setChatLane("live"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  document.addEventListener("click",(e)=>{ if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  // App menu (top-right)
  const navBtn=el("navMenuBtn"); const navMenu=el("navMenu");
  bindClick(navBtn, (e)=>{ e.preventDefault(); e.stopPropagation(); navMenu.classList.toggle("hidden"); });
  document.addEventListener("click",(e)=>{ if (navMenu && !navMenu.classList.contains("hidden") && !navMenu.contains(e.target) && e.target!==navBtn) navMenu.classList.add("hidden"); });

  bindClick(el("navProfile"), async ()=>{ navMenu?.classList.add("hidden"); setProfileModalMode("edit"); await loadProfileIntoForm(); show(el("profileModal"), "flex"); });
  bindClick(el("navHistory"), async ()=>{ navMenu?.classList.add("hidden"); const { ok, data } = await j("/history", { method:"POST", body:"{}" }); if (ok && data?.response) appendMessage("chip", data.response); });
  bindClick(el("navLogout"), async ()=>{ navMenu?.classList.add("hidden"); try { await fetch("/logout", { method:"POST", credentials:"include" }); } catch{} location.reload(); });

  // Chat compose
  bindClick(el("chatSendBtn"), ()=>{ const input=el("chatInput"); if (!input) return; const v=input.value; if (v && v.trim()) { sendChat(v); input.value=""; } });
  el("chatInput")?.addEventListener("keydown",(e)=>{ const input=el("chatInput"); if (!input) return; if (e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); const v=input.value; if (v&&v.trim()){ sendChat(v); input.value=""; } } });

  // Mic UI
  setMicUIUpdater((on, recording=false)=>{
    if (!BTN_AUDIO) return;
    BTN_AUDIO.classList.toggle("primary", !!on);
    BTN_AUDIO.classList.toggle("recording", !!recording);
    const lbl = BTN_AUDIO.querySelector("span:last-child"); if (lbl) lbl.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
  });
  setVoiceGuide((text)=>_chipGuide(text));
  setRecordCallbacks(async ()=>{}, async ()=>{ await _vm_stopRecording(async (blob, durMs)=>{ await handleVoiceOnceResponse({ blob, durMs }); }); });
  setArmVADForSend(()=>_vm_armVAD());
  wireChatLane(getChatLane, setChatLane);
  refreshLaneUI();

  // Calibration UX
  calibrateMouth();
  el("chipStage")?.addEventListener("dblclick",(e)=>{ if (e.target===chipMouth) return; const st=el("chipStage"); if (!st) return; if (st.classList.contains("calibrating")) st.classList.remove("calibrating"); else enableCalibration(); });

  // Debug helper
  window.__chipDebug = () => ({
    build: "2025-08-31-ui-r3",
    bound: {
      start: !!(BTN_START && BTN_START.getAttribute("data-bound")),
      audio: !!(BTN_AUDIO && BTN_AUDIO.getAttribute("data-bound")),
      end:   !!(BTN_END   && BTN_END.getAttribute("data-bound")),
      chat:  !!(BTN_CHAT  && BTN_CHAT.getAttribute("data-bound"))
    }
  });
}

function refreshLaneUI(){
  if (!chatPanel) return;
  chatText && chatText.classList.remove("hidden"); // always visible
  if (chatLane==="live") chatTTS && chatTTS.classList.remove("hidden"); else chatTTS && chatTTS.classList.add("hidden");
  badge();
}

/* ------------------------------ Session UX ------------------------------ */
let sessionActive=false;
const setSessionActive=(on)=>{ sessionActive=!!on; if (BTN_START) BTN_START.disabled=sessionActive; if (BTN_END) BTN_END.disabled=!sessionActive; };
const setStatus=(t)=>{ const s=$("statusBanner"); if (s) s.textContent=t||""; };

function onChatToggle(){ if (!chatPanel) return; chatPanel.hidden=!chatPanel.hidden; if (!chatPanel.hidden) refreshLaneUI(); }

async function onStartClicked(){
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;  // prompts login/profile if needed fileciteturn1file0
  setSessionActive(true);
  setStatus("Connecting"); _chipGuide("Starting");
  await startDynamicSession();
  if (chatLane==="live"){ _chipGuide("Now listening — start talking after the tone."); try{ await _vm_armVAD(); }catch(e){ console.warn("VAD arm failed", e);} }
  setTimeout(()=>{ const armed=BTN_AUDIO?.classList.contains("primary"); if (armed) _chipGuide("I didn’t catch anything—check your mic or tap Audio to try again."); }, 8000);
}

async function onAudioClicked(){
  const armed = BTN_AUDIO?.classList.contains("primary");
  if (armed){ _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate.ok) return; // prompts if not ready fileciteturn1file0
  try { const s = await navigator.mediaDevices.getUserMedia({ audio: true }); setRecordStream(s); }
  catch(err){ console.warn("getUserMedia failed:", err); _chipGuide("I can’t access your mic. Check browser permissions and try again."); return; }
  _vm_stopPlayback(); await _vm_armVAD(); // barge-in then listen
}

function endSession(){
  try{ _chipStep("disconnect","teardown"); _vm_disarmVAD(); _vm_stopPlayback(); _chipClearIdleNudge(); _chipSetState("idle"); setSessionActive(false); setStatus("Disconnected. Press Start to begin a new session."); _chipGuide("Disconnected. Press Start to begin a new session."); }
  catch(e){ console.warn("disconnect error", e); }
}

/* ---------------------------- Boot experience --------------------------- */
wireLoginAndProfileHandlers();
onReady(initUI);

(async ()=>{
  const g = await gate({ applyLayout: true });
  if (g && g.ok){ setStatus("Disconnected. Press Start to begin a new session."); _chipGuide("Press Start to speak with Chip."); _chipStep("boot","ready"); }
  else { if (chatPanel) chatPanel.hidden=true; }
})();

window.addEventListener("resize", calibrateMouth);

/* -------------------------- Dynamic greet / start ----------------------- */
async function startDynamicSession(){
  try{
    _chipSetState("greeting"); _chipStep("GET /api/greet →", {});
    const res = await j("/api/greet");
    const ok = res && (res.ok !== false);
    if (!ok){
      _chipStep("greet-failed",{status: res && res.status});
      _chipSetState("idle");
      alert("Could not start the greeting. Try again?");
      setSessionActive(false);
      return;
    }
    const reply = String(res.reply ?? res.reply_text ?? res.text ?? res.message ?? "").trim();
    if (reply) appendMessage("assistant", reply);
    const audioUrl = res.audio || res.audio_url || null;
    if (audioUrl){
      try { await tryPlayWithMouth(audioUrl); } catch(e){ console.warn("Greet audio failed", e); }
    } else if (reply){
      try {
        const ttsRes = await fetch("/api/v1/voice/tts-with-visemes", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: reply })
        }).then(r=>r.json()).catch(()=>null);
        if (ttsRes){
          if (ttsRes.audio) {
            try { await tryPlayWithMouth(ttsRes.audio); } catch(e){ console.warn("Greet TTS (url) failed", e); }
          } else if (ttsRes.audio_base64 || ttsRes.audio_b64) {
            try { 
              const a = new Audio("data:audio/mpeg;base64," + (ttsRes.audio_base64 || ttsRes.audio_b64));
              await a.play();
            } catch(e){ console.warn("Greet TTS (b64) failed", e); }
          }
        }
      } catch(e){ console.warn("Greet TTS failed", e); }
    }
    if (chatPanel) chatPanel.hidden=false; refreshLaneUI();
    const chatInput=$("chatInput"); if (chatInput){ chatInput.placeholder="Ask me anything about Pure Storage…"; try{ chatInput.focus(); }catch{} }
    setStatus("Ready");
    _chipSetState("idle");
  } catch(e){
    console.warn("startDynamicSession failed", e);
    setStatus("Ready"); _chipSetState("idle");
  }
}
