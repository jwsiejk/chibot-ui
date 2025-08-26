// voice/record.js — recording, stop, complete → delegate to handler
import { _uiBeep } from "./playback.js";
import { _vm_updateMicUI } from "./vad.js";

let _vm_stream = null;
let _vm_rec = null;
let _vm_chunks = [];
let _vm_recording = false;
let _vm_rec_startedAt = 0;

export function setStream(stream) { _vm_stream = stream; }
export function isRecording() { return _vm_recording; }
export function getBlob() { return new Blob(_vm_chunks, { type: "audio/webm" }); }
export function getDurationMs() { return Math.max(0, performance.now() - (_vm_rec_startedAt || performance.now())); }

export async function _vm_startRecording() {
  if (_vm_recording) return;
  _vm_chunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
  _vm_rec = new MediaRecorder(_vm_stream, mime ? { mimeType: mime } : undefined);
  _vm_rec.addEventListener("dataavailable", (e) => { if (e?.data?.size) _vm_chunks.push(e.data); });
  _vm_rec.start();
  _vm_rec_startedAt = performance.now();
  _vm_recording = true;
  _vm_updateMicUI(true, true);
  _uiBeep(1020, 80);
}

export async function _vm_stopRecording(onComplete) {
  if (!_vm_recording) return;
  _vm_recording = false;
  try {
    _vm_rec.addEventListener("stop", () => {
      _vm_updateMicUI(true, false);
      _uiBeep(660, 60);
      if (typeof onComplete === "function") onComplete(getBlob(), getDurationMs());
    }, { once: true });
    _vm_rec?.stop();
  } catch {}
}
