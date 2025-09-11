/* Phase 14: UA-based capability detection + AudioWorklet gating */
export function detectAudioCaps(){
  const ua = (navigator.userAgent || '').toLowerCase();
  const isSafari  = /^((?!chrome|android).)*safari/.test(ua);
  const isFirefox = ua.includes('firefox');
  const isChrome  = !isSafari && !isFirefox && ua.includes('chrome');
  const workletSupported = !!(window.AudioWorklet && (AudioWorkletNode || window.AudioWorkletNode));
  return { isSafari, isFirefox, isChrome, workletSupported };
}
const __PH14_USE_WORKLET = !!(window.ASKCHIP?.features?.audio_worklet_enabled) && detectAudioCaps().workletSupported && !detectAudioCaps().isSafari;

import { API, TIMING } from "./config.js";
import { csrfHeader } from "./csrf.js";
import { getSID } from './util/sid.js';
import { setState, STATES } from "./state.js";

let vadArmed = false;
let thresholdBoost = 0;
let mediaStream;
let recorder;
let chunks = [];

/** --------------- CSRF helper (voice) --------------- **/
export function armVAD(boostDuringPlayback = 0){
  vadArmed = true;
  thresholdBoost = boostDuringPlayback;
}

export function disarmVAD(){
  vadArmed = false;
  thresholdBoost = 0;
}

export async function initMic(){
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
  return mediaStream;
}

export function speechStart(){
  if (!vadArmed) return;
  // Begin recording a single WebM/Opus blob
  if (!mediaStream) return;
  chunks = [];
  recorder = new MediaRecorder(mediaStream, { mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 128000 });
  recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  recorder.onstop = async () => {
    const blob = new Blob(chunks, { type: "audio/webm" });
    await postSTT(blob);
  };
  recorder.start();
}

export function speechEnd(){
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

async function postSTT(blob){
  try{
    setState(STATES.THINKING);
    // meta we would include: language lock, simple prosody placeholders
    const meta = { session_id: getSID(), language: "en", avg_rms: 0.0, max_rms: 0.0 };
    const form = new FormData();
    form.append("file", blob, "turn.webm");
    form.append("meta", JSON.stringify(meta));
    const r = await fetch(API.STT, {
      method: "POST",
      body: form,
      credentials: "include",
      headers: await csrfHeader()
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // The server would then stream response on WS; no-op here
  }catch(e){
    console.warn("STT error", e);
  }
}


export function getVADThresholds(){
  const cfg = (window.__ASKCHIP_CFG || {});
  return {
    attack_ms:  (cfg.vad_attack_ms ?? 12),
    release_ms: (cfg.vad_release_ms ?? 240),
    dbfs:       (cfg.vad_dbfs_threshold ?? -42),
  };
}