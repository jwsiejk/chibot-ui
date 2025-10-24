(() => {
  const TRACE_TYPES = ["EVT_TURN_BEGIN", "EVT_TTS_START", "EVT_TTS_END", "EVT_TTS_MASK"];
  const FETCH_LIMIT = 200;
  const RENDER_LIMIT = 120;

  function normalizeString(value) {
    if (typeof value !== "string") return null;
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }

  function buildTraceUrl(sid) {
    const params = new URLSearchParams();
    params.set("type", TRACE_TYPES.join(","));
    params.set("limit", String(FETCH_LIMIT));
    return `/api/v1/admin/flow/${encodeURIComponent(sid)}/trace?${params.toString()}`;
  }

  function buildZipUrl(sid) {
    return `/api/v1/admin/flow/${encodeURIComponent(sid)}/zip`;
  }

  function applyDownloadState(link, sid) {
    if (!link) return;
    link.href = "#";
    if (sid) {
      link.removeAttribute("aria-disabled");
      link.removeAttribute("tabindex");
      link.title = "Download flow.zip archive";
    } else {
      link.setAttribute("aria-disabled", "true");
      link.setAttribute("tabindex", "-1");
      link.title = "Start a session to enable export downloads.";
    }
  }

  function init(options = {}) {
    if (window.__DevInspectorInitialized) {
      return;
    }
    window.__DevInspectorInitialized = true;

    const toggle = document.getElementById("devInspectorToggle");
    const panel = document.getElementById("devInspectorPanel");
    const output = document.getElementById("devInspectorOutput");
    const sidLabel = document.getElementById("devInspectorSid");
    const statusLabel = document.getElementById("devInspectorStatus");
    const refreshBtn = document.getElementById("devInspectorRefresh");
    const closeBtn = document.getElementById("devInspectorClose");
    const downloadLink = document.getElementById("devInspectorDownload");

    if (!toggle || !panel || !output || !sidLabel || !statusLabel || !downloadLink) {
      console.warn("DevInspector UI elements missing; skipping init.");
      return;
    }

    toggle.hidden = false;
    toggle.setAttribute("title", "Open flow inspector");
    panel.setAttribute("aria-hidden", "true");

    const AppState = options.AppState || window.AppState;
    const getState = () => (AppState && typeof AppState.getState === "function") ? AppState.getState() : {};

    let latestSid = null;
    let isOpen = false;
    let currentAbort = null;
    let isFetching = false;
    let unsubscribe = null;
    let isDownloading = false;

    function setStatus(message) {
      statusLabel.textContent = message;
    }

    function renderSid(sid) {
      if (sid) {
        sidLabel.textContent = `SID: ${sid}`;
      } else {
        sidLabel.textContent = "SID: —";
      }
    }

    function resetOutput() {
      output.textContent = "";
    }

    async function loadTrace() {
      if (!latestSid) {
        resetOutput();
        setStatus("Connect to capture flow events.");
        return;
      }
      if (currentAbort) {
        currentAbort.abort();
        currentAbort = null;
        isFetching = false;
      }
      const controller = new AbortController();
      currentAbort = controller;
      isFetching = true;
      setStatus("Loading trace…");
      const url = buildTraceUrl(latestSid);
      const headers = {
        Accept: "application/x-ndjson, application/json"
      };
      try {
        const response = await fetch(url, {
          method: "GET",
          headers,
          credentials: "include",
          cache: "no-store",
          signal: controller.signal
        });
        if (!response.ok) {
          let errorDetail = `${response.status}`;
          try {
            const contentType = response.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
              const payload = await response.json();
              if (payload && (payload.error || payload.detail)) {
                errorDetail = `${payload.error || "error"}: ${payload.detail || ""}`.trim();
              }
            } else {
              const text = await response.text();
              if (text) {
                errorDetail = text.trim();
              }
            }
          } catch (err) {
            console.warn("Failed to parse trace error payload", err);
          }
          resetOutput();
          setStatus(`Trace request failed (${errorDetail || "unknown error"}).`);
          return;
        }
        const body = await response.text();
        const lines = body.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        const recent = lines.slice(-RENDER_LIMIT);
        if (recent.length) {
          output.textContent = recent.join("\n");
          setStatus(`Showing ${recent.length} event${recent.length === 1 ? "" : "s"}.`);
        } else {
          output.textContent = "(no matching events)";
          setStatus("No matching events yet.");
        }
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        console.error("DevInspector trace fetch failed", err);
        resetOutput();
        setStatus(`Trace request failed: ${err && err.message ? err.message : "unknown error"}.`);
      } finally {
        if (currentAbort === controller) {
          currentAbort = null;
          isFetching = false;
        }
      }
    }

    function handleStateChange(state) {
      const sid = normalizeString(state && state.sid);
      if (sid !== latestSid) {
        latestSid = sid;
        renderSid(latestSid);
        applyDownloadState(downloadLink, latestSid);
        if (!latestSid) {
          resetOutput();
          setStatus("Connect to capture flow events.");
        } else if (isOpen) {
          loadTrace();
        } else {
          setStatus("Inspector ready — open to view latest events.");
        }
      }
    }

    if (AppState && typeof AppState.subscribe === "function") {
      unsubscribe = AppState.subscribe(handleStateChange);
    } else {
      handleStateChange(getState());
    }

    const initialState = getState();
    if (!latestSid && initialState) {
      handleStateChange(initialState);
    }
    applyDownloadState(downloadLink, latestSid);
    if (!latestSid) {
      setStatus("Connect to capture flow events.");
    }

    function openPanel() {
      if (isOpen) return;
      panel.hidden = false;
      panel.setAttribute("aria-hidden", "false");
      toggle.setAttribute("aria-expanded", "true");
      isOpen = true;
      if (latestSid) {
        loadTrace();
      } else if (!latestSid) {
        resetOutput();
        setStatus("Connect to capture flow events.");
      }
    }

    function closePanel() {
      if (!isOpen) return;
      panel.hidden = true;
      panel.setAttribute("aria-hidden", "true");
      toggle.setAttribute("aria-expanded", "false");
      isOpen = false;
    }

    toggle.addEventListener("click", () => {
      if (isOpen) {
        closePanel();
      } else {
        openPanel();
      }
    });

    if (closeBtn) {
      closeBtn.addEventListener("click", () => {
        closePanel();
        toggle.focus({ preventScroll: true });
      });
    }

    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        if (isFetching) {
          return;
        }
        if (!latestSid) {
          resetOutput();
          setStatus("Connect to capture flow events.");
          return;
        }
        loadTrace();
      });
    }

    async function downloadZip() {
      if (!latestSid) {
        setStatus("Connect to capture flow events.");
        return;
      }
      if (isDownloading) {
        return;
      }
      isDownloading = true;
      setStatus("Preparing flow.zip download…");
      const url = buildZipUrl(latestSid);
      try {
        const response = await fetch(url, {
          method: "GET",
          credentials: "include",
          cache: "no-store"
        });
        if (!response.ok) {
          let errorDetail = `${response.status}`;
          try {
            const contentType = response.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
              const payload = await response.json();
              if (payload && (payload.error || payload.detail)) {
                errorDetail = `${payload.error || "error"}: ${payload.detail || ""}`.trim();
              }
            } else {
              const text = await response.text();
              if (text) {
                errorDetail = text.trim();
              }
            }
          } catch (err) {
            console.warn("Failed to parse zip error payload", err);
          }
          setStatus(`Download failed (${errorDetail || "unknown error"}).`);
          return;
        }
        const blob = await response.blob();
        const filename = `flow-${latestSid}.zip`;
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        requestAnimationFrame(() => {
          document.body.removeChild(anchor);
          URL.revokeObjectURL(objectUrl);
        });
        setStatus(`Download started (${filename}).`);
      } catch (err) {
        console.error("DevInspector zip download failed", err);
        setStatus(`Download failed: ${err && err.message ? err.message : "unknown error"}.`);
      } finally {
        isDownloading = false;
      }
    }

    downloadLink.addEventListener("click", (event) => {
      event.preventDefault();
      if (downloadLink.getAttribute("aria-disabled") === "true") {
        return;
      }
      downloadZip();
    });

    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closePanel();
        toggle.focus({ preventScroll: true });
      }
    });

    window.addEventListener("beforeunload", () => {
      if (typeof unsubscribe === "function") {
        try {
          unsubscribe();
        } catch (err) {
          console.warn("DevInspector unsubscribe failed", err);
        }
      }
      if (currentAbort) {
        currentAbort.abort();
      }
    });
  }

  window.DevInspector = { init };
})();
