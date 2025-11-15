// app/static/js/ws/transcript_bridge.js
// Encapsulates transcript delivery (ASR + chat history) and streaming UI state.

export function createTranscriptBridge({ AppState, hubLog, logStage, dispatchFrame }) {
  const pendingTranscriptFrames = [];
  const assistantStreamingTurns = new Map();
  const ASR_MATCH_WINDOW_MS = 4000;
  const lastUserBySid = new Map();
  let provisionalSidCounter = 0;

  function generateProvisionalSid() {
    provisionalSidCounter = (provisionalSidCounter + 1) % Number.MAX_SAFE_INTEGER;
    return `sid:${Date.now()}:${provisionalSidCounter}`;
  }

  function pruneStaleUserSids(now = Date.now()) {
    for (const [sid, record] of lastUserBySid.entries()) {
      if (!record || typeof record.ts !== "number" || (now - record.ts) > ASR_MATCH_WINDOW_MS) {
        lastUserBySid.delete(sid);
      }
    }
  }

  function deliverAsr(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    if (frame.type === "asr.final" && typeof frame.sid === "string" && frame.sid) {
      if (AppState.asrSid && AppState.asrSid !== frame.sid) {
        console.warn("asr.final sid mismatch", { expected: AppState.asrSid, sid: frame.sid });
      } else {
        AppState.asrSid = frame.sid;
      }
    }
    const view = window.TranscriptView;
    const now = Date.now();
    if (frame.type === "asr.final" && typeof frame.text === "string" && frame.text) {
      const sid = (typeof frame.sid === "string" && frame.sid) || generateProvisionalSid();
      lastUserBySid.set(sid, { text: frame.text, ts: now });
      pruneStaleUserSids(now);
      if (view && typeof view.upsertUser === "function") {
        try {
          view.upsertUser({ key: sid, text: frame.text, provisional: true });
        } catch (err) {
          console.warn("TranscriptView upsertUser error", err);
        }
      }
    } else {
      pruneStaleUserSids(now);
    }
    if (!view) {
      return;
    }
    try {
      if (frame.type === "asr.partial" && typeof view.handlePartial === "function") {
        view.handlePartial(frame);
      } else if (frame.type === "asr.final" && typeof view.handleFinal === "function") {
        view.handleFinal(frame);
      }
    } catch (err) {
      console.warn("TranscriptView ASR handler error", err);
    }
  }

  function deliverChat(frame) {
    if (!frame || typeof frame !== "object") {
      console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_frame" });
      return;
    }
    const view = window.TranscriptView;
    pruneStaleUserSids();
    if (!view || typeof view.handleChatMessage !== "function") {
      queueForTranscript(frame);
      return;
    }

    const turnId = typeof frame.turn_id === "string" ? frame.turn_id : null;
    if (
      turnId &&
      frame.role === "assistant" &&
      assistantStreamingTurns.has(turnId) &&
      typeof view.commitAssistantStreaming === "function"
    ) {
      const record = assistantStreamingTurns.get(turnId) || {};
      const finalText = typeof frame.text === "string" ? frame.text : record.text || "";
      try {
        view.commitAssistantStreaming(turnId, {
          text: finalText,
          messageId: typeof frame.id === "string" ? frame.id : null,
          reqId: typeof frame.req_id === "string" ? frame.req_id : record.reqId || null,
          final: true,
        });
      } catch (err) {
        console.warn("TranscriptView final commit error", err);
      }
      assistantStreamingTurns.delete(turnId);
      return;
    }

    const isUserChat =
      frame.type === "chat.message" &&
      frame.role === "user" &&
      typeof frame.text === "string" &&
      frame.text;

    if (isUserChat) {
      const sid =
        (typeof frame.sid === "string" && frame.sid) ||
        findNearestSid(frame.text);
      if (sid && lastUserBySid.has(sid) && typeof view.upsertUser === "function") {
        try {
          view.upsertUser({ key: sid, text: frame.text, provisional: false });
          lastUserBySid.delete(sid);
          return;
        } catch (err) {
          console.warn("TranscriptView upsertUser error", err);
        }
      }
    }

    try {
      view.handleChatMessage(frame);
    } catch (err) {
      console.warn("TranscriptView chat handler error", err);
    }
  }

  function handleChatHistoryFrame(frame) {
    const messages = Array.isArray(frame.messages) ? frame.messages : [];
    if (!messages.length) {
      return;
    }
    for (const message of messages) {
      deliverChat(message);
    }
  }

  function queueForTranscript(frame) {
    pendingTranscriptFrames.push(frame);
  }

  function transcriptFrameAllowed(frame) {
    const type = typeof frame?.type === "string" ? frame.type : "";
    const role = typeof frame?.role === "string" ? frame.role : "";

    // For now, do NOT filter anything at the transcript layer.
    // We want all ASR + chat frames to be eligible for display.
    const allow = true;

    try {
      console.log(`evt=ui_transcript_filter allow=${allow} type=${type || ""} role=${role || ""}`);
    } catch {}

    return allow;
  }

  function handleAssistantStreamingBegin(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    let record = assistantStreamingTurns.get(turnId);
    if (!record) {
      record = { text: "", totalLen: 0, committed: false, ended: false, reqId: null };
      assistantStreamingTurns.set(turnId, record);
    }
    if (typeof frame?.req_id === "string" && frame.req_id) {
      record.reqId = frame.req_id;
    }
    const view = window.TranscriptView;
    if (view && typeof view.beginAssistantStreaming === "function") {
      try {
        view.beginAssistantStreaming({ turnId, reqId: record.reqId || null });
      } catch (err) {
        console.warn("TranscriptView beginAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingDelta(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    if (!assistantStreamingTurns.has(turnId)) {
      handleAssistantStreamingBegin(frame);
    }
    const record = assistantStreamingTurns.get(turnId);
    if (!record) return;
    const append = typeof frame?.append === "string" ? frame.append : "";
    if (typeof frame?.req_id === "string" && frame.req_id) {
      record.reqId = frame.req_id;
    }
    if (append) {
      record.text = `${record.text || ""}${append}`;
    }
    if (typeof frame?.total_len === "number") {
      record.totalLen = frame.total_len;
    } else if (append) {
      record.totalLen = (record.totalLen || 0) + append.length;
    }
    const view = window.TranscriptView;
    if (append && view && typeof view.appendAssistantStreaming === "function") {
      try {
        view.appendAssistantStreaming(turnId, append);
      } catch (err) {
        console.warn("TranscriptView appendAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingCommit(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    if (!assistantStreamingTurns.has(turnId)) {
      handleAssistantStreamingBegin(frame);
    }
    const record = assistantStreamingTurns.get(turnId);
    if (!record) return;
    if (typeof frame?.total_len === "number") {
      record.totalLen = frame.total_len;
    }
    if (typeof frame?.text === "string") {
      record.text = frame.text;
    }
    record.committed = true;
    const view = window.TranscriptView;
    if (view && typeof view.commitAssistantStreaming === "function") {
      try {
        view.commitAssistantStreaming(turnId, {
          text: record.text,
          reqId: record.reqId || null,
        });
      } catch (err) {
        console.warn("TranscriptView commitAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingEnd(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    const record = assistantStreamingTurns.get(turnId);
    if (!record) {
      return;
    }
    record.ended = true;
    const view = window.TranscriptView;
    if (view && typeof view.commitAssistantStreaming === "function") {
      try {
        view.commitAssistantStreaming(turnId, {
          text: record.text,
          reqId: record.reqId || null,
        });
      } catch (err) {
        console.warn("TranscriptView commitAssistantStreaming error", err);
      }
    }
  }

  function findNearestSid(text) {
    if (typeof text !== "string" || !text) {
      return null;
    }
    const now = Date.now();
    for (const [sid, record] of lastUserBySid.entries()) {
      if (record && record.text === text && (now - record.ts) < ASR_MATCH_WINDOW_MS) {
        return sid;
      }
    }
    return null;
  }

  function attachTranscriptView(view) {
    if (!view || typeof view.handleChatMessage !== "function") {
      if (pendingTranscriptFrames.length) {
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_transcript_view" });
      }
      return;
    }
    while (pendingTranscriptFrames.length) {
      const frame = pendingTranscriptFrames.shift();
      try {
        deliverChat(frame);
      } catch (err) {
        console.warn("flush chat error", err);
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "transcript_flush_error" });
      }
    }
  }

  return {
    deliverAsr,
    deliverChat,
    handleChatHistoryFrame,
    transcriptFrameAllowed,
    attachTranscriptView,
    handleAssistantStreamingBegin,
    handleAssistantStreamingDelta,
    handleAssistantStreamingCommit,
    handleAssistantStreamingEnd,
  };
}
