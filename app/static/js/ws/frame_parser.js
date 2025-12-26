// app/static/js/ws/frame_parser.js
// Decodes incoming WS messages (JSON/msgpack/control frames) and forwards
// normalized frames to a handler.
//
// This module is intentionally small and predictable:
//  - connection.js owns the low-level socket + binaryType.
//  - frame_parser.js owns "data → JS frame" and light normalization.
//  - ws_client.js owns policy/turn/mic behavior.
//
// NOTE:
// Binary frames that fail msgpack decoding with known "non-control" errors
// (e.g., trailing bytes, unsupported prefixes/ext, truncated buffers) are now
// treated as raw binary (audio) payloads instead of hard parse errors.
// Parse error logging is rate-limited to avoid console spam.
//
// Contract:
//   createFrameParser({
//     hubLog,                 // (label, detail) => void
//     logStage,               // (stage, meta) => void
//     connection,             // createWsConnection(...) instance
//     handleMessageFrame,     // (frame) => void
//     handleErrorFrame,       // (frame) => void
//     getAudioPlayer,         // () => window.AudioPlayer | null (optional)
//     ignoredVendorMessages,  // Set<string> of vendor messageType values to drop
//   }) -> {
//     normalizeIncomingFrame,
//     processControlFrameObject,
//     parseMessageData,
//     handleRawMessageData,
//   }

import { decodeMessagePack } from "../utils/msgpack.mjs";

const DEFAULT_IGNORED_VENDOR_MESSAGES = new Set([
  "AddPartialTranscript",
  "AddTranscript",
]);

const MSGPACK_PARSE_ERROR_LIMIT = 5;
const MSGPACK_ERROR_COUNTS = Object.create(null);
const KNOWN_BINARY_MSGPACK_ERRORS = [
  "Trailing bytes after msgpack payload",
  "Unsupported msgpack fixed ext format",
  "Unsupported msgpack prefix",
  "Unsupported msgpack ext format",
  "Truncated msgpack buffer",
];
const AUDIO_FRAME_LOG_LIMIT = 3;

