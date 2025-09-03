// chat/send.js — sendChat, voice-once handler, follow-up governor (patched & cleaned)

// Core & UI
import { j, wsConnect } from "../core/api.js";
import { _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep, _chipClearIdleNudge } from "../core/state.js";
import { appendMessage, appendActions, _chipRenderSuggestions } from "./ui.js";

// Voice
import {
  tryPlayWithMouth,
  _vm_stopPlayback,
  driveVisemes,
  respAcquire,
  respRelease,
  startStream,
  pushPCM16Base64,
  stopStream,
  cancelActiveSpeech
} from "../voice/playback.js";

// Auth / profile gate
import { gate } from "../auth/profile.js";

// Barge-in: stop playback and cancel any in-flight TTS
window.addEventListener("chip:bargein", () => { try { cancelActiveSpeech(); } catch {} });

/* --------------------------- Chat lane wiring --------------------------- */

let _getChatLane = null;
let _setChatLane = null;
let _armVAD = null;
export function wireChatLane(getFn, setFn) { _getChatLane = getFn; _setChatLane = setFn; }
export function setArmVAD(fn) { _armVAD = fn; }

/* ---------------- FOLLOW-UP GOVERNOR + HUMANE BEHAVIOR ------------------ */

let _fu_lastOfferedAt = 0;
let _fu_turnsSinceOffer = 99;
const _FU_MIN_INTERVAL_MS = 18000;
const _FU_MIN_TURNS = 2;

export function _limitWords(text, n = 20) {
  const words = (text || "").trim().split(/\s+/);
  return words.length <= n ? (text || "") : words.slice(0, n).join(" ") + "...";
}
export function _isEndTrigger(message = "") {
  const s = (message || "").toLowerCase();
  return /(end chat|we['’]re done|that['’]s all|thanks[, ]*chip|bye[, ]*chip|goodbye)/.test(s);
}

const _pureKeywords = [
  "pure storage","flasharray","flashblade","fb//s","fb//e","fa//x","fa//c","purity","safemode","safe mode",
  "evergreen","portworx","px","px-backup","fusion","nvme","nvme/tcp","directflash","s3","object","file",
  "block","snapshot","replication","px-dbaas","pure1","array","arrays"
];

const _canned = {
  no_audio: [
    "I didn’t catch that—mind trying again?",
    "Could you say that once more for me?"
  ],
  offtopic: [
    "I’m tuned for Pure Storage topics. Want to talk Pure hardware or software?",
    "Let’s focus on Pure for now—what are you working on?"
  ],
  unsure: [
    "Not sure I follow. Product, version, and goal? I’ll get specific.",
    "Hmm—can you share product and version?",
    "Need a bit more detail. What product are we on?"
  ]
};
function _pick(arr){ return arr[Math.floor(Math.random()*arr.length)]; }
function _isLikelyEnglish(s){
  if (!s) return false;
  const ascii = s.replace(/[^\x00-\x7F]/g,"");
  return ascii.length / s.length >= 0.75;
}
function _mentionsPureTopic(s){
  const t = (s||"").toLowerCase();
  return _pureKeywords.some(k => t.includes(k));
}
function _classifyInput(transcript){
  const t = (transcript||"").trim();
  if (!t) return "no_audio";
  if (!_isLikelyEnglish(t)) return "unsure";
  if (!_mentionsPureTopic(t)) return "offtopic";
  if (t.split(/\s+/).length < 3) return "unsure";
  return "ok";
}

async function _handleCanned(kind) {
  const line = _pick(_canned[kind] || []);
  if (line) {
    appendMessage("assistant", line, _getChatLane() === "text" ? "text" : "live");
    try {
      const r = await fetch("/api/v1/voice/tts-with-visemes", {
        credentials: 'include',
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ text: line })
      }).then(r=>r.json()).catch(()=>null);
      if (r) {
        if (r.audio) { try { await tryPlayWithMouth(r.audio); } catch {} }
        else if (r.audio_base64 || r.audio_b64) {
          try {
            const a = new Audio("data:audio/mpeg;base64," + (r.audio_base64 || r.audio_b64));
            await a.play();
          } catch {}
        }
      }
    } catch {}
  }
  _chipSetState("followup");
}

