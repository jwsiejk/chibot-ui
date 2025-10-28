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
    policy_media: {
      asr_input: 'webm_opus',
      asr_rate_hz: 48000,
      asr_channels: 1,
      fallbacks_allowed: false,
    },
    policy_capture: {
      start_on_asr_ready: true,
      start_on_turn_ready: true,
      timeslice_ms: 200,
      mask_during_tts: true,
    },
  };
  const CHUNK_MIN = 1;
  const CHUNK_MAX = 100;
  const MEDIA_ALLOWED_INPUTS = ['webm_opus', 'pcm_16k'];
  const CAPTURE_TIMESLICE_MIN = 20;
  const CAPTURE_TIMESLICE_STEP = 10;

  function cloneDefaults() {
    return {
      diag_client_hud: DEFAULTS.diag_client_hud,
      diag_audio_guard: DEFAULTS.diag_audio_guard,
      diag_chunk_sample_n: DEFAULTS.diag_chunk_sample_n,
      policy_media: { ...DEFAULTS.policy_media },
      policy_capture: { ...DEFAULTS.policy_capture },
    };
  }

  const state = {
    values: cloneDefaults(),
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
      sanitize: clampChunk,
    },
    {
      key: 'policy_media.asr_input',
      settingKey: 'policy_media',
      prop: 'asr_input',
      type: 'select',
      label: 'ASR Input',
      description: 'Choose the microphone stream format sent to the ASR vendor.',
      options: [
        { value: 'webm_opus', label: 'WebM + Opus (default)' },
        { value: 'pcm_16k', label: 'PCM 16 kHz (future)', disabled: true },
      ],
    },
    {
      key: 'policy_media.fallbacks_allowed',
      settingKey: 'policy_media',
      prop: 'fallbacks_allowed',
      type: 'boolean',
      label: 'Allow fallbacks',
      description: 'Permit switching to alternate ASR inputs when the primary fails.',
    },
    {
      key: 'policy_capture.start_on_asr_ready',
      settingKey: 'policy_capture',
      prop: 'start_on_asr_ready',
      type: 'boolean',
      label: 'Start on ASR Ready',
      description: 'Begin microphone capture after the ASR session is ready.',
    },
    {
      key: 'policy_capture.start_on_turn_ready',
      settingKey: 'policy_capture',
      prop: 'start_on_turn_ready',
      type: 'boolean',
      label: 'Start on Turn Ready',
      description: 'Begin microphone capture when the turn is ready to listen.',
    },
    {
      key: 'policy_capture.timeslice_ms',
      settingKey: 'policy_capture',
      prop: 'timeslice_ms',
      type: 'number',
      label: 'Timeslice (ms)',
      description: 'Chunk duration to send from the browser when streaming audio.',
      min: CAPTURE_TIMESLICE_MIN,
      step: CAPTURE_TIMESLICE_STEP,
      sanitize: clampTimeslice,
    },
    {
      key: 'policy_capture.mask_during_tts',
      settingKey: 'policy_capture',
      prop: 'mask_during_tts',
      type: 'boolean',
      label: 'Mask during TTS',
      description: 'Mute microphone capture while the assistant is speaking.',
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

  function coerceBoolean(value, fallback) {
    if (typeof value === 'boolean') {
      return value;
    }
    if (typeof value === 'number') {
      return value !== 0;
    }
    if (typeof value === 'string') {
      const candidate = value.trim().toLowerCase();
      if (['1', 'true', 't', 'yes', 'y', 'on'].includes(candidate)) {
        return true;
      }
      if (['0', 'false', 'f', 'no', 'n', 'off'].includes(candidate)) {
        return false;
      }
    }
    return Boolean(fallback);
  }

  function clampTimeslice(value) {
    const candidate = Number(value);
    if (!Number.isFinite(candidate)) {
      return DEFAULTS.policy_capture.timeslice_ms;
    }
    const rounded = Math.round(candidate / CAPTURE_TIMESLICE_STEP) * CAPTURE_TIMESLICE_STEP;
    if (rounded < CAPTURE_TIMESLICE_MIN) {
      return CAPTURE_TIMESLICE_MIN;
    }
    return rounded;
  }

  function sanitizePolicyMedia(value) {
    const base = { ...DEFAULTS.policy_media };
    if (!value || typeof value !== 'object') {
      return base;
    }
    const inputValue = typeof value.asr_input === 'string' ? value.asr_input.trim() : '';
    if (MEDIA_ALLOWED_INPUTS.includes(inputValue)) {
      base.asr_input = inputValue;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'fallbacks_allowed')) {
      base.fallbacks_allowed = coerceBoolean(value.fallbacks_allowed, base.fallbacks_allowed);
    }
    const rateCandidate = Number(value.asr_rate_hz);
    if (Number.isFinite(rateCandidate) && rateCandidate > 0) {
      base.asr_rate_hz = Math.round(rateCandidate);
    }
    const channelCandidate = Number(value.asr_channels);
    if (Number.isFinite(channelCandidate) && channelCandidate > 0) {
      base.asr_channels = Math.round(channelCandidate);
    }
    return base;
  }

  function sanitizePolicyCapture(value) {
    const base = { ...DEFAULTS.policy_capture };
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'start_on_asr_ready')) {
      base.start_on_asr_ready = coerceBoolean(
        value.start_on_asr_ready,
        base.start_on_asr_ready,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'start_on_turn_ready')) {
      base.start_on_turn_ready = coerceBoolean(
        value.start_on_turn_ready,
        base.start_on_turn_ready,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'mask_during_tts')) {
      base.mask_during_tts = coerceBoolean(
        value.mask_during_tts,
        base.mask_during_tts,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'timeslice_ms')) {
      base.timeslice_ms = clampTimeslice(value.timeslice_ms);
    }
    return base;
  }

  function sanitizeSettingValue(key, value) {
    if (key === 'policy_media') {
      return sanitizePolicyMedia(value);
    }
    if (key === 'policy_capture') {
      return sanitizePolicyCapture(value);
    }
    if (key === 'diag_chunk_sample_n') {
      return clampChunk(value);
    }
    if (key === 'diag_client_hud' || key === 'diag_audio_guard') {
      return coerceBoolean(value, DEFAULTS[key]);
    }
    return value;
  }

  function getSettingKey(field) {
    return field && field.settingKey ? field.settingKey : field.key;
  }

  function sanitizeFieldInput(field, value) {
    if (!field) {
      return value;
    }
    if (field.type === 'boolean') {
      const settingKey = getSettingKey(field);
      let fallback = false;
      if (field.prop) {
        const defaults = DEFAULTS[settingKey];
        if (defaults && typeof defaults === 'object') {
          fallback = Boolean(defaults[field.prop]);
        }
      } else if (Object.prototype.hasOwnProperty.call(DEFAULTS, settingKey)) {
        fallback = Boolean(DEFAULTS[settingKey]);
      }
      return coerceBoolean(value, fallback);
    }
    if (field.type === 'number') {
      if (typeof field.sanitize === 'function') {
        return field.sanitize(value);
      }
      const numeric = Number(value);
      return Number.isFinite(numeric) ? numeric : 0;
    }
    if (field.type === 'select') {
      const options = Array.isArray(field.options) ? field.options : [];
      const str = typeof value === 'string' ? value : '';
      if (options.some((option) => option.value === str)) {
        return str;
      }
      return options.length ? options[0].value : '';
    }
    return value;
  }

  function getFieldValue(field) {
    const settingKey = getSettingKey(field);
    const current = state.values[settingKey];
    let value = current;
    if (field.prop && current && typeof current === 'object') {
      value = current[field.prop];
    }
    return sanitizeFieldInput(field, value);
  }

  function persistFieldValue(field, sanitizedValue) {
    const settingKey = getSettingKey(field);
    let payloadValue = sanitizedValue;
    if (field.prop) {
      const existing = sanitizeSettingValue(settingKey, state.values[settingKey]);
      payloadValue = Object.assign({}, existing, { [field.prop]: sanitizedValue });
    } else if (
      settingKey !== field.key &&
      sanitizedValue &&
      typeof sanitizedValue === 'object' &&
      !Array.isArray(sanitizedValue)
    ) {
      const existing = sanitizeSettingValue(settingKey, state.values[settingKey]);
      payloadValue = Object.assign({}, existing, sanitizedValue);
    }
    persistSetting(settingKey, payloadValue, field.key);
  }

  function handleFieldChange(field, rawValue) {
    const sanitizedValue = sanitizeFieldInput(field, rawValue);
    const entry = elements[field.key];
    if (entry && entry.input && field.type === 'number') {
      entry.input.value = String(sanitizedValue);
    }
    persistFieldValue(field, sanitizedValue);
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
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_media')) {
      result.policy_media = sanitizePolicyMedia(raw.policy_media);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_capture')) {
      result.policy_capture = sanitizePolicyCapture(raw.policy_capture);
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
      POLICY_MEDIA: sanitizePolicyMedia(values.policy_media),
      POLICY_CAPTURE: sanitizePolicyCapture(values.policy_capture),
    });
  }

  function applyValues(values) {
    if (!values || typeof values !== 'object') {
      return;
    }
    const next = { ...state.values };
    Object.keys(values).forEach((key) => {
      const sanitized = sanitizeSettingValue(key, values[key]);
      if (key === 'policy_media' || key === 'policy_capture') {
        next[key] = { ...sanitized };
      } else {
        next[key] = sanitized;
      }
    });
    state.values = next;
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
      policy_media: cfg.POLICY_MEDIA,
      policy_capture: cfg.POLICY_CAPTURE,
    });
  }

  function updateInputs() {
    Object.keys(elements).forEach((key) => {
      const entry = elements[key];
      if (!entry || !entry.input || !entry.field) {
        return;
      }
      const field = entry.field;
      const disabled = Boolean(state.loading) || Boolean(state.saving[key]);
      entry.input.disabled = disabled;
      const value = getFieldValue(field);
      if (field.type === 'boolean') {
        entry.input.checked = Boolean(value);
      } else if (field.type === 'number') {
        entry.input.value = String(value);
      } else if (field.type === 'select') {
        entry.input.value = String(value);
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

  async function persistSetting(key, value, savingKey) {
    if (state.loading) {
      return;
    }
    const tracker = savingKey || key;
    state.saving[tracker] = true;
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
      delete state.saving[tracker];
      updateInputs();
    }
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
      input.addEventListener('change', (event) => {
        const checked = Boolean(event && event.target && event.target.checked);
        handleFieldChange(field, checked);
      });
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
      elements[key] = { input, field };
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
      if (typeof field.min === 'number') {
        input.min = String(field.min);
      } else {
        input.min = String(CHUNK_MIN);
      }
      if (typeof field.max === 'number') {
        input.max = String(field.max);
      }
      input.step = field.step ? String(field.step) : '1';
      input.addEventListener('change', (event) => {
        const raw = event && event.target ? event.target.value : '';
        handleFieldChange(field, raw);
      });
      control.appendChild(input);
      wrapper.appendChild(control);

      if (field.description) {
        const description = document.createElement('p');
        description.className = 'admin-debug-card__description';
        description.textContent = field.description;
        wrapper.appendChild(description);
      }

      elements[key] = { input, field };
      return wrapper;
    }

    if (field.type === 'select') {
      const label = document.createElement('label');
      label.className = 'admin-debug-card__label';
      label.setAttribute('for', `admin-debug-${key}`);
      label.textContent = field.label;
      wrapper.appendChild(label);

      const control = document.createElement('div');
      control.className = 'admin-debug-card__control';
      const select = document.createElement('select');
      select.id = `admin-debug-${key}`;
      const options = Array.isArray(field.options) ? field.options : [];
      options.forEach((option) => {
        const opt = document.createElement('option');
        opt.value = option.value;
        opt.textContent = option.label;
        if (option.disabled) {
          opt.disabled = true;
        }
        select.appendChild(opt);
      });
      select.addEventListener('change', (event) => {
        const raw = event && event.target ? event.target.value : '';
        handleFieldChange(field, raw);
      });
      control.appendChild(select);
      wrapper.appendChild(control);

      if (field.description) {
        const description = document.createElement('p');
        description.className = 'admin-debug-card__description';
        description.textContent = field.description;
        wrapper.appendChild(description);
      }

      elements[key] = { input: select, field };
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
    Object.keys(elements).forEach((key) => {
      delete elements[key];
    });
    buildPanel(root);

    const initialValues = Object.assign(cloneDefaults(), hydrateFromGlobal());
    applyValues(initialValues);
    updateInputs();
    fetchSettings();
  }

  ROOT_SCOPE.AdminConfigPanel = Object.assign({}, ROOT_SCOPE.AdminConfigPanel || {}, { init });
})();
