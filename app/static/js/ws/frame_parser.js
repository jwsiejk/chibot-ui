// app/static/js/ws/frame_parser.js
// Decodes incoming WS messages (JSON/msgpack/control frames) and forwards
// normalized frames to a handler.

import { decodeMessagePack } from "../utils/msgpack.mjs";

const DEFAULT_IGNORED_VENDOR_MESSAGES = new Set([
  "AddPartialTranscript",
  "AddTranscript",
]);

export function createFrameParser({
  hubLog,
  logStage,
  connection,         // createWsConnection(...), if needed
  handleMessageFrame, // function(frame) from ws_client.js
  handleErrorFrame,
  getNegotiatedControlCodec,
  getAudioPlayer = () => (typeof window !== "undefined" ? window.AudioPlayer : null),
  ignoredVendorMessages = DEFAULT_IGNORED_VENDOR_MESSAGES,
}) {
  function resolveControlCodec() {
    try {
      if (typeof getNegotiatedControlCodec === "function") {
        const codec = getNegotiatedControlCodec();
        if (codec === "msgpack") {
          return "msgpack";
        }
        if (codec === "json") {
          return "json";
        }
      }
    } catch {}
    try {
      if (connection && typeof connection.getNegotiatedControlCodec === "function") {
        const codec = connection.getNegotiatedControlCodec();
        if (codec === "msgpack") {
          return "msgpack";
        }
        if (codec === "json") {
          return "json";
        }
      }
    } catch {}
    return "json";
  }

  function tryDecodeMsgpackFrame(buffer) {
    try {
      const view = buffer instanceof ArrayBuffer
        ? new Uint8Array(buffer)
        : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
      const decoded = decodeMessagePack(view);
      if (decoded && typeof decoded === "object" && typeof decoded.type === "string") {
        return decoded;
      }
    } catch {}
    return null;
  }

  function normalizeIncomingFrame(frame) {
    if (!frame || typeof frame !== "object") {
      return null;
    }
    if (typeof frame.type === "string" && frame.type) {
      return frame;
    }
    let inferredType = null;
    if (typeof frame.kind === "string" && frame.kind) {
      inferredType = frame.kind;
    } else if (typeof frame.event === "string" && frame.event) {
      inferredType = frame.event;
    } else if (
      typeof frame.code === "string" ||
      typeof frame.detail === "string" ||
      typeof frame.message === "string"
    ) {
      inferredType = "error";
    }
    if (!inferredType) {
      return null;
    }
    return { ...frame, type: inferredType };
  }

  async function processControlFrameObject(frame) {
    const ignoredMessages = ignoredVendorMessages || DEFAULT_IGNORED_VENDOR_MESSAGES;
    if (frame && typeof frame.message === "string") {
      const normalizedType =
        (typeof frame.type === "string" && frame.type) ||
        (typeof frame.kind === "string" && frame.kind) ||
        (typeof frame.event === "string" && frame.event) ||
        null;
      if (normalizedType !== "chat.message") {
        if (ignoredMessages.has(frame.message)) {
          return null;
        }
      } else if (ignoredMessages.has(frame.message)) {
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "filtered" });
      }
    }
    const normalizedFrame = normalizeIncomingFrame(frame);
    if (!normalizedFrame) {
      console.warn("Dropping WS frame without recognizable type", frame);
      if (typeof handleErrorFrame === "function") {
        await handleErrorFrame({
          type: "error",
          code: typeof frame?.code === "string" ? frame.code : "schema_invalid",
          detail:
            typeof frame?.detail === "string"
              ? frame.detail
              : "Frame missing type field",
        });
      }
      return null;
    }
    if (normalizedFrame.type === "server.ping") {
      try {
        connection?.send?.({ type: "client.pong", ts: Date.now(), echo: normalizedFrame.ts });
      } catch {}
      return null;
    }
    await handleMessageFrame(normalizedFrame);
    return normalizedFrame;
  }

  async function parseMessageData(data) {
    if (typeof data === "string") {
      try {
        return JSON.parse(data);
      } catch (err) {
        console.error("Failed to parse WS frame", err, data);
        return null;
      }
    }
    if (typeof Blob !== "undefined" && data instanceof Blob) {
      if (resolveControlCodec() === "msgpack") {
        try {
          const buffer = await data.arrayBuffer();
          const frame = tryDecodeMsgpackFrame(buffer);
          if (frame) {
            return frame;
          }
        } catch (err) {
          console.warn("Failed to decode msgpack blob", err);
        }
      }
      const audioPlayer = getAudioPlayer?.();
      if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
        audioPlayer.enqueueChunk(data);
      }
      try {
        if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
          window.dispatchEvent(new CustomEvent("binary", { detail: data }));
        }
      } catch {}
      return null;
    }
    if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
      const chunk = data instanceof ArrayBuffer
        ? data
        : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      if (resolveControlCodec() === "msgpack") {
        const frame = tryDecodeMsgpackFrame(chunk);
        if (frame) {
          return frame;
        }
      }
      const audioPlayer = getAudioPlayer?.();
      if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
        audioPlayer.enqueueChunk(chunk);
      }
      try {
        if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
          window.dispatchEvent(new CustomEvent("binary", { detail: chunk }));
        }
      } catch {}
      return null;
    }
    console.warn("Unknown WS frame type", data);
    return null;
  }

  function handleRawMessageData(data) {
    try {
      const maybeFrame = parseMessageData(data);
      if (!maybeFrame) {
        return;
      }
      if (typeof maybeFrame.then === "function") {
        maybeFrame
          .then((resolved) => {
            if (!resolved) {
              return;
            }
            const maybeProcessed = processControlFrameObject(resolved);
            if (maybeProcessed && typeof maybeProcessed.then === "function") {
              maybeProcessed.catch((err) => {
                console.error("WS frame handler async failure", err);
              });
            }
          })
          .catch((err) => {
            console.error("WS frame handler async failure", err);
          });
        return;
      }
      const maybeProcessed = processControlFrameObject(maybeFrame);
      if (maybeProcessed && typeof maybeProcessed.then === "function") {
        maybeProcessed.catch((err) => {
          console.error("WS frame handler async failure", err);
        });
      }
    } catch (outerErr) {
      console.error("Uncaught exception in parseFrame", outerErr);
      try {
        hubLog?.("client.ws.parse_crash", { error: outerErr?.message, frame_data: data });
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
