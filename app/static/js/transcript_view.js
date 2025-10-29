(() => {
  const view = {
    handlePartial: () => {},
    handleFinal: () => {},
    handleChatMessage: () => {},
    addUserMessage: () => {},
    reset: () => {},
    showSystemFromChip: () => {}
  };

  function init() {
    const container = document.getElementById("messages");
    if (!container) {
      console.warn("TranscriptView: messages container not found");
      return;
    }
    const form = document.getElementById("textChatForm");
    const input = document.getElementById("textChatInput");

    const PARTIAL_THROTTLE_MS = 50;

    let partialNode = null;
    let pendingPartial = null;
    let partialTimer = null;
    let lastPartialSeq = null;
    let lastPartialReqId = null;
    let lastPartialRenderAt = 0;
    let bargeInEnabled = true;
    const messageNodesById = new Map();
    const messageNodesByClientId = new Map();
    const messageNodesByReqId = new Map();

    function setNodeRole(node, role) {
      if (!node) return;
      const wrapper = node;
      if (!(wrapper instanceof HTMLElement)) return;
      wrapper.dataset.role = role;
      wrapper.classList.remove("assistant", "user", "system");
      if (role === "assistant" || role === "user" || role === "system") {
        wrapper.classList.add(role);
      }
    }

    function scrollToBottom() {
      try {
        container.scrollTop = container.scrollHeight;
      } catch (err) {
        console.warn("TranscriptView: failed to scroll", err);
      }
    }

    function ensureParagraph(node) {
      if (!node) return null;
      const bubble = node.querySelector(".bubble");
      if (!bubble) return null;
      let paragraph = bubble.querySelector("p");
      if (!paragraph) {
        paragraph = document.createElement("p");
        bubble.appendChild(paragraph);
      }
      return paragraph;
    }

    function setNodeText(node, text) {
      const paragraph = ensureParagraph(node);
      if (!paragraph) return;
      const safe = typeof text === "string" && text.length ? text : "\u00a0";
      paragraph.textContent = safe;
    }

    function updateMeta(node, role, { partial = false, pending = false } = {}) {
      const meta = node.querySelector(".meta");
      if (!meta) return;
      if (role === "user") {
        if (partial) {
          meta.textContent = "you · speaking…";
        } else if (pending) {
          meta.textContent = "you · sending…";
        } else {
          meta.textContent = "you · just now";
        }
      } else if (role === "system") {
        meta.textContent = "Chip · just now";
      } else if (partial) {
        meta.textContent = "assistant · transcribing…";
      } else {
        meta.textContent = "assistant · just now";
      }
    }

    function createMessage(role, text, { partial = false } = {}) {
      const wrapper = document.createElement("div");
      wrapper.className = `msg ${role}${partial ? " partial" : ""}`;
      if (partial) {
        wrapper.dataset.partial = "true";
      }
      setNodeRole(wrapper, role);
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const paragraph = document.createElement("p");
      bubble.appendChild(paragraph);
      wrapper.appendChild(bubble);
      const meta = document.createElement("div");
      meta.className = "meta";
      wrapper.appendChild(meta);
      setNodeText(wrapper, text);
      updateMeta(wrapper, role, { partial });
      return wrapper;
    }

    function ensurePartialNode() {
      if (partialNode && partialNode.isConnected) {
        return partialNode;
      }
      partialNode = createMessage("user", "", { partial: true });
      container.appendChild(partialNode);
      return partialNode;
    }

    function cancelPartialTimer() {
      if (!partialTimer) return;
      clearTimeout(partialTimer);
      partialTimer = null;
    }

    function flushPartial(text) {
      const node = ensurePartialNode();
      node.classList.add("partial");
      node.dataset.partial = "true";
      if (lastPartialReqId) {
        node.dataset.reqId = lastPartialReqId;
        messageNodesByReqId.set(lastPartialReqId, { node, role: "user" });
      } else {
        delete node.dataset.reqId;
      }
      setNodeText(node, text);
      setNodeRole(node, "user");
      updateMeta(node, "user", { partial: true });
      lastPartialRenderAt = Date.now();
      scrollToBottom();
    }

    function schedulePartialRender() {
      if (!pendingPartial) return;
      const now = Date.now();
      const elapsed = now - lastPartialRenderAt;
      if (elapsed >= PARTIAL_THROTTLE_MS) {
        const text = pendingPartial.text;
        pendingPartial = null;
        flushPartial(text);
        return;
      }
      if (partialTimer) return;
      const wait = Math.max(0, PARTIAL_THROTTLE_MS - elapsed);
      partialTimer = setTimeout(() => {
        partialTimer = null;
        if (!pendingPartial) return;
        const text = pendingPartial.text;
        pendingPartial = null;
        flushPartial(text);
        if (pendingPartial) {
          schedulePartialRender();
        }
      }, wait);
    }

    function clearPartial({ removeNode = false } = {}) {
      cancelPartialTimer();
      pendingPartial = null;
      if (removeNode && lastPartialReqId && messageNodesByReqId.has(lastPartialReqId)) {
        const entry = messageNodesByReqId.get(lastPartialReqId);
        if (entry && entry.node === partialNode) {
          messageNodesByReqId.delete(lastPartialReqId);
        }
      }
      lastPartialSeq = null;
      lastPartialReqId = null;
      lastPartialRenderAt = 0;
      if (removeNode && partialNode && partialNode.isConnected) {
        partialNode.remove();
      }
      if (removeNode) {
        partialNode = null;
      }
    }

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

    function generateClientMsgId() {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        try {
          return crypto.randomUUID();
        } catch (err) {
          console.warn("TranscriptView: crypto.randomUUID failed", err);
        }
      }
      const random = Math.random().toString(36).slice(2, 10);
      return `client-${Date.now().toString(36)}-${random}`;
    }

    function sendChatPayload(text, clientMsgId) {
      const payload = { type: "chat.user", text };
      if (clientMsgId) {
        payload.client_msg_id = clientMsgId;
      }
      const WSClient = window.WSClient;
      if (WSClient && typeof WSClient.send === "function") {
        try {
          WSClient.send(payload);
        } catch (err) {
          console.error("TranscriptView: failed to send chat.user", err);
        }
      } else {
        console.warn("TranscriptView: WSClient unavailable for chat.user");
      }
    }

    function maybeInterruptForBarge() {
      const AppState = window.AppState;
      if (!AppState || typeof AppState.getState !== "function") return;
      const state = AppState.getState();
      const speaking = !!(state && state.ttsUttId);
      if (!speaking || !bargeInEnabled) return;
      const audioPlayer = window.AudioPlayer;
      if (audioPlayer && typeof audioPlayer.interrupt === "function") {
        audioPlayer.interrupt();
      }
      if (typeof AppState.setState === "function") {
        AppState.setState({ engineMode: "Listening" });
      }
      try {
        window.dispatchEvent(
          new CustomEvent("engine.mode", { detail: { mode: "Listening", source: "text_barge" } })
        );
      } catch (err) {
        console.warn("TranscriptView: failed to emit engine.mode", err);
      }
    }

    function handleSubmit(event) {
      event.preventDefault();
      if (!input) return;
      const raw = input.value || "";
      const trimmed = raw.trim();
      if (!trimmed) return;
      input.value = "";
      const clientMsgId = generateClientMsgId();
      addUserMessage(trimmed, { clientMsgId, pending: true });
      sendChatPayload(trimmed, clientMsgId);
      maybeInterruptForBarge();
      input.focus();
    }

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

    function showSystemFromChip(text) {
      const message = typeof text === "string" ? text.trim() : "";
      if (!message) {
        return;
      }
      const node = createMessage("system", message);
      node.classList.add("chip-system");
      node.dataset.from = "Chip";
      container.appendChild(node);
      scrollToBottom();
    }

    function handlePolicyInteraction(event) {
      const detail = event && event.detail;
      const policy = detail && detail.policy;
      if (policy && typeof policy.barge_in_enabled === "boolean") {
        bargeInEnabled = policy.barge_in_enabled;
      }
    }

    if (form && input) {
      form.addEventListener("submit", handleSubmit);
    }

    window.addEventListener("policy.interaction", handlePolicyInteraction);
    window.addEventListener("ws.close", () => {
      bargeInEnabled = true;
      clearPartial({ removeNode: true });
    });

    view.handlePartial = handlePartial;
    view.handleFinal = handleFinal;
    view.handleChatMessage = handleChatMessage;
    view.addUserMessage = addUserMessage;
    view.reset = () => clearPartial({ removeNode: true });
    view.showSystemFromChip = showSystemFromChip;
  }

  window.TranscriptView = view;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
