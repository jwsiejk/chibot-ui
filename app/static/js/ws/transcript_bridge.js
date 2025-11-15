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
    // real implementation will be moved from ws_client.js
  }

  function deliverChat(frame) {
    // real implementation will be moved from ws_client.js
  }

  function handleChatHistoryFrame(frame) {
    // real implementation will be moved from ws_client.js
  }

  function queueForTranscript(frame) {
    pendingTranscriptFrames.push(frame);
  }

  function transcriptFrameAllowed(frame) {
    // real implementation will be moved from ws_client.js
    return true;
  }

  function handleAssistantStreamingBegin(frame) {
    // real implementation will be moved from ws_client.js
  }

  function handleAssistantStreamingDelta(frame) {
    // real implementation will be moved from ws_client.js
  }

  function handleAssistantStreamingCommit(frame) {
    // real implementation will be moved from ws_client.js
  }

  function handleAssistantStreamingEnd(frame) {
    // real implementation will be moved from ws_client.js
  }

  function findNearestSid(text) {
    // real implementation will be moved from ws_client.js
    return null;
  }

  function attachTranscriptView(view) {
    // real implementation will be moved from ws_client.js
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
