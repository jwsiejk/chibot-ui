// voice/playback.js — streaming player (AudioWorklet) + visemes + fallbacks (patched)
import { _chipStep } from "../core/state.js";

let _resp_in_flight = false;
export function respAcquire() { if (_resp_in_flight) return false; _resp_in_flight = true; return true; }
export function respRelease() { _resp_in_flight = false; }

let _vm_playback = null;      // <audio> fallback element
let _ctx = null;              // AudioContext
let _workletNode = null;      // AudioWorkletNode
let _workletReady = false;    // whether worklet module is loaded
let _streamingActive = false; // are we currently streaming?
let _streamSampleRate = 24000;
let _expectedChannels = 1;

/** Stop any playback (worklet stream, <audio> element, visemes). */
export function _vm_stopPlayback() {
  // Stop streaming worklet
  if (_workletNode) {
    try { _workletNode.port.postMessage({ type: "flush" }); } catch {}
    try { _workletNode.disconnect(); } catch {}
    _workletNode = null;
  }
  _streamingActive = false;

  // Stop element playback (fallback)
  if (_vm_playback) {
    try { _vm_playback.pause(); } catch {}
    _vm_playback = null;
  }

  // Stop visemes
  if (window.ChipViseme && typeof window.ChipViseme.stop === "function") {
    try { window.ChipViseme.stop(); } catch {}
  }
}

/** Lazy-create AudioContext and load our worklet module once. */
async function _ensureWorklet() {
  if (!_ctx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    _ctx = new Ctx({ latencyHint: "interactive" });
  }
  if (_ctx.state === "suspended") { try { await _ctx.resume(); } catch {} }

  if (!_workletReady) {
    try {
      await _ctx.audioWorklet.addModule("/static/user-experience/js/voice/worklet-player.js");
      _workletReady = true;
    } catch (e) {
      console.warn("AudioWorklet unavailable; will fallback to <audio> tag.", e);
      _workletReady = false;
    }
  }
  return _workletReady;
}

/** Begin a streaming session to the worklet (call once per response). */
export async function startStream({ sampleRate = 24000, channels = 1 } = {}) {
  _streamSampleRate = sampleRate;
  _expectedChannels = channels;

  const ok = await _ensureWorklet();
  if (!ok) {
    // Worklet not available; caller should fallback to URL/element playback
    _streamingActive = false;
    return false;
  }

  // New node each stream; cheap and keeps state clean
  _workletNode = new AudioWorkletNode(_ctx, "chip-stream-player", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [channels]
  });
  _workletNode.connect(_ctx.destination);
  _workletNode.port.postMessage({ type: "start", sampleRate, channels });
  _streamingActive = true;
  return true;
}

/** Send Float32 PCM samples to the worklet. */
export function pushPCMFloat32(float32, { sampleRate } = {}) {
  if (!_streamingActive || !_workletNode) return false;
  if (sampleRate && sampleRate !== _streamSampleRate) {
    // Let the worklet resample cheaply; we only tag the rate here.
  }
  // Transferable: underlying buffer is moved for zero-copy when possible.
  try {
    _workletNode.port.postMessage(
      { type: "pcm_f32", sampleRate: sampleRate || _streamSampleRate, channels: _expectedChannels, payload: float32.buffer },
      [float32.buffer]
    );
    return true;
  } catch (e) {
    // If transfer fails (older browsers), fall back to copy
    try {
      _workletNode.port.postMessage({ type: "pcm_f32_copy", sampleRate: sampleRate || _streamSampleRate, channels: _expectedChannels, payload: Array.from(float32) });
      return true;
    } catch {}
  }
  return false;
}

/** Send Int16 PCM (mono) to the worklet; converts to Float32 [-1,1]. */
export function pushPCMInt16(int16, { sampleRate } = {}) {
  if (!int16) return false;
  const len = int16.length;
  const f32 = new Float32Array(len);
  for (let i = 0; i < len; i++) { f32[i] = int16[i] / 32768; }
  return pushPCMFloat32(f32, { sampleRate });
}

/** Convenience: receive base64-encoded Int16LE PCM and forward it. */
export function pushPCM16Base64(b64, { sampleRate } = {}) {
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const int16 = new Int16Array(bytes.buffer);
    return pushPCMInt16(int16, { sampleRate });
  } catch (e) {
    console.warn("pushPCM16Base64 decode failed", e);
    return false;
  }
}

/** End a streaming session (flush and disconnect). */
export function stopStream() {
  if (_workletNode) {
    try { _workletNode.port.postMessage({ type: "end" }); } catch {}
    try { _workletNode.disconnect(); } catch {}
  }
  _workletNode = null;
  _streamingActive = false;
}

/** Backward-compatible URL playback (with visemes if available). */
export async function tryPlayWithMouth(url, opts) {
  if (window.ChipViseme && typeof window.ChipViseme.play === "function") {
    await window.ChipViseme.play(url, opts || {});
    return url;
  }
  return await new Promise((resolve, reject) => {
    const a = new Audio(url);
    _vm_playback = a;
    a.addEventListener("ended", () => { resolve(url); }, { once: true });
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


export function cancelActiveSpeech(){ try{ _vm_stopPlayback(); }catch{} try{ window.dispatchEvent(new CustomEvent('chip:tts-cancel')); }catch{} }
