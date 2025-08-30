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
const _FU_BASE_PROB = 0.35;

function _seemsSatisfied(text = "") {
  const s = (text || "").toLowerCase();
  return /(thanks|got it|perfect|that helps|nice|good stuff)/.test(s);
}
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
  if (_seemsSatisfied(userText) || _isEndTrigger(userText)) return false;
  if (!_isFollowupWorthy(userText, assistantText)) return false;
  let p = _FU_BASE_PROB;
  if (assistantText.split(/\s+/).length < 14) p += 0.15;
  return Math.random() < p;
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

/* ------------------------------- Helpers -------------------------------- */

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
  offtopic: [
    "Interesting thought, but my wheelhouse is Pure Storage—FlashArray, FlashBlade, Portworx. Let’s pivot back. What Pure question can I help with?",
    "Fun angle, but I stick to Pure Storage. Want to talk FlashBlade or FlashArray instead?",
    "Not my lane—Pure Storage is. What’s your question on arrays, blades, or Portworx?"
  ],
  no_audio: [
    "I didn’t hear anything. Mic might be muted. Want to try again, or type your Pure Storage question?",
    "No audio picked up. Try again, or type it in.",
    "Nothing came through—mic on? You can also type your question."
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
      const r = await fetch("/api/speak", {
        credentials: 'include',
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ prompt: line, language: "en" })
      }).then(r=>r.json()).catch(()=>null);
      if (r && r.audio) { try { await tryPlayWithMouth(r.audio); } catch {} }
    } catch {}
  }
  _chipSetState("followup");
}

/* --------------------------- End conversation --------------------------- */

export async function _chipEndConversation() {
  try {
    _chipStep("end", "user requested");
    fetch("/api/speak", {
      credentials: 'include',
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ prompt: "Anytime. I’ll be right here when you need me.", language: "en" })
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

function _ensureWS() {
  if (_ws && _ws.isOpen()) return true;
  _ws = wsConnect((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/chat", {
    onOpen: () => { _wsReady = true; },
    onClose: () => { _wsReady = false; _streamPrimed = false; },
    onError: () => { _wsReady = false; },
    onMessage: async (msg) => {
      try {
        if (msg.type === "audio_chunk") {
          if (!_streamPrimed) {
            const ok = await startStream({ sampleRate: msg.sr || 24000, channels: 1 });
            _streamPrimed = ok;
          }
          if (msg.b16) pushPCM16Base64(msg.b16, { sampleRate: msg.sr || 24000 });
        } else if (msg.type === "end") {
          stopStream();
          _streamPrimed = false;
          respRelease();
          _chipSetState("followup");
        }
        // 'partial_text' / 'final_text' are intentionally no-ops here
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
      const res = await fetch("/api/voice/tts_with_visemes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: seg }),
        signal: ac.signal
      }).catch(() => null);

      if (!res || ac.signal.aborted) break;

      const data = await res.json().catch(() => ({}));
      if (data && (data.ok || data.audio || data.audio_b64)) {
        if (data.audio) {
          try { await tryPlayWithMouth(data.audio); } catch {}
        } else if (data.audio_b64) {
          const a = new Audio("data:audio/mpeg;base64," + data.audio_b64);
          try { await a.play(); } catch {}
          await new Promise(r => a.addEventListener("ended", r, { once: true }));
        }
        if (Array.isArray(data.visemes)) { try { driveVisemes(data.visemes); } catch {} }
      }
    }
  } finally {
    window.removeEventListener("chip:tts-cancel", onCancel);
  }
}

/* ------------------------------- sendChat ------------------------------- */
/**
 * Send a user message to Chip.
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
    _chipSetState("thinking");
    _chipStep("WS /ws/chat →", { message: message.trim(), lane: _getChatLane() });

    _ensureWS();
    if (_ws && _ws.isOpen()) {
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
    // Ask server for a final text summary (optional)
    try {
      const res = await fetch("/api/chat/summary", {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lastOnly: true })
      }).then(r => (r.ok ? r.json() : null)).catch(() => null);

      _chipSetState("responding");
      const textRaw = ((res && (res.reply_text ?? res.reply)) || "").trim();
      const text = _limitWords(textRaw, 20);

      thinking.textContent = (_getChatLane() === "live" ? "🔊 " : "💬 ") + (text || "");
      if (Array.isArray(res?.actions)) appendActions(res.actions);

      if (Array.isArray(res?.suggestions)) {
        _chipRenderSuggestions(res.suggestions, (s) => {
          if (/end chat/i.test(s)) { _chipEndConversation(); return; }
          sendChat(s);
        });
      }

      if (text && _shouldOfferFollowup({ userText: message, assistantText: text })) {
        _offerFollowupOnce();
      }

      _chipSetState("followup");
      if (res?.end === true) { _chipEndConversation(); }
      return; // streaming path handled turn
    } catch {
      // If summary fails, continue to REST for text
    }
  }

  // --- REST fallback: /api/chat ---
  try {
    _chipSetState("thinking");

    // Preflight cleanup (do not let this block the turn if it fails)
    try {
      await window.AskChip?.voice?.stop?.();           // cancel any TTS playback
      if (window.__chipAbortCtl?.abort) window.__chipAbortCtl.abort();
      window.__chipAbortCtl = new AbortController();
    } catch (err) {
      console.warn("[AskChip] send preflight failed (continuing)", err);
    } finally {
      if (!window.__chipAbortCtl) window.__chipAbortCtl = new AbortController();
    }

    const resp = await fetch("/api/chat", {
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

    if (Array.isArray(data.actions)) appendActions(data.actions);

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
    console.error("[AskChip] /api/chat failed", err);
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
  _chipStep("POST /api/voice-once →", { size: blob.size, durMs });

  const res = await fetch("/api/voice-once", { method: "POST", body: fd, credentials: "include" });
  const data = await res.json().catch(() => ({}));
  _chipStep("← /api/voice-once", data);

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

  const cls = _classifyInput(data.transcript || "");
  if (cls !== "ok") {
    if (data.transcript) appendMessage("user", data.transcript, "live");
    await _handleCanned(cls);
    return;
  }

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

  let played = false;
  if (data.audio_b64 || data.audio) {
    _chipSetState("responding");
    if (!respAcquire()) {
      _chipSetState("followup");
    } else {
      try {
        if (data.audio_b64) {
          const a = new Audio("data:audio/mpeg;base64," + data.audio_b64);
          a.addEventListener("ended", () => { respRelease(); _chipSetState("followup"); }, { once: true });
          await a.play();
          played = true;
        } else if (data.audio) {
          await tryPlayWithMouth(data.audio);
          played = true;
          respRelease();
          _chipSetState("followup");
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
