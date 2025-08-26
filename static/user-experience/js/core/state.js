// core/state.js — UX state, guide/debug, timers
import { show } from "./dom.js";

const guidePanelEl = () => document.getElementById("guidePanel");
const debugPanelEl = () => document.getElementById("debugPanel");

export const _chipUX = {
  isAdmin: false,
  debugVisible: false,
  guideVisible: false,
  state: "idle", // idle|greeting|waiting|listening|thinking|responding|followup
  waitingTimer: null,
  waitingSeconds: 7
};

let _renderSuggestions = null;
export function setRenderSuggestions(fn) { _renderSuggestions = typeof fn === "function" ? fn : null; }

export function _chipStep(phase, details) {
  if (!_chipUX.debugVisible) return;
  const el = debugPanelEl();
  if (!el) return;
  const time = new Date().toLocaleTimeString();
  let line = [`[${time}] ${phase}`];
  if (details !== undefined) {
    try { line.push(typeof details === "string" ? details : JSON.stringify(details, null, 2)); } catch {}
  }
  el.textContent += (el.textContent ? "\n" : "") + line.join("\n");
  el.scrollTop = el.scrollHeight;
}

export function _chipGuide(text) {
  const el = guidePanelEl(); if (!el) return;
  el.textContent = text || "";
  const on = !!text;
  el.style.display = on ? "block" : "none";
  _chipUX.guideVisible = on;
}

export function _chipSetAdmin(val, queryParamDebugVal) {
  _chipUX.isAdmin = !!val;
  _chipUX.debugVisible = _chipUX.isAdmin || queryParamDebugVal === "1";
  const dbg = debugPanelEl();
  if (dbg) dbg.style.display = _chipUX.debugVisible ? "block" : "none";
}

let _idleTimer = null;
export function _chipClearIdleNudge() { if (_idleTimer) { clearTimeout(_idleTimer); _idleTimer = null; } }
export function _chipScheduleIdleNudge(ms = 18000 + Math.floor(Math.random()*8000)) {
  _chipClearIdleNudge();
  _idleTimer = setTimeout(() => {
    if (_chipUX.state !== "followup" && _chipUX.state !== "listening") return;
    if (typeof _renderSuggestions === "function") {
      _chipGuide("Still there? Keep chatting or end?");
      _renderSuggestions(["Explain a bit more", "Give me a quick example", "End chat"]);
    }
  }, ms);
}

let _armVADHook = null;
export function setArmVADHook(fn) { _armVADHook = fn; }

function _openChatComposer(hintText) {
  const chatPanel = document.getElementById("chatPanel");
  const chatInput = document.getElementById("chatInput");
  if (chatPanel) chatPanel.hidden = false;
  if (chatInput) {
    if (hintText) chatInput.placeholder = hintText;
    chatInput.focus();
  }
  _chipStep("composer", "opened");
}

export function _chipSetState(next) {
  _chipUX.state = next;
  _chipStep("state", next);
  switch (next) {
    case "greeting":
      _chipGuide("Chip is saying hello — how can he help?");
      break;
    case "waiting":
      _chipGuide(`Get ready… Chip will start listening in ${_chipUX.waitingSeconds}s. Speak after the tone.`);
      break;
    case "listening":
      _chipGuide("Now listening — start talking after the tone. (Pause to send)");
      _openChatComposer("You can also type your question…");
      if (typeof _armVADHook === "function") _armVADHook();
      break;
    case "thinking":
      _chipGuide("Chip is thinking…");
      break;
    case "responding":
      _chipGuide("Chip is responding…");
      break;
    case "followup":
      _chipGuide("Do you have a follow-up?");
      _chipScheduleIdleNudge();
      if (typeof _armVADHook === "function") _armVADHook();
      break;
    default:
      _chipGuide("Press Start or Chat to speak with Chip.");
  }
}

export function _chipStartWaitingCountdown() {
  if (_chipUX.waitingTimer) { clearInterval(_chipUX.waitingTimer); _chipUX.waitingTimer = null; }
  let t = _chipUX.waitingSeconds;
  _chipSetState("waiting");
  _chipUX.waitingTimer = setInterval(() => {
    t -= 1;
    if (t <= 0) {
      clearInterval(_chipUX.waitingTimer);
      _chipUX.waitingTimer = null;
      _chipSetState("listening");
    } else {
      _chipGuide(`Get ready… Chip will start listening in ${t}s. Speak after the tone.`);
    }
  }, 1000);
}
