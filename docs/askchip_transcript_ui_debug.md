# AskChip Transcript UI Debugging Guide

This document maps how incoming WebSocket frames become visible user bubbles in the AskChip front-end. It focuses on the transcript/chat UI and complements the transport/server flow in `docs/askchip_voice_turns_debug.md`.

## 1. High-Level UI Event Flow

1. `ws_client.js` receives frames and dispatches them to the transcript bridge.
2. `user.turn` frames flow through `createTranscriptBridge` to `deliverUserTurn`.
3. `deliverUserTurn` synthesizes a `chat.message` with `role: "user"` and forwards it via `deliverChat`.
4. `TranscriptView` renders chat messages (and optional partial ASR text) in the DOM.

## 2. WS Client → Transcript Bridge Wiring

### 2.1 `ws_client.js`: frame dispatch and `user.turn` routing

`ws_client.js` wires the transcript bridge and resolves `deliverUserTurn` (bridge implementation first, fallback otherwise):

```javascript
const transcriptBridge = createTranscriptBridge({
  AppState,
  hubLog: logStage,
  logStage,
  dispatchFrame,
});

const {
  deliverAsr,
  deliverChat,
  handleChatHistoryFrame,
  transcriptFrameAllowed,
  attachTranscriptView,
  handleAssistantStreamingBegin,
  handleAssistantStreamingDelta,
  handleAssistantStreamingCommit,
  handleAssistantStreamingEnd,
} = transcriptBridge || {};

let deliverUserTurn = null;

if (transcriptBridge && typeof transcriptBridge.deliverUserTurn === "function") {
  // Normal path: use the bridge implementation.
  deliverUserTurn = (frame) => transcriptBridge.deliverUserTurn(frame);
} else {
  // Fallback: synthesize a chat.message so the user still sees their bubble.
  deliverUserTurn = (frame) => {
    if (!frame || typeof frame !== "object") return;
    const text = typeof frame.text === "string" ? frame.text : "";
    if (!text.trim()) return;

    const chatFrame = {
      type: "chat.message",
      role: "user",
      text,
      req_id: typeof frame.req_id === "string" ? frame.req_id : undefined,
      turn_id: typeof frame.turn_id === "string" ? frame.turn_id : undefined,
      turn_index: typeof frame.turn_index === "number" ? frame.turn_index : undefined,
      ts: typeof frame.ts === "number" ? frame.ts : undefined,
    };

    if (transcriptFrameAllowed && transcriptFrameAllowed(chatFrame)) {
      try {
        if (typeof deliverChat === "function") {
          deliverChat(chatFrame);
        }
      } catch (err) {
        console.warn("user.turn fallback deliverChat error", err);
      }
    }
  };
}
```

Incoming `user.turn` frames are routed into this resolver inside `handleMessageFrame`:

```javascript
case "user.turn":
  try { deliverUserTurn && deliverUserTurn(frame); } catch (e) { console.warn("user.turn err", e); }
  handledByTranscriptDispatch = true;
  break;
```

### 2.2 `ws_client.js`: `asr.partial` / `asr.final` handling

ASR frames are forwarded to `deliverAsr` only when `transcriptFrameAllowed` allows them:

```javascript
case "asr.partial":
  schedulePartialWatchdog("asr.partial");
  if (transcriptFrameAllowed(frame)) {
    deliverAsr(frame);
  } else {
    logStage("ui_transcript_filter", { allow: false, type: frame.type });
  }
  handledByTranscriptDispatch = true;
  break;

case "asr.final":
  clearPartialWatchdog();
  // logStage("client.asr", ...)
  if (transcriptFrameAllowed(frame)) {
    deliverAsr(frame);
  } else {
    logStage("ui_transcript_filter", { allow: false, type: frame.type });
  }
  handledByTranscriptDispatch = true;
  break;
```

`deliverAsr(frame)` is used for ASR feedback (partials/finals). It is not intended to create durable user chat bubbles; the canonical durable path is `user.turn` → `chat.message`.

## 3. Transcript Bridge: `deliverAsr` / `deliverUserTurn` / `deliverChat`

`app/static/js/ws/transcript_bridge.js` holds the bridge that feeds the TranscriptView.

### 3.1 `deliverAsr(frame)`

```javascript
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
```

* `deliverAsr` only calls `view.handlePartial` for `asr.partial` frames. It does **not** call `handleFinal` or create chat messages. Deduping (`lastAsrFinalKey`) prevents processing identical ASR finals but does not render them directly.

### 3.2 `deliverUserTurn(frame)`

```javascript
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
```

This is the canonical path that turns `user.turn` frames into `chat.message` events with `role: "user"`.

### 3.3 `deliverChat(frame)` and `transcriptFrameAllowed(frame)`

