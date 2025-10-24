(() => {
  const ROOT_SCOPE = typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : {};
  const API_ENDPOINT = "/api/v1/admin/settings";
  const VALID_MODES = ["required", "disabled"];
  const LABELS = {
    required: "Required",
    disabled: "Disabled"
  };
  const HINT_TEXT = "WS connects anonymously; admin endpoints may be restricted by policy.";

  function normalizeMode(value) {
    if (typeof value !== "string") {
      return null;
    }
    const candidate = value.trim().toLowerCase();
    if (!candidate) {
      return null;
    }
    return VALID_MODES.includes(candidate) ? candidate : null;
  }

  function extractSettings(payload) {
    if (!payload || typeof payload !== "object") {
      return { ws_auth_mode: "required" };
    }
    const root = payload.settings && typeof payload.settings === "object" ? payload.settings : payload;
    let raw = root.ws_auth_mode;
    if (typeof raw === "undefined") {
      raw = root.wsAuthMode;
    }
    const normalized = normalizeMode(raw) || "required";
    return { ws_auth_mode: normalized };
  }

  function ensureField(root) {
    let field = root.querySelector('[data-setting="ws_auth_mode"]');
    if (!field) {
      field = document.createElement("div");
      field.dataset.setting = "ws_auth_mode";
      field.classList.add("config-panel__field");
      root.appendChild(field);
    }
    return field;
  }

  function ensureLabel(field) {
    let label = field.querySelector("label");
    if (!label) {
      label = document.createElement("label");
      field.appendChild(label);
    }
    label.textContent = "WS Auth Mode";
    return label;
  }

  function ensureSelect(field) {
    let select = field.querySelector("select[name='ws_auth_mode']");
    if (!select) {
      select = document.createElement("select");
      select.name = "ws_auth_mode";
      select.id = select.id || "wsAuthModeSelect";
      select.classList.add("config-panel__select");
      select.autocomplete = "off";
      field.appendChild(select);
    }
    const seen = new Set();
    Array.from(select.options).forEach((option) => {
      const normalized = normalizeMode(option.value);
      if (!normalized) {
        option.remove();
        return;
      }
      seen.add(normalized);
      option.value = normalized;
      option.textContent = LABELS[normalized] || normalized;
    });
    VALID_MODES.forEach((mode) => {
      if (seen.has(mode)) {
        return;
      }
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = LABELS[mode] || mode;
      select.appendChild(option);
    });
    return select;
  }

  function ensureHint(field) {
    let hint = field.querySelector('[data-role="ws-auth-hint"]');
    if (!hint) {
      hint = document.createElement("p");
      hint.dataset.role = "ws-auth-hint";
      field.appendChild(hint);
    }
    hint.textContent = HINT_TEXT;
    hint.style.display = "none";
    hint.style.margin = "6px 0 0";
    hint.style.padding = "6px 8px";
    hint.style.borderRadius = "6px";
    hint.style.fontSize = "12px";
    hint.style.lineHeight = "1.4";
    hint.style.background = "rgba(255, 204, 0, 0.2)";
    hint.style.color = "#4a3800";
    hint.style.border = "1px solid rgba(255, 204, 0, 0.45)";
    hint.setAttribute("role", "note");
    hint.setAttribute("aria-live", "polite");
    return hint;
  }

  function ensureActions(root) {
    let actions = root.querySelector(".config-panel__actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.classList.add("config-panel__actions");
      root.appendChild(actions);
    }
    let save = actions.querySelector('[data-action="save"]');
    if (!save) {
      save = document.createElement("button");
      save.type = "button";
      save.dataset.action = "save";
      save.classList.add("config-panel__save");
      save.textContent = "Save";
      actions.appendChild(save);
    }
    return { actions, save };
  }

  function ensureStatus(root) {
    let status = root.querySelector('[data-role="status"]');
    if (!status) {
      status = document.createElement("div");
      status.dataset.role = "status";
      status.classList.add("config-panel__status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      root.appendChild(status);
    }
    return status;
  }

  function setStatus(el, message, tone) {
    if (!el) {
      return;
    }
    el.textContent = message || "";
    const tones = ["info", "error", "success"];
    tones.forEach((value) => el.classList.remove(`config-panel__status--${value}`));
    if (tone && tones.includes(tone)) {
      el.classList.add(`config-panel__status--${tone}`);
    }
  }

  function buildHeaders(baseHeaders, extra) {
    const headers = {};
    [baseHeaders, extra].forEach((source) => {
      if (!source) {
        return;
      }
      if (typeof Headers !== "undefined" && source instanceof Headers) {
        source.forEach((value, key) => {
          if (typeof value === "string") {
            headers[key] = value;
          }
        });
        return;
      }
      if (typeof source !== "object") {
        return;
      }
      for (const key in source) {
        if (!Object.prototype.hasOwnProperty.call(source, key)) {
          continue;
        }
        const value = source[key];
        if (typeof value === "string") {
          headers[key] = value;
        }
      }
    });
    return headers;
  }

  function AdminPanel(root, options) {
    this.root = root;
    this.options = options;
    this.fetchImpl = options.fetchImpl;
    this.headers = options.headers;
    this.credentials = options.credentials;
    this.state = { ws_auth_mode: "required" };
    this.field = ensureField(root);
    this.label = ensureLabel(this.field);
    this.select = ensureSelect(this.field);
    if (this.label && this.select && this.select.id) {
      this.label.setAttribute("for", this.select.id);
    }
    this.hint = ensureHint(this.field);
    const actionRefs = ensureActions(root);
    this.actions = actionRefs.actions;
    this.saveButton = actionRefs.save;
    this.status = ensureStatus(root);
    this.loading = false;

    this.onSaveClick = this.onSaveClick.bind(this);
    this.onModeChange = this.onModeChange.bind(this);
    this.saveButton.addEventListener("click", this.onSaveClick);
    this.select.addEventListener("change", this.onModeChange);
    this.syncHint(this.state.ws_auth_mode);
  }

  AdminPanel.prototype.setLoading = function (loading) {
    this.loading = Boolean(loading);
    this.select.disabled = this.loading;
    this.saveButton.disabled = this.loading;
  };

  AdminPanel.prototype.onModeChange = function () {
    this.syncHint(this.select.value);
  };

  AdminPanel.prototype.syncHint = function (mode) {
    const normalized = normalizeMode(mode);
    if (!this.hint) {
      return;
    }
    if (normalized === "disabled") {
      this.hint.style.display = "block";
      this.hint.setAttribute("aria-hidden", "false");
    } else {
      this.hint.style.display = "none";
      this.hint.setAttribute("aria-hidden", "true");
    }
  };

  AdminPanel.prototype.refresh = async function () {
    const fetchImpl = this.fetchImpl;
    if (typeof fetchImpl !== "function") {
      return;
    }
    this.setLoading(true);
    setStatus(this.status, "Loading settings…", "info");
    try {
      const response = await fetchImpl(API_ENDPOINT, {
        method: "GET",
        credentials: this.credentials,
        headers: buildHeaders(this.headers)
      });
      if (!response || typeof response.status !== "number") {
        throw new Error("Invalid response");
      }
      if (!response.ok) {
        let detail = "Failed to load settings";
        try {
          const errorData = await response.json();
          if (errorData && typeof errorData.detail === "string") {
            detail = errorData.detail;
          }
        } catch (err) {
          // ignore JSON errors
        }
        throw new Error(detail);
      }
      const data = await response.json();
      this.state = extractSettings(data);
      this.select.value = this.state.ws_auth_mode;
      this.syncHint(this.state.ws_auth_mode);
      setStatus(this.status, "Settings up to date.", "success");
      if (typeof this.options.onChange === "function") {
        this.options.onChange(Object.assign({}, this.state));
      }
    } catch (err) {
      const message = err && err.message ? err.message : "Unable to load settings";
      setStatus(this.status, message, "error");
    } finally {
      this.setLoading(false);
    }
  };

  AdminPanel.prototype.onSaveClick = async function (event) {
    event.preventDefault();
    await this.save();
  };

  AdminPanel.prototype.save = async function () {
    const fetchImpl = this.fetchImpl;
    if (typeof fetchImpl !== "function") {
      return;
    }
    const selected = normalizeMode(this.select.value) || "required";
    this.setLoading(true);
    setStatus(this.status, "Saving…", "info");
    try {
      const response = await fetchImpl(API_ENDPOINT, {
        method: "PATCH",
        credentials: this.credentials,
        headers: buildHeaders(this.headers, { "content-type": "application/json" }),
        body: JSON.stringify({ ws_auth_mode: selected })
      });
      if (!response || typeof response.status !== "number") {
        throw new Error("Invalid response");
      }
      if (!response.ok) {
        let detail = "Failed to save settings";
        try {
          const errorData = await response.json();
          if (errorData && typeof errorData.detail === "string") {
            detail = errorData.detail;
          }
        } catch (err) {
          // ignore JSON errors
        }
        throw new Error(detail);
      }
      const data = await response.json();
      this.state = extractSettings(data);
      this.select.value = this.state.ws_auth_mode;
      this.syncHint(this.state.ws_auth_mode);
      setStatus(this.status, "Settings saved.", "success");
      if (typeof this.options.onChange === "function") {
        this.options.onChange(Object.assign({}, this.state));
      }
    } catch (err) {
      const message = err && err.message ? err.message : "Unable to save settings";
      setStatus(this.status, message, "error");
    } finally {
      this.setLoading(false);
    }
  };

  AdminPanel.prototype.destroy = function () {
    if (this.saveButton) {
      this.saveButton.removeEventListener("click", this.onSaveClick);
    }
    if (this.select) {
      this.select.removeEventListener("change", this.onModeChange);
    }
  };

  function resolveFetch(options) {
    if (options && typeof options.fetch === "function") {
      return options.fetch;
    }
    if (ROOT_SCOPE && typeof ROOT_SCOPE.fetch === "function") {
      return ROOT_SCOPE.fetch.bind(ROOT_SCOPE);
    }
    return null;
  }

  function init(root, opts) {
    const options = opts && typeof opts === "object" ? Object.assign({}, opts) : {};
    if (!root || typeof root !== "object" || root.nodeType !== 1) {
      throw new TypeError("AdminConfigPanel.init requires a root element");
    }
    if (!root.classList.contains("config-panel")) {
      root.classList.add("config-panel");
    }
    const fetchImpl = resolveFetch(options);
    if (typeof fetchImpl !== "function") {
      throw new Error("AdminConfigPanel requires window.fetch or a custom fetch option");
    }
    const panel = new AdminPanel(root, {
      fetchImpl,
      headers: options.headers || null,
      credentials: options.credentials || "same-origin",
      onChange: options.onChange
    });
    panel.refresh();
    return panel;
  }

  ROOT_SCOPE.AdminConfigPanel = Object.assign(ROOT_SCOPE.AdminConfigPanel || {}, { init });
})();