/* --------------------------- End conversation --------------------------- */

export async function _chipEndConversation() {
  try {
    _chipStep("end", "user requested");
    fetch("/api/v1/voice/tts-with-visemes", {
      credentials: 'include',
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text: "Anytime. I’ll be right here when you need me." })
    }).catch(()=>{});
  } finally {
    _chipClearIdleNudge();
    respRelease();
    stopStream();
    _chipSetState("idle");
    _chipGuide("Press Start or Chat to speak with Chip.");
  }
}

/* ---------------------- Streaming chat WS (optional) -------------------- */

let _ws = null;
let _wsReady = false;
let _streamPrimed = false; // have we called startStream() for current turn?
let _pendingThinking = null;

function _ensureWS() {
  if (_ws && _ws.isOpen()) return true;
  _ws = wsConnect("/ws/v1/chat", {
    onOpen: () => { _wsReady = true; },
    onClose: () => { _wsReady = false; _streamPrimed = false; },
    onError: () => { _wsReady = false; },
    onMessage: async (msg) => {
      try {
        if (msg.type === "partial_text") {
          if (_pendingThinking) _pendingThinking.textContent = _limitWords(msg.text || "", 20);
        } else if (msg.type === "final_text") {
          if (_pendingThinking) { _pendingThinking.textContent = _limitWords(msg.text || "", 20); _pendingThinking = null; }
        } else if (msg.type === "audio_chunk") {
          if (!_streamPrimed) {
            const ok = await startStream({ sampleRate: msg.sr || 24000, channels: 1 });
            _streamPrimed = ok;
          }
          if (msg.b16) pushPCM16Base64(msg.b16, { sampleRate: msg.sr || 24000 });
        } else if (msg.type === "end") {
          _streamPrimed = false;
          respRelease();
          _chipSetState("followup");
        }
        // errors are handled below
      } catch (e) {
        console.warn("WS onMessage error:", e);
      }
    }
  });
  return true;
}

/* --------------------- Sentence-level speech helper --------------------- */

export function _splitIntoSentences(text) {
  const parts = (text || "").trim().split(/(?<=[.!?])\s+(?=[A-Z0-9])/g);
  return parts.filter(s => s && s.trim());
}

async function _speakReplySentenceLevel(replyText) {
  const segments = _splitIntoSentences(replyText);
  if (!segments.length) return;
  const ac = new AbortController();
  const onCancel = () => ac.abort();
  window.addEventListener("chip:tts-cancel", onCancel);
  try {
    for (const seg of segments) {
      if (ac.signal.aborted) break;
      const res = await fetch("/api/v1/voice/tts-with-visemes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: seg }),
        signal: ac.signal
      }).catch(() => null);

      if (!res || ac.signal.aborted) break;

      const data = await res.json().catch(() => ({}));
      if (data && (data.ok || data.audio || data.audio_b64 || data.audio_base64)) {
        if (data.audio) {
          try { await tryPlayWithMouth(data.audio); } catch {}
        } else if (data.audio_b64 || data.audio_base64) {
          try {
            const a = new Audio("data:audio/mpeg;base64," + (data.audio_b64 || data.audio_base64));
            await a.play();
          } catch {}
        }
      }
    }
  } finally {
    window.removeEventListener("chip:tts-cancel", onCancel);
  }
}

/**
 * sendChat(message)
 * - classifies for quick canned responses (no network)
 * - tries WS streaming; if available, WS handles text + TTS; we update the “thinking” bubble from WS text
 * - falls back to REST /api/v1/chat (and optional sentence-level TTS for live lane)
 *
 * Exposed as a named export so main.js can import { sendChat } from './chat/send.js'
 */