```javascript
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
  // ... assistant streaming handling omitted for brevity ...

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
```

`deliverChat` normalizes frames and forwards them to `TranscriptView.handleChatMessage`. User chat frames may be merged into an existing provisional ASR bubble via `upsertUser` (matching SID/text). Otherwise they render as standard chat messages.

`transcriptFrameAllowed` currently allows all transcriptable frames but logs user turns/messages:

```javascript
function transcriptFrameAllowed(frame) {
  const type = typeof frame?.type === "string" ? frame.type : "";
  const role = typeof frame?.role === "string" ? frame.role : "";
  const allow = true;
  const shouldLog = allow === false || type === "user.turn" || type === "chat.message";
  if (shouldLog) {
    try { logStage("ui_transcript_filter", { allow, type: type || "", role: role || "" }); } catch {}
  }
  return allow;
}
```

## 4. Transcript / Chat UI Components

### 4.1 `TranscriptView` rendering

`app/static/js/transcript_view.js` exposes the methods consumed by the bridge. Key handlers include:

*Partial and final ASR display (front-end only; not durable chat messages):*

```javascript
function handlePartial(frame) {
  if (!frame || typeof frame.text !== "string") return;
  const text = frame.text;
  const reqId = typeof frame.req_id === "string" ? frame.req_id : null;
  if (reqId && lastPartialReqId && reqId !== lastPartialReqId) {
    lastPartialSeq = null;
  }
  if (reqId) {
    lastPartialReqId = reqId;
  }
  const seqCandidate = Number(frame.partial_seq);
  if (Number.isFinite(seqCandidate)) {
    if (lastPartialSeq !== null && seqCandidate < lastPartialSeq) {
      return;
    }
    lastPartialSeq = seqCandidate;
  }
  pendingPartial = { text };
  schedulePartialRender();
}

function handleFinal(frame) {
  if (!frame || typeof frame.text !== "string") {
    clearPartial({ removeNode: true });
    return;
  }
  const text = frame.text;
  const reqId = typeof frame.req_id === "string" ? frame.req_id : null;
  cancelPartialTimer();
  pendingPartial = null;
  if (
    reqId &&
    lastPartialReqId &&
    reqId !== lastPartialReqId &&
    partialNode &&
    partialNode.isConnected
  ) {
    partialNode.remove();
    partialNode = null;
  }
  let node = null;
  if (
    partialNode &&
    partialNode.isConnected &&
    (!reqId || !lastPartialReqId || reqId === lastPartialReqId)
  ) {
    node = partialNode;
  }
  if (!node) {
    node = createMessage("user", "");
    container.appendChild(node);
  }
  node.classList.remove("partial");
  delete node.dataset.partial;
  if (reqId) {
    node.dataset.reqId = reqId;
    messageNodesByReqId.set(reqId, { node, role: "user" });
  } else {
    delete node.dataset.reqId;
  }
  setNodeText(node, text);
  setNodeRole(node, "user");
  updateMeta(node, "user", { partial: false });
  scrollToBottom();
  partialNode = null;
  lastPartialSeq = null;
  lastPartialReqId = null;
  lastPartialRenderAt = Date.now();
}
```

`deliverAsr` currently calls only `handlePartial`; `handleFinal` exists but is not invoked by the bridge.

*Durable chat rendering (used by `deliverChat` and chat history replay):*

```javascript
function handleChatMessage(frame) {
  if (!frame || typeof frame !== "object") return;
  const text = typeof frame.text === "string" ? frame.text.trim() : "";
  if (!text) return;
  let role = "assistant";
  if (frame.role === "user") {
    role = "user";
  } else if (frame.role === "system") {
    role = "system";
  }
  const messageId = typeof frame.id === "string" ? frame.id : null;
  const clientMsgId = typeof frame.client_msg_id === "string" ? frame.client_msg_id : null;
  const reqId = typeof frame.req_id === "string" ? frame.req_id : null;

  if (role === "assistant" && typeof view.hasCommittedAssistantForReqId === "function" && reqId) {
    if (view.hasCommittedAssistantForReqId(reqId)) {
      return;
    }
  }

  let node = null;

  if (messageId && messageNodesById.has(messageId)) {
    node = messageNodesById.get(messageId);
  }
  if (!node && clientMsgId && messageNodesByClientId.has(clientMsgId)) {
    node = messageNodesByClientId.get(clientMsgId);
  }
  if (!node && role === "user" && reqId && messageNodesByReqId.has(reqId)) {
    const entry = messageNodesByReqId.get(reqId);
    if (entry && entry.node) {
      if (!entry.role || entry.role === role) {
        node = entry.node;
      }
    }
  }

  if (!node) {
    node = createMessage(role, text);
    container.appendChild(node);
  } else {
    setNodeRole(node, role);
  }

  node.classList.remove("partial");
  delete node.dataset.partial;
  setNodeText(node, text);
  updateMeta(node, role, { partial: false });

  if (messageId) {
    messageNodesById.set(messageId, node);
    node.dataset.messageId = messageId;
  } else {
    delete node.dataset.messageId;
  }

  if (clientMsgId) {
    messageNodesByClientId.set(clientMsgId, node);
    node.dataset.clientMsgId = clientMsgId;
  } else {
    delete node.dataset.clientMsgId;
  }

  if (reqId && role === "user") {
    messageNodesByReqId.set(reqId, { node, role });
    node.dataset.reqId = reqId;
  } else if (reqId) {
    node.dataset.reqId = reqId;
  } else {
    delete node.dataset.reqId;
  }

  scrollToBottom();
}
```