export function createFrameParser({
  hubLog,
  logStage,
  connection,         // createWsConnection(...), if needed
  handleMessageFrame, // function(frame) from ws_client.js
  handleErrorFrame,
  getAudioPlayer = () =>
    (typeof window !== "undefined" ? window.AudioPlayer : null),
  getPhase = () => (typeof window !== "undefined" ? window.AppState?.phase || null : null),
  ignoredVendorMessages = DEFAULT_IGNORED_VENDOR_MESSAGES,
}) {
  // --- Codec negotiation ----------------------------------------------------

  // --- Helpers --------------------------------------------------------------

  function cloneArrayBuffer(view) {
    if (view instanceof ArrayBuffer) {
      return view.slice(0);
    }
    if (ArrayBuffer.isView?.(view)) {
      try {
        return view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength);
      } catch (err) {
        console.warn("frame_parser failed to slice ArrayBuffer view", err);
      }
    }
    return null;
  }

  function logLimitedParseError(kind, message) {
    if (!message) return;
    const key = `${kind}:${message}`;
    MSGPACK_ERROR_COUNTS[key] = (MSGPACK_ERROR_COUNTS[key] || 0) + 1;
    if (MSGPACK_ERROR_COUNTS[key] > MSGPACK_PARSE_ERROR_LIMIT) {
      return;
    }
    try {
      console.warn(`frame_parser ${kind} decode failed`, message);
    } catch {}
    try {
      hubLog?.("client.ws.parse_error", { kind, error: message });
    } catch {}
  }

  function tryDecodeMsgpackFrame(view) {
    try {
      const decoded = decodeMessagePack(view);
      if (
        decoded &&
        typeof decoded === "object" &&
        !Array.isArray(decoded) &&
        typeof decoded.type === "string"
      ) {
        return { frame: decoded };
      }
      return { frame: null };
    } catch (err) {
      const message = err?.message || String(err || "decode_error");
      const treatAsRaw = KNOWN_BINARY_MSGPACK_ERRORS.some((prefix) => message.startsWith(prefix));
      return { frame: null, error: message, treatAsRaw };
    }
  }

  function isVendorFrame(frame) {
    if (!frame || typeof frame !== "object") return false;
    const vendor = frame.vendor || frame.provider || frame.source;
    const msgType = frame.messageType || frame.message_type || frame.kind;
    return typeof vendor === "string" && typeof msgType === "string";
  }

  function isIgnoredVendorFrame(frame) {
    if (!isVendorFrame(frame)) return false;
    const msgType =
      frame.messageType || frame.message_type || frame.kind || frame.type;
    return typeof msgType === "string" && ignoredVendorMessages.has(msgType);
  }

  function isErrorFrame(frame) {
    if (!frame || typeof frame !== "object") return false;
    if (frame.type === "error" || frame.level === "error") return true;
    if (frame.error && typeof frame.error === "object") return true;
    if (typeof frame.code === "string" && frame.code.toLowerCase().includes("error")) {
      return true;
    }
    return false;
  }

  // --- Normalization --------------------------------------------------------

  function normalizeIncomingFrame(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }

    // Some vendors wrap their payloads, e.g. { data: {...} } or
    // { message: {...} }. Unwrap shallowly if it looks safe.
    if (
      raw.data &&
      typeof raw.data === "object" &&
      !Array.isArray(raw.data) &&
      typeof raw.type !== "string" &&
      typeof raw.data.type === "string"
    ) {
      raw = raw.data;
    } else if (
      raw.message &&
      typeof raw.message === "object" &&
      !Array.isArray(raw.message) &&
      typeof raw.type !== "string" &&
      typeof raw.message.type === "string"
    ) {
      raw = raw.message;
    }

    // Ensure .type is a simple string when present.
    if (typeof raw.type === "string") {
      raw.type = raw.type.trim();
    }

    return raw;
  }

  // --- Core parsing ---------------------------------------------------------

  function parseMessageData(data) {
    if (data == null) {
      return null;
    }

    let frame = null;
    let binary = false;

    if (typeof data === "string") {
      // Text frames are JSON.
      try {
        frame = JSON.parse(data);
      } catch (err) {
        console.warn("frame_parser JSON parse failed", err);
        try {
          hubLog?.("client.ws.parse_error", {
            kind: "json",
            error: err?.message,
          });
        } catch {}
        return null;
      }
    } else if (
      data instanceof ArrayBuffer ||
      ArrayBuffer.isView?.(data)
    ) {
      binary = true;
      const view =
        data instanceof Uint8Array
          ? data
          : new Uint8Array(
            data instanceof ArrayBuffer
              ? data
              : data.buffer,
            data.byteOffset || 0,
            data.byteLength
          );
      const decodeResult = tryDecodeMsgpackFrame(view);
      frame = decodeResult?.frame || null;

      if (!frame) {
        if (decodeResult?.treatAsRaw) {
          const bufferClone = cloneArrayBuffer(view);
          if (bufferClone) {
            return { kind: "binary", format: "raw", buffer: bufferClone };
          }
        } else if (decodeResult?.error && view.byteLength <= 256) {
          logLimitedParseError("msgpack", decodeResult.error);
        }
        // Not a structured control frame; let higher layers treat this as
        // pure binary (e.g. audio). That path does NOT go through frame_parser.
        return { kind: "binary", format: "raw", buffer: cloneArrayBuffer(view) };
      }
    } else {
      console.warn("frame_parser unknown WS message type", {
        type: typeof data,
        constructor: data && data.constructor && data.constructor.name,
      });
      return null;
    }

    if (!frame || typeof frame !== "object") {
      return null;
    }

    return { frame, binary, kind: "control", format: binary ? "msgpack" : "json" };
  }

  // --- Routing --------------------------------------------------------------

  function processControlFrameObject(frame) {
    // This is called *after* normalization, only for structured objects.
    if (!frame || typeof frame !== "object") {
      return;
    }

    // Ignore noisy vendor-only messages we don't surface in the UI.
    if (isIgnoredVendorFrame(frame)) {
      try {
        hubLog?.("client.ws.vendor_ignored", {
          vendor: frame.vendor || frame.provider || frame.source || null,
          messageType:
            frame.messageType || frame.message_type || frame.kind || null,
        });
      } catch {}
      return;
    }

    // Error frames get their own handler so we can banner/log separately.
    if (isErrorFrame(frame)) {
      try {
        handleErrorFrame?.(frame);
      } catch (err) {
        console.warn("frame_parser error handler crashed", err);
      }
      return;
    }

    // Everything else is a normal message frame from the server.
    try {
      handleMessageFrame?.(frame);
    } catch (err) {
      console.error("frame_parser message handler crashed", err);
      try {
        hubLog?.("client.ws.parse_handler_crash", {
          error: err?.message,
          type: frame.type || null,
        });
      } catch {}
    }
  }

  // --- Raw entrypoint from connection.js -----------------------------------

  let rawBinaryDropLogged = false;
  let audioFrameLogCount = 0;
  const AUDIO_WS_LOG_MAX = 5;
  let audioWsLogCount = 0;
  let ttsPlaybackGateOpen = false;
  let ttsGateState = "closed";
  let ttsGateUtteranceId = null;
  let ttsAudioExpected = false;
  let ttsExpectedDeadlineMs = 0;
  let ttsExpectedTimerId = null;
  let ttsFirstFrameLogged = false;
  let ttsAutoOpenLoggedForUtt = false;
  let pendingAudioDescriptor = null;
  const AUDIO_DROP_LOG_LIMIT = 5;
  let audioDropLogCount = 0;
  const TTS_EXPECTED_WINDOW_MS = 3000;

  /**
   * Phase 0 invariants:
   * - Greet MUST NOT open ASR or require ASR readiness.
   * - First user speech after greet MUST emit exactly one client.turn_start.
   * - TTS audio MUST only play when explicitly expected (bounded window).
   * - Mic hard-fail MUST stop PCM send until user retry.
   *
   * If any of these change, Phase 0 assumptions are broken.
   *
   * Reference: AskChip Architecture – Step 3, Phase 0
   */
  function resolvePhase() {
    try {
      return typeof getPhase === "function" ? getPhase() : null;
    } catch (_) {
      return typeof window !== "undefined" ? window.AppState?.phase || null : null;
    }
  }

  function resolveUttId(frame) {
    if (!frame || typeof frame !== "object") return null;
    return (
      frame.utt_id ||
      frame.utterance_id ||
      frame?.meta?.utt_id ||
      frame?.meta?.utterance_id ||
      null
    );
  }

  function logGateTransition(prev, next, reason, uttId) {
    try {
      logStage?.("client.tts_gate_transition", {
        prev,
        next,
        reason,
        utt_id: uttId || null,
      });
    } catch (_) {}
  }

  function logDroppedAudio(reason, meta = {}) {
    audioDropLogCount += 1;
    if (audioDropLogCount <= AUDIO_DROP_LOG_LIMIT) {
      try {
        console.warn("frame_parser dropping raw audio", { reason, ...meta });
      } catch (_) {}
    }
    try {
      logStage?.("client.tts_audio_drop", {
        reason,
        bytes: meta?.size || meta?.bytes || null,
        utt_id: meta?.utt_id || ttsGateUtteranceId || null,
        gate_state: ttsGateState,
        phase: resolvePhase(),
      });
    } catch (_) {}
    try {
      hubLog?.("client.ws.audio_drop", { reason, ...meta });
    } catch (_) {}
  }

  function resetTtsGate(reason = "reset", { clearDescriptor = false } = {}) {
    const prevState = ttsGateState;
    ttsPlaybackGateOpen = false;
    ttsGateState = "closed";
    audioDropLogCount = 0;
    if (clearDescriptor) {
      pendingAudioDescriptor = null;
    }
    ttsAudioExpected = false;
    ttsExpectedDeadlineMs = 0;
    if (ttsExpectedTimerId) {
      clearTimeout(ttsExpectedTimerId);
      ttsExpectedTimerId = null;
    }
    ttsFirstFrameLogged = false;
    ttsAutoOpenLoggedForUtt = false;
    try {
      logGateTransition(prevState, ttsGateState, reason, ttsGateUtteranceId);
    } catch (_) {}
    ttsGateUtteranceId = null;
  }

  function applyPendingDescriptor(audioPlayer) {
    if (!ttsPlaybackGateOpen || !pendingAudioDescriptor) return;
    if (audioPlayer && typeof audioPlayer.setDescriptor === "function") {
      audioPlayer.setDescriptor(pendingAudioDescriptor);
      pendingAudioDescriptor = null;
    }
  }

  function openTtsGate(reason = "tts.start", uttId = null) {
    const prevState = ttsGateState;
    ttsPlaybackGateOpen = true;
    ttsGateState = "open";
    audioDropLogCount = 0;
    ttsFirstFrameLogged = false;
    if (uttId) {
      ttsGateUtteranceId = uttId;
    }
    try {
      logGateTransition(prevState, ttsGateState, reason, ttsGateUtteranceId);
    } catch (_) {}
    try {
      const audioPlayer = getAudioPlayer?.();
      applyPendingDescriptor(audioPlayer);
    } catch (_) {}
  }

  function setTtsAudioDescriptor(descriptor) {
    pendingAudioDescriptor = descriptor || null;
    if (ttsPlaybackGateOpen) {
      try {
        const audioPlayer = getAudioPlayer?.();
        applyPendingDescriptor(audioPlayer);
      } catch (_) {}
    }
  }

  function scheduleTtsExpectedDeadline() {
    if (ttsExpectedTimerId) {
      clearTimeout(ttsExpectedTimerId);
      ttsExpectedTimerId = null;
    }
    if (!ttsAudioExpected || !ttsExpectedDeadlineMs) {
      return;
    }
    const delayMs = Math.max(0, ttsExpectedDeadlineMs - Date.now());
    ttsExpectedTimerId = setTimeout(() => {
      ttsExpectedTimerId = null;
      if (!ttsAudioExpected || !ttsExpectedDeadlineMs) {
        return;
      }
      if (Date.now() >= ttsExpectedDeadlineMs) {
        ttsAudioExpected = false;
        ttsExpectedDeadlineMs = 0;
        ttsGateUtteranceId = null;
      }
    }, delayMs);
  }

  function markTtsAudioExpected(uttId) {
    ttsAudioExpected = true;
    ttsExpectedDeadlineMs = Date.now() + TTS_EXPECTED_WINDOW_MS;
    if (uttId) {
      ttsGateUtteranceId = uttId;
    }
    scheduleTtsExpectedDeadline();
  }

  function handleTtsGateFrame(frame) {
    const type = typeof frame?.type === "string" ? frame.type : null;
    const uttId = resolveUttId(frame);
    if (
      type === "server.greet_start" ||
      type === "server.tts_start" ||
      type === "greet.start" ||
      type === "greet.begin" ||
      type === "greet"
    ) {
      ttsAutoOpenLoggedForUtt = false;
      markTtsAudioExpected(uttId);
      openTtsGate(type, ttsGateUtteranceId);
      return;
    }
    if (type === "tts.start") {
      ttsAutoOpenLoggedForUtt = false;
      markTtsAudioExpected(uttId);
      openTtsGate("tts.start", uttId || ttsGateUtteranceId);
    } else if (type === "tts.end" || type === "tts.cancel" || type === "tts.error") {
      const currentUttId = ttsGateUtteranceId;
      if (uttId && currentUttId && uttId !== currentUttId) {
        try {
          logStage?.("client.tts_gate_transition", {
            prev: ttsGateState,
            next: ttsGateState,
            reason: "utt_id_mismatch",
            utt_id: currentUttId,
          });
        } catch (_) {}
        return;
      }
      resetTtsGate(type);
    }
  }

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    try {
      window.addEventListener("ws.close", () => resetTtsGate("ws.close", { clearDescriptor: true }));
    } catch (_) {}
  }

  function handleRawBinaryFrame(buffer) {
    if (!ttsPlaybackGateOpen) {
      const now = Date.now();
      const withinWindow = ttsAudioExpected && now <= ttsExpectedDeadlineMs;
      if (ttsAudioExpected && !withinWindow) {
        ttsAudioExpected = false;
        ttsExpectedDeadlineMs = 0;
        ttsGateUtteranceId = null;
      }
      if (ttsAudioExpected && withinWindow) {
        if (!ttsAutoOpenLoggedForUtt) {
          ttsAutoOpenLoggedForUtt = true;
          try {
            logStage?.("client.tts_gate_auto_open", {
              reason: "audio_first_frame",
              within_window: withinWindow,
              deadline_ms_remaining: Math.max(0, ttsExpectedDeadlineMs - now),
              utt_id: ttsGateUtteranceId || null,
            });
          } catch (_) {}
        }
        openTtsGate("audio_first_frame", ttsGateUtteranceId);
      } else {
        logDroppedAudio("tts_gate_closed_no_recent_start", { size: buffer?.byteLength || null });
        return;
      }
    }
    const audioPlayer = getAudioPlayer?.();
    if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
      try {
        applyPendingDescriptor(audioPlayer);
        if (!ttsFirstFrameLogged) {
          ttsFirstFrameLogged = true;
          logStage?.("client.tts_audio_first_frame", {
            utt_id: ttsGateUtteranceId || null,
            bytes: buffer?.byteLength || null,
          });
        }
        if (audioWsLogCount < AUDIO_WS_LOG_MAX) {
          audioWsLogCount += 1;
          try {
            console.log("client.ws.tts_audio_chunk_frame", {
              type: "binary.raw", // binary audio frames are untyped
              size: buffer?.byteLength || null,
            });
          } catch (_) {}
        }
        audioPlayer.enqueueChunk(buffer);
        if (audioFrameLogCount < AUDIO_FRAME_LOG_LIMIT) {
          audioFrameLogCount += 1;
          logStage?.("client.ws.frame", { kind: "binary", type: "audio" });
        }
      } catch (err) {
        logLimitedParseError("binary", err?.message || "audio_enqueue_failed");
      }
      return;
    }

    if (!rawBinaryDropLogged) {
      rawBinaryDropLogged = true;
      console.warn("frame_parser received binary frame without audio handler; dropping");
    }
    logDroppedAudio("no_audio_handler", { size: buffer?.byteLength || null });
  }

  function handleRawMessageData(data) {
    try {
      const parsed = parseMessageData(data);
      if (!parsed) {
        // Unknown / unstructured frame; someone else (audio, etc.) may own it.
        return;
      }

      if (parsed.kind === "binary" && parsed.format === "raw") {
        if (parsed.buffer) {
          handleRawBinaryFrame(parsed.buffer);
        }
        return;
      }

      let { frame } = parsed;
      const { binary } = parsed;

      frame = normalizeIncomingFrame(frame);
      if (!frame) {
        return;
      }

      handleTtsGateFrame(frame);

      if (binary && (
        frame.type === "audio.chunk" ||
        frame.type === "audio.start" ||
        frame.type === "audio.end"
      )) {
        if (!ttsPlaybackGateOpen) {
          logDroppedAudio("tts_gate_closed_no_recent_start", {
            type: frame.type,
            bytes: frame?.bytes || frame?.byteLength || null,
            utt_id: resolveUttId(frame),
          });
          return;
        }
      }

      if (binary) {
        // In practice, if we successfully decoded msgpack into an object,
        // we can treat it like any other control/message frame.
        logStage?.("client.ws.frame", {
          kind: "binary",
          type: frame.type || null,
        });
      } else {
        logStage?.("client.ws.frame", {
          kind: "text",
          type: frame.type || null,
        });
      }

      processControlFrameObject(frame);
    } catch (outerErr) {
      console.error("Uncaught exception in handleRawMessageData", outerErr);
      try {
        hubLog?.("client.ws.parse_crash", {
          error: outerErr?.message,
          source: "frame_parser",
        });
      } catch {}
    }
  }

  return {
    normalizeIncomingFrame,
    processControlFrameObject,
    parseMessageData,
    handleRawMessageData,
    setTtsAudioDescriptor,
    resetTtsGate,
  };
}
