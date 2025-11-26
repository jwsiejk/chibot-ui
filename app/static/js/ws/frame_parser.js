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

  function handleRawBinaryFrame(buffer) {
    const audioPlayer = getAudioPlayer?.();
    if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
      try {
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
  };
}
