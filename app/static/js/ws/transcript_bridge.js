// app/static/js/ws/transcript_bridge.js
// Encapsulates transcript delivery (ASR + chat history) and streaming UI state.

export function createTranscriptBridge({ AppState, hubLog, logStage, dispatchFrame }) {
  const pendingTranscriptFrames = [];
  const assistantStreamingTurns = new Map();
  const committedAssistantByReqId = new Map();
  const ASR_MATCH_WINDOW_MS = 4000;
  const lastUserBySid = new Map();
  // Vendor ASR finals can duplicate; keep a separate dedupe key from user.turn so we do not emit twice.
  let lastAsrFinalKey = null;
  // user.turn is the canonical user bubble source; guard against back-to-back duplicates from the server.
  let lastUserTurnKey = null;
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
      const reqId = typeof frame.req_id === "string" ? frame.req_id : typeof frame.reqId === "string" ? frame.reqId : null;
      const turnId = typeof frame.turn_id === "string" ? frame.turn_id : null;
      const key = `${reqId || turnId || ""}|${frame.text}`;
      if (lastAsrFinalKey === key) {
        return;
      }
      lastAsrFinalKey = key;
    } else {
      pruneStaleUserSids(now);
    }
    if (!view) {
      return;
    }
    try {
      if (frame.type === "asr.partial" && typeof view.handlePartial === "function") {
        view.handlePartial(frame);
      }
    } catch (err) {
      console.warn("TranscriptView ASR handler error", err);
    }
  }

  function deliverUserTurn(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    const text = typeof frame.text === "string" ? frame.text : "";
    if (!text.trim()) {
      try { logStage("ui_transcript_filter", { allow: false, type: "user.turn", reason: "empty_text" }); } catch {}
      return;
    }
    const reqId = typeof frame.req_id === "string" ? frame.req_id : typeof frame.reqId === "string" ? frame.reqId : null;
    const turnId = typeof frame.turn_id === "string" && frame.turn_id
      ? frame.turn_id
      : reqId;
    // Deduplicate user.turn frames defensively. Vendor finals can duplicate (server dedupes),
    // and the client also guards against back-to-back identical user.turn frames.
    const key = `${reqId || ""}|${text}`;
    if (lastUserTurnKey === key) {
      return;
    }
    lastUserTurnKey = key;

    const chatFrame = {
      type: "chat.message",
      role: "user",
      text,
      req_id: reqId || undefined,
      turn_id: turnId || undefined,
      turn_index: typeof frame.turn_index === "number" ? frame.turn_index : undefined,
      ts: typeof frame.ts === "number" ? frame.ts : undefined,
    };

    if (transcriptFrameAllowed(chatFrame)) {
      deliverChat(chatFrame);
    } else {
      logStage("ui_transcript_filter", { allow: false, type: "user.turn", role: "user" });
    }
  }

  function deliverChat(frame) {
    if (!frame || typeof frame !== "object") {
      console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_frame" });
      return;
    }

    // Normalize and patch up the frame so downstream consumers can rely on text/type.
    try {
      frame.type = normalizeChatType(frame.type);
    } catch {}
    try {
      const text = extractTextFromChatFrame(frame);
      if (text && typeof frame.text !== "string") {
        frame.text = text;
      }
    } catch {}

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

  // Always allow transcriptable frames; filtering here caused drops during refactor.
  function transcriptFrameAllowed(frame) {
    const type = typeof frame?.type === "string" ? frame.type : "";
    const role = typeof frame?.role === "string" ? frame.role : "";
    const allow = true;
    try { logStage("ui_transcript_filter", { allow, type: type || "", role: role || "" }); } catch {}
    return allow;
  }

  // Extract visible text from various frame shapes (message, streaming, or content arrays).
  function extractTextFromChatFrame(frame) {
    if (!frame || typeof frame !== "object") return "";
    if (typeof frame.text === "string") return frame.text;
    if (typeof frame.delta === "string") return frame.delta;
    const c = frame.content;
    if (Array.isArray(c)) {
      const parts = [];
      for (const seg of c) {
        if (!seg) continue;
        if (typeof seg === "string") {
          parts.push(seg);
          continue;
        }
        if (typeof seg.text === "string") {
          parts.push(seg.text);
          continue;
        }
        if (typeof seg.delta === "string") {
          parts.push(seg.delta);
          continue;
        }
      }
      return parts.join("");
    }
    return "";
  }

  // Normalize type aliases so routing is stable.
  function normalizeChatType(t) {
    if (!t) return "";
    if (t === "message") return "chat.message";
    if (t === "history") return "chat.history";
    if (t === "begin") return "chat.begin";
    if (t === "delta") return "chat.delta";
    if (t === "commit") return "chat.commit";
    if (t === "end") return "chat.end";
    return t;
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

    const reqId = typeof frame?.req_id === "string" ? frame.req_id : record.reqId || null;
    if (reqId) {
      committedAssistantByReqId.set(reqId, {
        turnId,
        messageId: typeof frame?.id === "string" ? frame.id : null,
      });
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
        console.warn("chat.message dropped", {
          phase: AppState?.wsPhase,
          reason: "invalid_transcript_view",
        });
      }
      return;
    }

    // ✅ Bind the view globally so deliverChat/deliverAsr can see it
    try {
      window.TranscriptView = view;
    } catch (err) {
      console.warn("Failed to bind TranscriptView on window", err);
    }

    view.hasCommittedAssistantForReqId = (reqId) => committedAssistantByReqId.has(reqId);

    // Flush any frames that arrived before the view was attached
    while (pendingTranscriptFrames.length) {
      const frame = pendingTranscriptFrames.shift();
      try {
        deliverChat(frame);
      } catch (err) {
        console.warn("flush chat error", err);
        console.warn("chat.message dropped", {
          phase: AppState?.wsPhase,
          reason: "transcript_flush_error",
        });
      }
    }
  }

  return {
    deliverAsr,
    deliverUserTurn,
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
