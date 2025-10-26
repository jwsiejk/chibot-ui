(() => {
  const ROOT_SCOPE = typeof window !== 'undefined'
    ? window
    : typeof globalThis !== 'undefined'
    ? globalThis
    : {};

  const API_ENDPOINT = '/api/v1/admin/settings';
  const DEFAULTS = {
    diag_client_hud: false,
    diag_audio_guard: true,
    diag_chunk_sample_n: 10,
  };
  const CHUNK_MIN = 1;
  const CHUNK_MAX = 100;

  const state = {
    values: { ...DEFAULTS },
    loading: false,
    saving: {},
  };

  const elements = {};
  let statusElement = null;
  let panelRoot = null;

  const FIELDS = [
    {
      key: 'diag_client_hud',
      type: 'boolean',
      label: 'Client HUD',
      description: 'Enable the in-client diagnostics overlay.',
    },
    {
      key: 'diag_audio_guard',
      type: 'boolean',
      label: 'Audio guardrails',
      description: 'Emit server-side diagnostics when client audio stalls.',
    },
    {
      key: 'diag_chunk_sample_n',
      type: 'number',
      label: 'Chunk sample N',
      description: 'Log every Nth audio chunk in the browser diagnostics overlay.',
      min: CHUNK_MIN,
      max: CHUNK_MAX,
    },
  ];

  function setStatus(message, tone) {
    if (!statusElement) {
      return;
    }
    statusElement.textContent = message || '';
    if (!tone) {
      statusElement.removeAttribute('data-tone');
      return;
    }
    statusElement.setAttribute('data-tone', tone);
  }

  function updatePanelState() {
    if (!panelRoot) return;
    const saving = Object.keys(state.saving).length > 0;
    panelRoot.classList.toggle('is-loading', Boolean(state.loading));
    panelRoot.classList.toggle('is-saving', saving);
  }

  function clampChunk(value) {
    const candidate = Number(value);
    if (!Number.isFinite(candidate)) {
      return DEFAULTS.diag_chunk_sample_n;
    }
    const rounded = Math.round(candidate);
    if (rounded < CHUNK_MIN) {
      return CHUNK_MIN;
    }
    if (rounded > CHUNK_MAX) {
      return CHUNK_MAX;
    }
    return rounded;
  }

  function normalizeSettings(raw) {
    const result = {};
    if (!raw || typeof raw !== 'object') {
      return result;
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'diag_client_hud')) {
      result.diag_client_hud = Boolean(raw.diag_client_hud);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'diag_audio_guard')) {
      result.diag_audio_guard = Boolean(raw.diag_audio_guard);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'diag_chunk_sample_n')) {
      result.diag_chunk_sample_n = clampChunk(raw.diag_chunk_sample_n);
    }
    return result;
  }

  function mergeIntoGlobal(values) {
    if (typeof window === 'undefined') {
      return;
    }
    const existing = window.__CFG__ && typeof window.__CFG__ === 'object' ? window.__CFG__ : {};
    window.__CFG__ = Object.assign(existing, {
      DIAG_CLIENT_HUD: Boolean(values.diag_client_hud),
      DIAG_AUDIO_GUARD: Boolean(values.diag_audio_guard),
      DIAG_CHUNK_SAMPLE_N: clampChunk(values.diag_chunk_sample_n),
    });
  }

  function applyValues(values) {
    if (!values || typeof values !== 'object') {
      return;
    }
    state.values = Object.assign({}, state.values, values);
    mergeIntoGlobal(state.values);
    updateInputs();
  }

  function hydrateFromGlobal() {
    if (typeof window === 'undefined') {
      return {};
    }
    const cfg = window.__CFG__;
    if (!cfg || typeof cfg !== 'object') {
      return {};
    }
    return normalizeSettings({
      diag_client_hud: cfg.DIAG_CLIENT_HUD,
      diag_audio_guard: cfg.DIAG_AUDIO_GUARD,
      diag_chunk_sample_n: cfg.DIAG_CHUNK_SAMPLE_N,
    });
  }

  function updateInputs() {
    Object.keys(elements).forEach((key) => {
      const entry = elements[key];
      if (!entry || !entry.input) {
        return;
      }
      const disabled = Boolean(state.loading) || Boolean(state.saving[key]);
      entry.input.disabled = disabled;
      if (entry.input.type === 'checkbox') {
        entry.input.checked = Boolean(state.values[key]);
      } else if (entry.input.type === 'number') {
        entry.input.value = String(clampChunk(state.values[key]));
      }
    });
    updatePanelState();
  }

  async function fetchSettings() {
    state.loading = true;
    updateInputs();
    setStatus('Loading settings…', 'info');
    try {
      const response = await fetch(API_ENDPOINT, {
        method: 'GET',
        credentials: 'include',
      });
      if (!response || !response.ok) {
        throw new Error('load_failed');
      }
      const data = await response.json();
      const normalized = normalizeSettings(data && data.settings);
      applyValues(normalized);
      setStatus('Settings loaded.', 'info');
    } catch (err) {
      console.warn('Failed to load admin settings', err);
      setStatus('Failed to load settings.', 'error');
    } finally {
      state.loading = false;
      updateInputs();
    }
  }

  async function persistSetting(key, value) {
    if (state.loading) {
      return;
    }
    state.saving[key] = true;
    updateInputs();
    setStatus('Saving…', 'info');
    try {
      const payload = { settings: { [key]: value } };
      const response = await fetch(API_ENDPOINT, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response || !response.ok) {
        throw new Error('save_failed');
      }
      const data = await response.json();
      const normalized = normalizeSettings(data && data.settings);
      if (Object.keys(normalized).length) {
        applyValues(normalized);
      } else {
        applyValues({ [key]: value });
      }
      setStatus('Changes saved.', 'success');
    } catch (err) {
      console.warn('Failed to save admin setting', err);
      setStatus('Failed to save changes.', 'error');
    } finally {
      delete state.saving[key];
      updateInputs();
    }
  }

  function handleToggle(key) {
    return (event) => {
      const checked = Boolean(event && event.target && event.target.checked);
      persistSetting(key, checked);
    };
  }

  function handleNumber(event) {
    if (!event || !event.target) {
      return;
    }
    const input = event.target;
    const sanitized = clampChunk(input.value);
    input.value = String(sanitized);
    persistSetting('diag_chunk_sample_n', sanitized);
  }

  function buildField(field) {
    const key = field.key;
    const wrapper = document.createElement('div');
    wrapper.className = 'admin-debug-card__field';

    if (field.type === 'boolean') {
      const toggle = document.createElement('label');
      toggle.className = 'admin-debug-card__toggle';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.id = `admin-debug-${key}`;
      input.addEventListener('change', handleToggle(key));
      const label = document.createElement('span');
      label.className = 'admin-debug-card__label';
      label.textContent = field.label;
      toggle.appendChild(input);
      toggle.appendChild(label);
      wrapper.appendChild(toggle);
      if (field.description) {
        const description = document.createElement('p');
        description.className = 'admin-debug-card__description';
        description.textContent = field.description;
        wrapper.appendChild(description);
      }
      elements[key] = { input };
      return wrapper;
    }

    if (field.type === 'number') {
      const label = document.createElement('label');
      label.className = 'admin-debug-card__label';
      label.setAttribute('for', `admin-debug-${key}`);
      label.textContent = field.label;
      wrapper.appendChild(label);

      const control = document.createElement('div');
      control.className = 'admin-debug-card__control';
      const input = document.createElement('input');
      input.type = 'number';
      input.id = `admin-debug-${key}`;
      input.min = String(field.min || CHUNK_MIN);
      input.max = String(field.max || CHUNK_MAX);
      input.step = '1';
      input.addEventListener('change', handleNumber);
      control.appendChild(input);
      wrapper.appendChild(control);

      if (field.description) {
        const description = document.createElement('p');
        description.className = 'admin-debug-card__description';
        description.textContent = field.description;
        wrapper.appendChild(description);
      }

      elements[key] = { input };
      return wrapper;
    }

    return null;
  }

  function buildPanel(root) {
    root.classList.add('admin-debug-card');
    const fragment = document.createDocumentFragment();
    FIELDS.forEach((field) => {
      const fieldNode = buildField(field);
      if (fieldNode) {
        fragment.appendChild(fieldNode);
      }
    });
    root.appendChild(fragment);
  }

  function init(root, options) {
    if (!root || !(root instanceof HTMLElement)) {
      throw new TypeError('AdminConfigPanel.init requires a root element');
    }
    if (root.dataset.configPanelInitialized === 'true') {
      return;
    }
    panelRoot = root;
    root.dataset.configPanelInitialized = 'true';
    root.innerHTML = '';
    statusElement = options && options.statusElement instanceof HTMLElement ? options.statusElement : null;
    ['diag_client_hud', 'diag_audio_guard', 'diag_chunk_sample_n'].forEach((key) => {
      delete elements[key];
    });
    buildPanel(root);

    const initialValues = Object.assign({}, DEFAULTS, hydrateFromGlobal());
    applyValues(initialValues);
    updateInputs();
    fetchSettings();
  }

  ROOT_SCOPE.AdminConfigPanel = Object.assign({}, ROOT_SCOPE.AdminConfigPanel || {}, { init });
})();
