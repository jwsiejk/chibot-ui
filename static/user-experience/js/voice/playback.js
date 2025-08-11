// voice/playback.js — playback, visemes, mutex, tone
import { _chipStep } from "../core/state.js";

let _resp_in_flight = false;
export function respAcquire() { if (_resp_in_flight) return false; _resp_in_flight = true; return true; }
export function respRelease() { _resp_in_flight = false; }

let _vm_playback = null;

export function _vm_stopPlayback() {
  if (_vm_playback) {
    try { _vm_playback.pause(); } catch {}
    _vm_playback = null;
  }
  if (window.ChipViseme && typeof window.ChipViseme.stop === "function") {
    try { window.ChipViseme.stop(); } catch {}
  }
}

export async function tryPlayWithMouth(url, opts) {
  if (window.ChipViseme && typeof window.ChipViseme.play === "function") {
    await window.ChipViseme.play(url, opts || {});
    return url;
  }
  return await new Promise((resolve, reject) => {
    const a = new Audio(url);
    a.addEventListener("ended", () => resolve(url), { once: true });
    a.addEventListener("error", (e) => reject(e));
    a.play().catch(reject);
  });
}

export function driveVisemes(visemes) {
  if (!visemes || !visemes.length) return;
  if (window.ChipViseme && typeof window.ChipViseme.drive === "function") {
    try { window.ChipViseme.drive(visemes); } catch (e) { console.warn("Viseme drive failed:", e); }
  }
}

let _vm_ctx = null;
export async function _uiBeep(freq = 880, ms = 90, gain = 0.05) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    _vm_ctx = _vm_ctx || new Ctx();
    if (_vm_ctx.state === "suspended") await _vm_ctx.resume();
    const osc = _vm_ctx.createOscillator();
    const g = _vm_ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    g.gain.value = gain;
    osc.connect(g); g.connect(_vm_ctx.destination);
    osc.start();
    setTimeout(() => { try { osc.stop(); osc.disconnect(); g.disconnect(); } catch {} }, ms);
  } catch {}
}
