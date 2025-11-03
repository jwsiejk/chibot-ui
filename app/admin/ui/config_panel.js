(() => {
  const ROOT_SCOPE = typeof window !== 'undefined'
    ? window
    : typeof globalThis !== 'undefined'
    ? globalThis
    : {};

  const API_ENDPOINT = '/api/v1/admin/settings';
  const ASR_VENDOR_OPTIONS = [
    { value: 'speechmatics', label: 'Speechmatics' },
  ];

  const ASR_VENDOR_SECONDARY_OPTIONS = [
    { value: '', label: 'None' },
    ...ASR_VENDOR_OPTIONS,
  ];

  const AUDIO_PIPELINE_OPTIONS = [
    { value: 'pcm16', label: 'PCM 16 kHz' },
  ];

  const DEFAULTS = {
    diag_client_hud: false,
    diag_audio_guard: true,
    diag_chunk_sample_n: 10,
    policy_media: {
      asr_input: 'pcm_16k',
      asr_rate_hz: 16000,
      asr_channels: 1,
      fallbacks_allowed: false,
    },
    policy_capture: {
      start_on_asr_ready: true,
      start_on_turn_ready: true,
      timeslice_ms: 200,
      mask_during_tts: true,
    },
    policy_recorder: {
      stop_on_tts_start: false,
      mute_send_during_tts: true,
    },
    policy_input: {
      require_hotword_to_start: false,
    },
    policy_asr: {
      prearm_on_tts_end: true,
      keep_stream_warm_ms: 30000,
      commit_on_vad_silence: true,
      commit_silence_ms: 900,
      max_utterance_ms: 8000,
      vendor: { primary: 'speechmatics', secondary: null },
    },
    policy_routing: {
      ws_version: 'v2',
    },
    policy_audio: {
      pipeline: { mode: 'pcm16' },
    },
  };
  const CHUNK_MIN = 1;
  const CHUNK_MAX = 100;
  const MEDIA_ALLOWED_INPUTS = ['pcm_16k'];
  const CAPTURE_TIMESLICE_MIN = 20;
  const CAPTURE_TIMESLICE_STEP = 10;

  function cloneDefaults() {
    return {
      diag_client_hud: DEFAULTS.diag_client_hud,
      diag_audio_guard: DEFAULTS.diag_audio_guard,
      diag_chunk_sample_n: DEFAULTS.diag_chunk_sample_n,
      policy_media: { ...DEFAULTS.policy_media },
      policy_capture: { ...DEFAULTS.policy_capture },
      policy_recorder: { ...DEFAULTS.policy_recorder },
      policy_input: { ...DEFAULTS.policy_input },
      policy_asr: JSON.parse(JSON.stringify(DEFAULTS.policy_asr)),
      policy_routing: { ...DEFAULTS.policy_routing },
      policy_audio: JSON.parse(JSON.stringify(DEFAULTS.policy_audio)),
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
      options: [{ value: 'pcm_16k', label: 'PCM 16 kHz' }],
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
    {
      key: 'policy_recorder.stop_on_tts_start',
      settingKey: 'policy_recorder',
      prop: 'stop_on_tts_start',
      type: 'boolean',
      label: 'Stop recorder on TTS start',
      description: 'Disable microphone streaming entirely when TTS begins.',
    },
    {
      key: 'policy_recorder.mute_send_during_tts',
      settingKey: 'policy_recorder',
      prop: 'mute_send_during_tts',
      type: 'boolean',
      label: 'Mute send during TTS',
      description: 'Keep the recorder armed but pause audio chunk transmission while TTS plays.',
    },
    {
      key: 'policy_input.require_hotword_to_start',
      settingKey: 'policy_input',
      prop: 'require_hotword_to_start',
      type: 'boolean',
      label: 'Require wake word to start',
      description: 'Require the wake word before auto-starting microphone capture.',
    },
    {
      key: 'policy_asr.prearm_on_tts_end',
      settingKey: 'policy_asr',
      prop: 'prearm_on_tts_end',
      type: 'boolean',
      label: 'Pre-arm ASR after TTS',
      description: 'Open a warm ASR stream when TTS ends so listening is immediate.',
    },
    {
      key: 'policy_asr.keep_stream_warm_ms',
      settingKey: 'policy_asr',
      prop: 'keep_stream_warm_ms',
      type: 'number',
      label: 'ASR keep-warm (ms)',
      description: 'How long to keep the ASR stream warm after pre-arming.',
      min: 0,
      sanitize: (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || numeric < 0) {
          return DEFAULTS.policy_asr.keep_stream_warm_ms;
        }
        return Math.round(numeric);
      },
    },
    {
      key: 'policy_asr.commit_on_vad_silence',
      settingKey: 'policy_asr',
      prop: 'commit_on_vad_silence',
      type: 'boolean',
      label: 'Commit on VAD silence',
      description: 'Automatically commit turns when silence is detected.',
    },
    {
      key: 'policy_asr.commit_silence_ms',
      settingKey: 'policy_asr',
      prop: 'commit_silence_ms',
      type: 'number',
      label: 'Commit silence (ms)',
      description: 'Silence duration required before committing a turn.',
      min: 0,
      sanitize: (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || numeric < 0) {
          return DEFAULTS.policy_asr.commit_silence_ms;
        }
        return Math.round(numeric);
      },
    },
    {
      key: 'policy_asr.max_utterance_ms',
      settingKey: 'policy_asr',
      prop: 'max_utterance_ms',
      type: 'number',
      label: 'Max utterance (ms)',
      description: 'Maximum duration to keep an utterance open before forcing a commit.',
      min: 0,
      sanitize: (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || numeric < 0) {
          return DEFAULTS.policy_asr.max_utterance_ms;
        }
        return Math.round(numeric);
      },
    },
    {
      key: 'policy_asr.vendor.primary',
      settingKey: 'policy_asr',
      propPath: ['vendor', 'primary'],
      type: 'select',
      label: 'ASR Vendor',
      description: 'Preferred speech recognition vendor for new sessions.',
      options: ASR_VENDOR_OPTIONS,
    },
    {
      key: 'policy_asr.vendor.secondary',
      settingKey: 'policy_asr',
      propPath: ['vendor', 'secondary'],
      type: 'select',
      label: 'Secondary ASR vendor',
      description: 'Fallback vendor when the primary is unavailable.',
      options: ASR_VENDOR_SECONDARY_OPTIONS,
      emptyAsNull: true,
    },
    {
      key: 'policy_audio.pipeline.mode',
      settingKey: 'policy_audio',
      propPath: ['pipeline', 'mode'],
      type: 'select',
      label: 'Audio pipeline mode',
      description: 'Select the media pipeline format advertised to clients.',
      options: AUDIO_PIPELINE_OPTIONS,
    },
    {
      key: 'policy_routing.ws_version',
      settingKey: 'policy_routing',
      prop: 'ws_version',
      type: 'select',
      label: 'WS routing version',
      description: 'WebSocket API version for chat connections.',
      options: [
        { value: 'v2', label: 'v2 (/ws/v2/chat)' },
      ],
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

  function sanitizePolicyRecorder(value) {
    const base = { ...DEFAULTS.policy_recorder };
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'stop_on_tts_start')) {
      base.stop_on_tts_start = coerceBoolean(
        value.stop_on_tts_start,
        base.stop_on_tts_start,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'mute_send_during_tts')) {
      base.mute_send_during_tts = coerceBoolean(
        value.mute_send_during_tts,
        base.mute_send_during_tts,
      );
    }
    return base;
  }

  function sanitizePolicyInput(value) {
    const base = { ...DEFAULTS.policy_input };
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'require_hotword_to_start')) {
      base.require_hotword_to_start = coerceBoolean(
        value.require_hotword_to_start,
        base.require_hotword_to_start,
      );
    }
    return base;
  }

  function sanitizePolicyAsr(value) {
    const base = JSON.parse(JSON.stringify(DEFAULTS.policy_asr));
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'prearm_on_tts_end')) {
      base.prearm_on_tts_end = coerceBoolean(
        value.prearm_on_tts_end,
        base.prearm_on_tts_end,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'keep_stream_warm_ms')) {
      const numeric = Number(value.keep_stream_warm_ms);
      base.keep_stream_warm_ms = Number.isFinite(numeric) && numeric >= 0
        ? Math.round(numeric)
        : DEFAULTS.policy_asr.keep_stream_warm_ms;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'commit_on_vad_silence')) {
      base.commit_on_vad_silence = coerceBoolean(
        value.commit_on_vad_silence,
        base.commit_on_vad_silence,
      );
    }
    if (Object.prototype.hasOwnProperty.call(value, 'commit_silence_ms')) {
      const numeric = Number(value.commit_silence_ms);
      base.commit_silence_ms = Number.isFinite(numeric) && numeric >= 0
        ? Math.round(numeric)
        : DEFAULTS.policy_asr.commit_silence_ms;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'max_utterance_ms')) {
      const numeric = Number(value.max_utterance_ms);
      base.max_utterance_ms = Number.isFinite(numeric) && numeric >= 0
        ? Math.round(numeric)
        : DEFAULTS.policy_asr.max_utterance_ms;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'vendor')) {
      const vendorValue = value.vendor;
      const normalized = typeof vendorValue === 'object' && vendorValue
        ? vendorValue
        : {};
      const vendor = { ...base.vendor };
      if (Object.prototype.hasOwnProperty.call(normalized, 'primary')) {
        const primary = typeof normalized.primary === 'string'
          ? normalized.primary.trim().toLowerCase()
          : '';
        vendor.primary = ASR_VENDOR_OPTIONS.some((option) => option.value === primary)
          ? primary
          : vendor.primary;
      }
      if (Object.prototype.hasOwnProperty.call(normalized, 'secondary')) {
        const secondary = normalized.secondary;
        if (secondary === null) {
          vendor.secondary = null;
        } else if (typeof secondary === 'string') {
          const trimmed = secondary.trim().toLowerCase();
          vendor.secondary = ASR_VENDOR_OPTIONS.some((option) => option.value === trimmed)
            ? trimmed
            : vendor.secondary;
        }
      }
      base.vendor = vendor;
    }
    return base;
  }

  function sanitizePolicyAudio(value) {
    const base = JSON.parse(JSON.stringify(DEFAULTS.policy_audio));
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'pipeline')) {
      const pipelineValue = value.pipeline;
      const pipeline = { ...base.pipeline };
      if (pipelineValue && typeof pipelineValue === 'object') {
        const mode = typeof pipelineValue.mode === 'string'
          ? pipelineValue.mode.trim().toLowerCase()
          : '';
        if (AUDIO_PIPELINE_OPTIONS.some((option) => option.value === mode)) {
          pipeline.mode = mode;
        }
      }
      base.pipeline = pipeline;
    }
    return base;
  }

  function sanitizePolicyRouting(value) {
    const base = { ...DEFAULTS.policy_routing };
    if (!value || typeof value !== 'object') {
      return base;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'ws_version')) {
      const candidate = typeof value.ws_version === 'string' ? value.ws_version.trim() : '';
      base.ws_version = candidate && candidate.toLowerCase() === 'v2' ? 'v2' : base.ws_version;
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
    if (key === 'policy_recorder') {
      return sanitizePolicyRecorder(value);
    }
    if (key === 'policy_input') {
      return sanitizePolicyInput(value);
    }
    if (key === 'policy_asr') {
      return sanitizePolicyAsr(value);
    }
    if (key === 'policy_audio') {
      return sanitizePolicyAudio(value);
    }
    if (key === 'policy_routing') {
      return sanitizePolicyRouting(value);
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

  function getNestedValue(source, path) {
    if (!source || typeof source !== 'object' || !Array.isArray(path)) {
      return undefined;
    }
    return path.reduce((acc, key) => {
      if (!acc || typeof acc !== 'object') {
        return undefined;
      }
      return acc[key];
    }, source);
  }

  function setNestedValue(source, path, value) {
    const base = (source && typeof source === 'object') ? { ...source } : {};
    if (!Array.isArray(path) || !path.length) {
      return base;
    }
    let cursor = base;
    for (let i = 0; i < path.length - 1; i += 1) {
      const key = path[i];
      const next = cursor[key];
      if (!next || typeof next !== 'object') {
        cursor[key] = {};
      } else {
        cursor[key] = { ...next };
      }
      cursor = cursor[key];
    }
    cursor[path[path.length - 1]] = value;
    return base;
  }

  function getDefaultForField(field) {
    const settingKey = getSettingKey(field);
    const defaults = DEFAULTS[settingKey];
    if (!defaults) {
      return undefined;
    }
    if (Array.isArray(field.propPath) && field.propPath.length) {
      return getNestedValue(defaults, field.propPath);
    }
    if (field.prop && typeof defaults === 'object') {
      return defaults[field.prop];
    }
    return defaults;
  }

  function sanitizeFieldInput(field, value) {
    if (!field) {
      return value;
    }
    if (field.type === 'boolean') {
      const fallback = Boolean(getDefaultForField(field));
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
    const current = sanitizeSettingValue(settingKey, state.values[settingKey]);
    let value = current;
    if (Array.isArray(field.propPath) && field.propPath.length) {
      value = getNestedValue(current, field.propPath);
    } else if (field.prop && current && typeof current === 'object') {
      value = current[field.prop];
    }
    if (field.emptyAsNull && (value === null || typeof value === 'undefined')) {
      return '';
    }
    return sanitizeFieldInput(field, value);
  }

  function persistFieldValue(field, sanitizedValue) {
    const settingKey = getSettingKey(field);
    let payloadValue = sanitizedValue;
    let nextValue = sanitizedValue;
    if (field.emptyAsNull && nextValue === '') {
      nextValue = null;
    }
    if (field.prop) {
      const existing = sanitizeSettingValue(settingKey, state.values[settingKey]);
      payloadValue = Object.assign({}, existing, { [field.prop]: nextValue });
    } else if (Array.isArray(field.propPath) && field.propPath.length) {
      const existing = sanitizeSettingValue(settingKey, state.values[settingKey]);
      payloadValue = setNestedValue(existing, field.propPath, nextValue);
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
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_recorder')) {
      result.policy_recorder = sanitizePolicyRecorder(raw.policy_recorder);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_input')) {
      result.policy_input = sanitizePolicyInput(raw.policy_input);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_asr')) {
      result.policy_asr = sanitizePolicyAsr(raw.policy_asr);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_audio')) {
      result.policy_audio = sanitizePolicyAudio(raw.policy_audio);
    }
    if (Object.prototype.hasOwnProperty.call(raw, 'policy_routing')) {
      result.policy_routing = sanitizePolicyRouting(raw.policy_routing);
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
      POLICY_RECORDER: sanitizePolicyRecorder(values.policy_recorder),
      POLICY_INPUT: sanitizePolicyInput(values.policy_input),
      POLICY_ASR: sanitizePolicyAsr(values.policy_asr),
      POLICY_AUDIO: sanitizePolicyAudio(values.policy_audio),
      POLICY_ROUTING: sanitizePolicyRouting(values.policy_routing),
    });
  }

  function applyValues(values) {
    if (!values || typeof values !== 'object') {
      return;
    }
    const next = { ...state.values };
    Object.keys(values).forEach((key) => {
      const sanitized = sanitizeSettingValue(key, values[key]);
      if (
        key === 'policy_media' ||
        key === 'policy_capture' ||
        key === 'policy_recorder' ||
        key === 'policy_input' ||
        key === 'policy_asr' ||
        key === 'policy_audio' ||
        key === 'policy_routing'
      ) {
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
      policy_recorder: cfg.POLICY_RECORDER,
      policy_input: cfg.POLICY_INPUT,
      policy_asr: cfg.POLICY_ASR,
      policy_audio: cfg.POLICY_AUDIO,
      policy_routing: cfg.POLICY_ROUTING,
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
