import { API, TIMING } from "./config.js";
import { getSID } from './util/sid.js';
import { setState, STATES } from "./state.js";
import { showError } from "./errors.js";
import { playStream, stopPlayback, isPlaying } from "./audio.js";
import { renderSuggestions } from "./suggestions.js";

let ws = null;
let _tabId = null;
function getTabId(){
  if(!_tabId){ try{ _tabId = sessionStorage.getItem('chip.tab') || (crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now())); sessionStorage.setItem('chip.tab', _tabId); }catch(e){ _tabId = 'tab'; } }
  return _tabId;
}

let reconnects = 0;
let startBtn, endBtn;
let nudgeTimer = null;
let lastAssistantTurn = null;
let _openPromise = null;

// Accumulators for current turn
let _audioBufs = [];
let _visemes = [];

function addChatMessage(role, text){
  try{
    const box = document.getElementById('chatMessages');
    if(!box || !text) return;
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }catch(e){}
}

export function bindControls(startEl, endEl){
  startBtn = startEl; endBtn = endEl;
  updateButtons();
}

export function waitWSOpen(timeout=4000){
  if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
  return _openPromise || new Promise((res) => setTimeout(res, 10));
}

export function openWS(){
  if (ws && ws.readyState === WebSocket.OPEN) return ws;
  ws = new WebSocket(`${API.WS}?session_id=${encodeURIComponent(getSID())}&tab=${encodeURIComponent(getTabId())}`);
  reconnects = 0;
  updateButtons();
  _openPromise = new Promise((resolve)=>{ ws.onopen = () => { updateButtons(); resolve(); }; });
  ws.onmessage = onWSMessage;
  ws.onerror = () => { showError(API.WS, "WS", "socket error"); };
  ws.onclose = () => {
    updateButtons();
    if (reconnects < 1){
      reconnects++;
      setTimeout(() => openWS(), 1000);
    }
  };
  return ws;
}

export function closeWS(){
  if (ws) try { ws.close(1000, "End"); } catch{};
  ws = null;
  updateButtons();
}

function updateButtons(){
  const active = ws && ws.readyState === WebSocket.OPEN;
  if (startBtn) startBtn.disabled = active;
  if (endBtn)   endBtn.disabled   = !active;
}

function b64ToArrayBuffer(base64) {
  const bin = atob(base64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function normalizeVisemes(items){
  const xs = Array.isArray(items) ? items : [];
  return xs.map(x => ({
    t: typeof x.t !== "undefined" ? x.t : (x.t_ms ?? 0),
    v: x.v
  }));
}

function onWSMessage(ev){
  try{
    const msg = JSON.parse(ev.data);

    // Normalize both server frame dialects
    if (msg.type === "assistant_chunk" || msg.type === "text"){
      setState(STATES.RESPONDING);
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;
      // append assistant text to chat
      if (msg.text || msg.content) addChatMessage('assistant', (msg.text || msg.content));
    }

    if (msg.type === "audio_chunk"){
      if (msg.base64) _audioBufs.push(b64ToArrayBuffer(msg.base64));
    }

    if (msg.type === "visemes"){
      _visemes = normalizeVisemes(msg.items || msg.visemes || []);
    }

    if (msg.type === "suggestions"){
      if (Array.isArray(msg.items)) renderSuggestions(msg.items);
    }

    if (msg.type === "assistant_end" || msg.type === "end"){
      const bufs = _audioBufs.slice();
      const ves  = _visemes.slice();
      _audioBufs.length = 0;
      _visemes = [];
      setState(STATES.RESPONDING);
      playStream(bufs, ves).finally(() => setState(STATES.READY));
      // If the server sends suggestions after end, schedule a gentle nudge
      scheduleNudge();
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;
    }

    if (msg.type === "error"){
      showError(API.WS, msg.code || "ERR", msg.message || "");
    }

    if (msg.type === "state"){
      // could map phases to UI if useful
    }

  }catch(e){}
}

export function sendInterrupt(){
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const turn = lastAssistantTurn;
  const frame = { type: "control", cmd: "interrupt", turn_id: turn };
  try { ws.send(JSON.stringify(frame)); } catch {}
  stopPlayback();
  setState(STATES.LISTENING);
}

export function scheduleNudge(){
  if (nudgeTimer) clearTimeout(nudgeTimer);
  nudgeTimer = setTimeout(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type:"control", cmd:"nudge" }));
  }, TIMING.NUDGE_DELAY_MS);
}

export function cancelNudge(){
  if (nudgeTimer) { clearTimeout(nudgeTimer); nudgeTimer = null; }
}
