(() => {
  const view = {
    handlePartial: () => {},
    handleFinal: () => {},
    addUserMessage: () => {},
    reset: () => {}
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

    function updateMeta(node, role, { partial = false } = {}) {
      const meta = node.querySelector(".meta");
      if (!meta) return;
      if (role === "user") {
        meta.textContent = "you · now";
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
      partialNode = createMessage("assistant", "", { partial: true });
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
      } else {
        delete node.dataset.reqId;
      }
      setNodeText(node, text);
      updateMeta(node, "assistant", { partial: true });
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
        node = createMessage("assistant", "");
        container.appendChild(node);
      }
      node.classList.remove("partial");
      delete node.dataset.partial;
      delete node.dataset.reqId;
      setNodeText(node, text);
      updateMeta(node, "assistant", { partial: false });
      scrollToBottom();
      partialNode = null;
      lastPartialSeq = null;
      lastPartialReqId = null;
      lastPartialRenderAt = Date.now();
    }

    function addUserMessage(text) {
      const normalized = typeof text === "string" ? text.trim() : "";
      if (!normalized) return;
      const node = createMessage("user", normalized);
      container.appendChild(node);
      scrollToBottom();
    }

    function sendChatPayload(text) {
      const payload = { type: "chat.user", text };
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
      addUserMessage(trimmed);
      sendChatPayload(trimmed);
      maybeInterruptForBarge();
      input.focus();
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
    view.addUserMessage = addUserMessage;
    view.reset = () => clearPartial({ removeNode: true });
  }

  window.TranscriptView = view;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