export async function sendChat(message) {
  if (!message || !message.trim()) return;

  if (_isEndTrigger(message)) { _chipEndConversation(); return; }

  const okGate = await gate();
  if (!okGate.ok) return;

  _chipClearIdleNudge();
  _fu_turnsSinceOffer = Math.min(_fu_turnsSinceOffer + 1, 99);

  const cls = _classifyInput(message);
  if (cls !== "ok") {
    appendMessage("user", message.trim(), null);
    await _handleCanned(cls);
    return;
  }

  appendMessage("user", message, null);
  const thinking = appendMessage("assistant", "…", _getChatLane());

  // --- Try streaming path first; gracefully fall back to REST ---
  let usedStreaming = false;
  try {
    _ensureWS();
    if (_ws && _ws.isOpen()) {
      _pendingThinking = thinking;
      _ws.send({
        type: "user_text",
        text: message.trim(),
        lane: _getChatLane(),
        language: "en",
        domain: "pure-storage",
        short: true
      });
      usedStreaming = true;
    }
  } catch {
    usedStreaming = false;
  }

  if (usedStreaming) {
    _pendingThinking = thinking;
    return; // streaming path handled turn
  }

  // WS-only mode (no REST fallback)
  _chipSetState("followup");
  _chipGuide("Chat stream isn’t available. Please try again.");
  return;

// --- REST fallback: /api/v1/chat ---
  try {
    _chipSetState("thinking");

    // Preflight cleanup (do not let this block the turn if it fails)
    try {
      await window.AskChip?.voice?.stop?.();           // cancel any TTS playback
      try { _vm_stopPlayback(); } catch {}
      if (!window.__chipAbortCtl) window.__chipAbortCtl = new AbortController();
    } catch {}

    const resp = await fetch("/api/v1/chat", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: message.trim(),
        lane: _getChatLane(),
        short: true
      }),
      signal: window.__chipAbortCtl.signal
    });

    const data = await resp.json().catch(() => ({}));
    const textRaw = (data.reply_text ?? data.reply ?? "").trim();

    _chipSetState("responding");

    if (textRaw) {
      thinking.textContent = textRaw;

      // Speak sentence-by-sentence if we're in live lane
      if (_getChatLane() === "live") {
        try { await _speakReplySentenceLevel(textRaw); } catch {}
      }
    } else {
      thinking.textContent = "(no reply)";
    }

    if (Array.isArray(data.actions) && data.actions.length) appendActions(data.actions);
    if (Array.isArray(data.suggestions)) {
      _chipRenderSuggestions(data.suggestions, (s) => {
        if (/end chat/i.test(s)) { _chipEndConversation(); return; }
        sendChat(s);
      });
    }

    if (textRaw && _shouldOfferFollowup({ userText: message, assistantText: textRaw })) {
      _offerFollowupOnce();
    }

    if (data.end === true) { _chipEndConversation(); return; }
  } catch (err) {
    console.error("[AskChip] /api/v1/chat failed", err);
    thinking.textContent = "Sorry — something went wrong.";
  } finally {
    _chipSetState("followup");
  }
}

/* --------------------------- Voice-once (REST) -------------------------- */