*Local form submission path:* sending `chat.user` also renders a pending user bubble locally via `addUserMessage` before the server echoes it back:

```javascript
function addUserMessage(text, { clientMsgId = null, pending = false } = {}) {
  const normalized = typeof text === "string" ? text.trim() : "";
  if (!normalized) return null;
  const node = createMessage("user", normalized, { partial: false });
  if (pending) {
    updateMeta(node, "user", { pending: true });
  }
  if (clientMsgId) {
    node.dataset.clientMsgId = clientMsgId;
    messageNodesByClientId.set(clientMsgId, node);
  }
  container.appendChild(node);
  scrollToBottom();
  return node;
}

async function handleSubmit(ev) {
  ev.preventDefault();
  // ... stopRecorder/inputStop ...
  addUserMessage(text, { clientMsgId, pending: true });
  const payload = { type: "chat.user", role: "user", text, client_msg_id: clientMsgId };
  // send via WSClient
}
```

### 4.2 Other listeners / handlers that touch ASR or chat

*Global ASR listeners in `app/static/js/app.js` do not create chat bubbles, but mark processing state:*

```javascript
window.addEventListener('asr.partial', (event) => {
  const frame = event && event.detail;
  const partialText = typeof frame?.text === 'string'
    ? frame.text
    : (typeof frame?.partial === 'string' ? frame.partial : '');
  try { markTurnPartial(partialText); } catch {}
});

window.addEventListener('asr.final', () => {
  try {
    window.AppState.processing = true;
    window.AppUI?.refresh?.();
  } catch {}
});
```

*Other transcript listeners:* `TranscriptView` only binds form submit and `ws.close` cleanup; there are no `asr.final` listeners on the view itself.

## 5. Deduplication and Filters

* Bridge-level dedupe:
  * `lastAsrFinalKey` prevents processing identical `asr.final` texts. This dedupe only affects ASR display and SID matching, not chat rendering.
  * `lastUserTurnKey` prevents back-to-back identical `user.turn` frames from producing duplicate `chat.message` events.
  * `lastUserBySid` + `findNearestSid` allow `deliverChat` to merge a `chat.message` with a provisional ASR node via `upsertUser`.

* UI-level dedupe:
  * `handleChatMessage` reuses existing nodes keyed by `message_id`, `client_msg_id`, or `req_id` (for user role). This prevents duplicates when the same message arrives multiple ways (history replay, echoes, etc.).
  * Pending local `addUserMessage` nodes are matched by `client_msg_id` when the server echoes `chat.message`.

* Filters:
  * `transcriptFrameAllowed` currently returns `true` for all frames but logs `ui_transcript_filter` events for `user.turn` and `chat.message` types for diagnostics.

## 6. Final Summary: All Paths That Can Create a User Bubble

* **Expected:** `user.turn` → `deliverUserTurn` → synthesized `chat.message` (`role: "user"`) → `deliverChat` → `TranscriptView.handleChatMessage` renders a durable user bubble.
* **Expected:** Chat history replay (`chat.history` frames) → `handleChatHistoryFrame` → `deliverChat` → `handleChatMessage` (renders past user bubbles).
* **Expected:** Local text submit → `TranscriptView.handleSubmit` → `addUserMessage` (pending bubble) → server echo as `chat.message` → `handleChatMessage` (updates same node via `client_msg_id`).
* **Expected (ASR merge path):** `asr.final` stored in `lastUserBySid`; when later `chat.message` for the user arrives, `deliverChat` may call `view.upsertUser` to replace the provisional ASR bubble instead of adding a new one.
* **Legacy/suspect:** `TranscriptView.handleFinal` can render a user bubble directly from ASR finals, but `deliverAsr` never calls it; if any other code dispatches `asr.final` to `handleFinal`, it could create a bubble. Current wiring does **not** invoke this path.
* **No other durable path found:** There are no additional listeners (`asr.final`/`asr`) that construct `{ role: "user" }` messages or call `handleChatMessage` directly beyond the paths above.
