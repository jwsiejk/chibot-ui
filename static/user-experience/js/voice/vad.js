// voice/vad.js — mic ensure, VAD arm/disarm, calibration, UI hooks
import { _chipStep } from "../core/state.js";
import { _uiBeep } from "./playback.js";

let _vm_stream = null;
let _vm_an = null;
let _vm_ctx = null;
let _vm_src = null;
let _vm_raf = 0;

let _vm_recording = false;
let _onStartRecording = null;
let _onStopRecording = null;

let _vm_vad_on = false;

const _vm_cfg = {
  vadThreshold: 0.015,
  defaultVadThreshold: 0.015,
  vadAttackMs: 120,
  vadReleaseMs: 700,
  maxRecordMs: 15000,
  preRollMs: 300,
  analyserSize: 1024
};
export function getVadConfig() { return _vm_cfg; }

let _updateMicUI = null;
let _guideFn = null;

export function setMicUIUpdater(fn) { _updateMicUI = fn; }
export function setGuide(fn) { _guideFn = fn; }

export function isRecording() { return _vm_recording; }
export function isArmed() { return _vm_vad_on; }

export function setRecordCallbacks(onStart, onStop) {
  _onStartRecording = onStart;
  _onStopRecording  = onStop;
}

export async function _vm_ensureMic() {
  if (_vm_stream && _vm_stream.getTracks().some(t => t.readyState === "live")) return _vm_stream;
  _vm_stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  return _vm_stream;
}

export function _vm_updateMicUI(on, recording=false) {
  if (typeof _updateMicUI === "function") _updateMicUI(on, recording);
}

export function _vm_disarmVAD() {
  _vm_vad_on = false;
  cancelAnimationFrame(_vm_raf); _vm_raf = 0;
  _vm_updateMicUI(false, false);
}

async function _vm_calibrateNoise(ms = 400) {
  try {
    if (!_vm_an) return;
    const buf = new Float32Array(_vm_an.fftSize);
    const start = performance.now();
    let n = 0, accum = 0;
    return await new Promise((resolve) => {
      const tick = () => {
        _vm_an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const s = buf[i]; sum += s*s; }
        const rms = Math.sqrt(sum / buf.length);
        accum += rms; n++;
        if (performance.now() - start >= ms) {
          const avg = accum / Math.max(1,n);
          const newThresh = Math.min(Math.max(avg * 2.5, _vm_cfg.defaultVadThreshold), 0.05);
          _vm_cfg.vadThreshold = newThresh;
          _chipStep("vad-calibrated", { avgRMS: avg.toFixed(5), threshold: newThresh.toFixed(5) });
          resolve();
          return;
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  } catch {}
}

export async function _vm_armVAD() {
  try {
    await _vm_ensureMic();
    _vm_updateMicUI(true, false);

    if (!_vm_ctx) _vm_ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (_vm_ctx.state === "suspended") await _vm_ctx.resume();

    _vm_src && _vm_src.disconnect();
    _vm_an && _vm_an.disconnect();
    _vm_src = _vm_ctx.createMediaStreamSource(_vm_stream);
    _vm_an = _vm_ctx.createAnalyser();
    _vm_an.fftSize = _vm_cfg.analyserSize;
    _vm_src.connect(_vm_an);

    await _vm_calibrateNoise(400);

    _vm_vad_on = true;
    let speakOn = 0, speakOff = 0, speaking = false;
    const buf = new Float32Array(_vm_an.fftSize);

    const tick = () => {
      if (!_vm_vad_on) return;
      _vm_an.getFloatTimeDomainData(buf);

      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const s = buf[i]; sum += s * s; }
      const rms = Math.sqrt(sum / buf.length);

      const now = performance.now();
      if (rms >= _vm_cfg.vadThreshold) {
        speakOn = speakOn || now;
        speakOff = 0;
        if (!speaking && (now - speakOn) >= _vm_cfg.vadAttackMs) {
          speaking = true;
          if (typeof _onStartRecording === "function") _onStartRecording();
          _vm_recording = true;
          _vm_updateMicUI(true, true);
          _uiBeep(1020, 80);
          setTimeout(() => { if (_vm_recording && typeof _onStopRecording === "function") _onStopRecording(); }, _vm_cfg.maxRecordMs);
        }
      } else {
        speakOff = speakOff || now;
        speakOn = 0;
        if (speaking && (now - speakOff) >= _vm_cfg.vadReleaseMs) {
          speaking = false;
          if (_vm_recording && typeof _onStopRecording === "function") _onStopRecording();
        }
      }
      _vm_raf = requestAnimationFrame(tick);
    };
    cancelAnimationFrame(_vm_raf);
    _vm_raf = requestAnimationFrame(tick);
  } catch (e) {
    console.warn("VAD arm failed:", e);
  }
}