export async function handleVoiceOnceResponse({ blob, durMs }) {
  if (durMs < 600) {
    _chipStep("voice-skip", { reason: "short-speech", durMs });
    await _handleCanned("no_audio");
    return;
  }

  const fd = new FormData();
  fd.append("audio", blob, "clip.webm");
  fd.append("language", "en");

  _chipSetState("thinking");
  _chipStep("POST /api/v1/voice/stt →", { size: blob.size, durMs });

  const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
  const data = await res.json().catch(() => ({}));
  _chipStep("← /api/v1/voice/stt", data);
  // Phase 1 pipeline: if server didn't return reply/audio, synthesize via v1 endpoints
  if (!data.transcript && data.text) data.transcript = data.text;
  try {
    if (!data.reply && (data.transcript || "").trim()) {
      const respChat = await fetch("/api/v1/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: data.transcript, lane: "live", short: true })
      });
      const dj = await respChat.json().catch(() => ({}));
      data.reply = (dj.reply_text ?? dj.reply ?? dj.text ?? "").trim();
    }
  } catch {}
  try {
    if (!data.audio_b64 && !data.audio && (data.reply || "").trim()) {
      const respTTS = await fetch("/api/v1/voice/tts-with-visemes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: data.reply })
      });
      const tj = await respTTS.json().catch(() => ({}));
      data.audio_b64 = tj.audio_base64 || tj.audio_b64 || null;
      data.audio = tj.audio || null;
      data.visemes = tj.visemes || null;
    }
  } catch {}

  const conf = typeof data.confidence === "number" ? data.confidence : null;
  if (conf !== null && conf < 0.6) {
    const fallback = (data.transcript || "").trim() ? "unsure" : "no_audio";
    if (data.transcript) appendMessage("user", data.transcript, "live");
    await _handleCanned(fallback);
    return;
  }

  if (data.transcript && _isEndTrigger(data.transcript)) {
    _chipEndConversation();
    return;
  }

  // Show user’s line and quick assistant summary (20 words)
  if (data.transcript) appendMessage("user", data.transcript, "live");

  const textRaw = (data.reply_text ?? data.reply ?? "").trim();
  const text = _limitWords(textRaw, 20);
  appendMessage("assistant", text || "👍", "live");

  if (Array.isArray(data.actions) && data.actions.length) appendActions(data.actions);
  if (Array.isArray(data.suggestions)) {
    _chipRenderSuggestions(data.suggestions, (s) => {
      if (/end chat/i.test(s)) { _chipEndConversation(); return; }
      sendChat(s);
    });
  }

  if (_shouldOfferFollowup({ userText: data.transcript || "", assistantText: text })) {
    _offerFollowupOnce();
  }

  // Voice playback (one-shot)
  let played = false;
  if (data.audio_b64 || data.audio || data.audio_base64) {
    _chipSetState("responding");
    if (!respAcquire()) {
      _chipSetState("followup");
    } else {
      try {
        if (data.audio_b64 || data.audio_base64) {
          const a = new Audio("data:audio/mpeg;base64," + (data.audio_b64 || data.audio_base64));
          a.addEventListener("ended", () => { respRelease(); _chipSetState("followup"); }, { once: true });
          await a.play();
          played = true;
        } else if (data.audio) {
          await tryPlayWithMouth(data.audio);
          played = true;
          respRelease();
        }
      } catch (e) {
        console.error("voice-once playback error:", e);
        respRelease();
      }
    }
  }
  if (!played) _chipSetState("followup");

  if (Array.isArray(data.visemes) && data.visemes.length) {
    try { driveVisemes(data.visemes); } catch {}
  }

  if (data.end === true) {
    await _chipEndConversation();
  }
}

/* -------------------- Follow-up offer + heuristics ---------------------- */

function _isQuestion(text = "") {
  const t = (text || "").trim();
  return /\?$/.test(t) || /^(how|why|what|where|when|who)\b/i.test(t);
}
function _isFollowupWorthy(userText = "", assistantText = "") {
  if (!assistantText || assistantText.length < 6) return false;
  if (/download|attached|opened|scheduled|sent/i.test(assistantText)) return false;
  if (_isQuestion(userText)) return false;
  return true;
}
function _shouldOfferFollowup({ userText = "", assistantText = "" } = {}) {
  const now = Date.now();
  if ((now - _fu_lastOfferedAt) < _FU_MIN_INTERVAL_MS) return false;
  if (_fu_turnsSinceOffer < _FU_MIN_TURNS) return false;
  return _isFollowupWorthy(userText, assistantText);
}
function _offerFollowupOnce() {
  _fu_lastOfferedAt = Date.now();
  _fu_turnsSinceOffer = 0;
  const prompts = [
    "Want me to dig a bit deeper?",
    "Need a quick example?",
    "Should I pull the numbers behind that?",
    "I can check related items if you want.",
  ];
  _chipRenderSuggestions([prompts[Math.floor(Math.random()*prompts.length)], "End chat"], (s) => {
    if (/end chat/i.test(s)) { _chipEndConversation(); return; }
    sendChat(s);
  });
}
