const SUBTAB_DEFAULT = 'effective';
const AUTO_LABEL = 'Auto (derived)';

const state = {
  initialized: false,
  sessionId: '',
  personaId: '',
  tenantId: 'default',
  data: null,
  sessions: [],
  meta: { personas: [], tenants: [] },
  activeSubtab: SUBTAB_DEFAULT,
  loading: false,
  toastTimer: null,
};

function $(root, selector) {
  return root ? root.querySelector(selector) : null;
}

function createChip(label, value) {
  const chip = document.createElement('span');
  chip.className = 'policy-chip';
  chip.textContent = value ? `${label}: ${value}` : label;
  return chip;
}

function showToast(root, message) {
  const toast = $(root, '#policy-toast');
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  if (state.toastTimer) {
    clearTimeout(state.toastTimer);
  }
  state.toastTimer = setTimeout(() => {
    toast.hidden = true;
    state.toastTimer = null;
  }, 4000);
}

function setLoading(root, isLoading) {
  state.loading = !!isLoading;
  if (root) {
    root.dataset.loading = state.loading ? '1' : '0';
  }
  const status = $(root, '#policy-status');
  if (status && state.loading) {
    status.textContent = 'Loading…';
  }
}

function formatValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch (err) {
    return String(value);
  }
}

function hasDiffForPath(diffs, path) {
  if (!diffs || !diffs.size || !path) {
    return false;
  }
  for (const diffPath of diffs) {
    if (diffPath === path) return true;
    if (diffPath.startsWith(path + '.')) return true;
  }
  return false;
}

function renderJsonViewer(container, data, options = {}) {
  if (!container) return;
  container.classList.remove('policy-json-empty');
  container.innerHTML = '';
  if (!data || typeof data !== 'object' || Array.isArray(data) && !data.length || !Object.keys(data).length) {
    container.classList.add('policy-json-empty');
    container.textContent = '—';
    return;
  }
  const { diffs, expandDepth = 2 } = options;

  function renderNode(key, value, path, depth) {
    const isObject = value && typeof value === 'object';
    if (isObject) {
      const details = document.createElement('details');
      details.className = 'policy-json-node';
      if (depth < expandDepth) {
        details.open = true;
      }
      if (hasDiffForPath(diffs, path)) {
        details.classList.add('has-diff');
      }
      const summary = document.createElement('summary');
      summary.className = 'policy-json-summary';
      const keySpan = document.createElement('span');
      keySpan.className = 'policy-json-key';
      keySpan.textContent = key;
      summary.appendChild(keySpan);
      details.appendChild(summary);
      const body = document.createElement('div');
      body.className = 'policy-json-children';
      if (Array.isArray(value)) {
        value.forEach((child, index) => {
          const childPath = path ? `${path}.${index}` : String(index);
          const label = `[${index}]`;
          body.appendChild(renderNode(label, child, childPath, depth + 1));
        });
        if (!value.length) {
          const empty = document.createElement('div');
          empty.className = 'policy-json-leaf';
          empty.textContent = '(empty array)';
          body.appendChild(empty);
        }
      } else {
        const entries = Object.entries(value);
        if (!entries.length) {
          const empty = document.createElement('div');
          empty.className = 'policy-json-leaf';
          empty.textContent = '(empty object)';
          body.appendChild(empty);
        }
        for (const [childKey, childValue] of entries) {
          const childPath = path ? `${path}.${childKey}` : childKey;
          body.appendChild(renderNode(childKey, childValue, childPath, depth + 1));
        }
      }
      details.appendChild(body);
      return details;
    }

    const row = document.createElement('div');
    row.className = 'policy-json-leaf';
    if (hasDiffForPath(diffs, path)) {
      row.classList.add('has-diff');
    }
    const keySpan = document.createElement('span');
    keySpan.className = 'policy-json-key';
    keySpan.textContent = key;
    row.appendChild(keySpan);
    const sep = document.createElement('span');
    sep.className = 'policy-json-sep';
    sep.textContent = ':';
    row.appendChild(sep);
    const valueSpan = document.createElement('span');
    valueSpan.className = 'policy-json-value';
    valueSpan.textContent = formatValue(value);
    row.appendChild(valueSpan);
    return row;
  }

  const root = document.createElement('div');
  root.className = 'policy-json-root';
  const entries = Array.isArray(data) ? data.entries() : Object.entries(data);
  if (Array.isArray(data)) {
    data.forEach((child, index) => {
      const node = renderNode(`[${index}]`, child, String(index), 0);
      root.appendChild(node);
    });
  } else {
    for (const [key, value] of entries) {
      const node = renderNode(key, value, key, 0);
      root.appendChild(node);
    }
  }
  container.appendChild(root);
}

function collectDiffPaths(base, override, prefix = '') {
  const diffs = new Set();
  if (!override || typeof override !== 'object') {
    return diffs;
  }
  const isArray = Array.isArray(override);
  if (isArray) {
    override.forEach((value, index) => {
      const nextPath = prefix ? `${prefix}.${index}` : String(index);
      const baseValue = Array.isArray(base) ? base[index] : undefined;
      if (value && typeof value === 'object') {
        if (!(baseValue && typeof baseValue === 'object')) {
          diffs.add(nextPath);
        }
        collectDiffPaths(baseValue, value, nextPath).forEach((p) => diffs.add(p));
      } else {
        diffs.add(nextPath);
      }
    });
    return diffs;
  }
  Object.keys(override).forEach((key) => {
    const nextPath = prefix ? `${prefix}.${key}` : key;
    const value = override[key];
    const baseValue = base && typeof base === 'object' ? base[key] : undefined;
    if (value && typeof value === 'object') {
      if (!(baseValue && typeof baseValue === 'object')) {
        diffs.add(nextPath);
      }
      collectDiffPaths(baseValue, value, nextPath).forEach((p) => diffs.add(p));
    } else {
      diffs.add(nextPath);
    }
  });
  return diffs;
}

function renderChips(root, payload) {
  const container = $(root, '#policy-chip-container');
  if (!container) return;
  container.innerHTML = '';
  const resolved = payload?.resolved_context || {};
  const chips = [];
  if (resolved.persona_id) {
    chips.push(createChip('Persona', resolved.persona_id));
  }
  if (resolved.tenant_id) {
    chips.push(createChip('Tenant', resolved.tenant_id));
  }
  if (resolved.session_id) {
    chips.push(createChip('Session', resolved.session_id));
  }
  if (payload?.policy_version) {
    chips.push(createChip('Version', payload.policy_version));
  }
  if (!chips.length) {
    const chip = document.createElement('span');
    chip.className = 'policy-chip';
    chip.textContent = 'Defaults only';
    chips.push(chip);
  }
  chips.forEach((chip) => container.appendChild(chip));
}

function renderStatus(root, payload) {
  const status = $(root, '#policy-status');
  if (!status) return;
  if (!payload) {
    status.textContent = '';
    return;
  }
  const resolved = payload.resolved_context || {};
  const parts = [];
  if (resolved.session_id) parts.push(`Session ${resolved.session_id}`);
  if (resolved.persona_id) parts.push(`Persona ${resolved.persona_id}`);
  if (resolved.tenant_id) parts.push(`Tenant ${resolved.tenant_id}`);
  const context = parts.length ? parts.join(' · ') : 'Defaults';
  status.textContent = `${context} • Fetched ${new Date().toLocaleTimeString()}`;
}

function renderPolicy(root, payload) {
  if (!payload) return;
  const effective = payload.effective_policy || {};
  const defaults = payload.layers?.defaults || {};
  const personaLayer = payload.layers?.persona || {};
  const tenantLayer = payload.layers?.tenant || {};
  const sessionLayer = payload.layers?.session || {};

  const diffPersona = collectDiffPaths(defaults, personaLayer);
  const diffTenant = collectDiffPaths(defaults, tenantLayer);
  const diffSession = collectDiffPaths(defaults, sessionLayer);

  renderJsonViewer($(root, '#policy-effective-json'), effective, { expandDepth: 3 });
  renderJsonViewer($(root, '#policy-layer-defaults'), defaults, { expandDepth: 2 });
  renderJsonViewer($(root, '#policy-layer-persona'), personaLayer, {
    diffs: diffPersona,
    expandDepth: 2,
  });
  renderJsonViewer($(root, '#policy-layer-tenant'), tenantLayer, {
    diffs: diffTenant,
    expandDepth: 2,
  });
  renderJsonViewer($(root, '#policy-layer-session'), sessionLayer, {
    diffs: diffSession,
    expandDepth: 2,
  });

  const emptyNotice = $(root, '#policy-empty');
  const hasPersona = personaLayer && Object.keys(personaLayer).length;
  const hasTenant = tenantLayer && Object.keys(tenantLayer).length;
  const hasSession = sessionLayer && Object.keys(sessionLayer).length;
  if (emptyNotice) {
    emptyNotice.hidden = !!(hasPersona || hasTenant || hasSession);
  }
}

function syncOptions(select, options) {
  if (!select) return;
  const current = select.value;
  Array.from(select.querySelectorAll('option[data-dynamic="1"]')).forEach((opt) => opt.remove());
  options.forEach((opt) => {
    const optionEl = document.createElement('option');
    optionEl.value = opt.id;
    optionEl.textContent = opt.label || opt.id;
    optionEl.dataset.dynamic = '1';
    select.appendChild(optionEl);
  });
  if (current) {
    const hasMatch = Array.from(select.options).some((opt) => opt.value === current);
    if (hasMatch) {
      select.value = current;
    }
  }
}

function syncMeta(root, payload) {
  state.meta.personas = Array.isArray(payload?.meta?.personas) ? payload.meta.personas : [];
  state.meta.tenants = Array.isArray(payload?.meta?.tenants) ? payload.meta.tenants : [];
  const personaSelect = $(root, '#policy-persona-select');
  const tenantSelect = $(root, '#policy-tenant-select');
  if (personaSelect) {
    const auto = personaSelect.querySelector('option[value=""]');
    if (auto) auto.textContent = AUTO_LABEL;
    syncOptions(personaSelect, state.meta.personas);
    personaSelect.value = state.personaId || '';
  }
  if (tenantSelect) {
    const auto = tenantSelect.querySelector('option[value=""]');
    if (auto) auto.textContent = AUTO_LABEL;
    syncOptions(tenantSelect, state.meta.tenants);
    if (state.tenantId && tenantSelect.value !== state.tenantId) {
      tenantSelect.value = state.tenantId;
    }
  }
}

async function loadSessionsList(root) {
  try {
    const resp = await fetch('/api/v1/admin/sessions', { credentials: 'include' });
    if (!resp.ok) return;
    const payload = await resp.json();
    const sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
    state.sessions = sessions;
    const dataList = $(root, '#policy-session-options');
    if (dataList) {
      dataList.innerHTML = '';
      sessions.forEach((session) => {
        const option = document.createElement('option');
        const sessionId = (session?.id || '').toString();
        option.value = sessionId;
        const persona = session?.persona_id ? ` · persona=${session.persona_id}` : '';
        const tenant = session?.tenant_id ? ` · tenant=${session.tenant_id}` : '';
        option.label = `${sessionId}${persona}${tenant}`;
        dataList.appendChild(option);
      });
    }
  } catch (err) {
    // ignore list errors
  }
}

function getQueryParams() {
  const params = new URLSearchParams();
  if (state.sessionId) params.set('session_id', state.sessionId);
  if (state.personaId) params.set('persona_id', state.personaId);
  if (state.tenantId) params.set('tenant_id', state.tenantId);
  if (!params.has('session_id') && !params.has('persona_id') && !params.has('tenant_id')) {
    params.set('tenant_id', 'default');
  }
  return params;
}

async function fetchPolicy(root, { forceRefresh = false } = {}) {
  setLoading(root, true);
  try {
    const params = getQueryParams();
    if (forceRefresh) {
      params.set('refresh', '1');
    }
    const url = `/api/v1/policy/effective?${params.toString()}`;
    const resp = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const payload = await resp.json();
    state.data = payload;
    const resolved = payload?.resolved_context || {};
    if (resolved.persona_id) {
      state.personaId = resolved.persona_id;
    }
    if (resolved.tenant_id) {
      state.tenantId = resolved.tenant_id;
    }
    renderChips(root, payload);
    renderStatus(root, payload);
    syncMeta(root, payload);
    renderPolicy(root, payload);
  } catch (err) {
    showToast(root, `Failed to load policy: ${err?.message || err}`);
  } finally {
    setLoading(root, false);
  }
}

function handleApply(root) {
  const sessionInput = $(root, '#policy-session-input');
  const personaSelect = $(root, '#policy-persona-select');
  const tenantSelect = $(root, '#policy-tenant-select');
  state.sessionId = sessionInput ? sessionInput.value.trim() : '';
  state.personaId = personaSelect ? personaSelect.value : '';
  state.tenantId = tenantSelect ? tenantSelect.value : '';
  if (!state.sessionId && !state.personaId && !state.tenantId) {
    state.tenantId = 'default';
    if (tenantSelect) tenantSelect.value = 'default';
  }
  fetchPolicy(root);
}

function handleRefresh(root) {
  fetchPolicy(root, { forceRefresh: true });
}

function handleCopy(root) {
  if (!state.data?.effective_policy) {
    showToast(root, 'Nothing to copy yet.');
    return;
  }
  const json = JSON.stringify(state.data.effective_policy, null, 2);
  if (navigator?.clipboard?.writeText) {
    navigator.clipboard.writeText(json).then(() => {
      showToast(root, 'Effective policy copied to clipboard.');
    }).catch(() => {
      showToast(root, 'Clipboard copy failed.');
    });
  } else {
    try {
      const temp = document.createElement('textarea');
      temp.value = json;
      document.body.appendChild(temp);
      temp.select();
      document.execCommand('copy');
      document.body.removeChild(temp);
      showToast(root, 'Effective policy copied to clipboard.');
    } catch (err) {
      showToast(root, 'Clipboard copy failed.');
    }
  }
}

function handleDownload(root) {
  if (!state.data?.effective_policy) {
    showToast(root, 'Nothing to download yet.');
    return;
  }
  const blob = new Blob([JSON.stringify(state.data.effective_policy, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'interaction_policy.json';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    a.remove();
  }, 0);
}

function handleLogs(root) {
  const resolved = state.data?.resolved_context || {};
  const sid = resolved.session_id || state.sessionId;
  if (!sid) {
    showToast(root, 'Select a session id to open logs.');
    return;
  }
  const url = `/api/v1/admin/logs-ui?session_id=${encodeURIComponent(sid)}`;
  window.open(url, '_blank', 'noopener');
}

function switchSubtab(root, target) {
  if (!root || !target) return;
  const buttons = root.querySelectorAll('.policy-subtab');
  const panels = root.querySelectorAll('.policy-tabpanel');
  buttons.forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.subtab === target);
  });
  panels.forEach((panel) => {
    panel.classList.toggle('active', panel.id === `policy-subtab-${target}`);
  });
  state.activeSubtab = target;
}

function bindEvents(root) {
  const applyBtn = $(root, '#policy-apply');
  const refreshBtn = $(root, '#policy-refresh');
  const copyBtn = $(root, '#policy-copy');
  const downloadBtn = $(root, '#policy-download');
  const logsBtn = $(root, '#policy-logs');
  const subtabButtons = root ? root.querySelectorAll('.policy-subtab') : [];

  if (applyBtn) applyBtn.addEventListener('click', () => handleApply(root));
  if (refreshBtn) refreshBtn.addEventListener('click', () => handleRefresh(root));
  if (copyBtn) copyBtn.addEventListener('click', () => handleCopy(root));
  if (downloadBtn) downloadBtn.addEventListener('click', () => handleDownload(root));
  if (logsBtn) logsBtn.addEventListener('click', () => handleLogs(root));
  subtabButtons.forEach((button) => {
    button.addEventListener('click', () => {
      switchSubtab(root, button.dataset.subtab || SUBTAB_DEFAULT);
    });
  });
}

function initializePanel() {
  const root = document.getElementById('tab-policy');
  if (!root || state.initialized) return;
  state.initialized = true;
  bindEvents(root);
  loadSessionsList(root);
  // Ensure selects start with default tenant option if available.
  const tenantSelect = $(root, '#policy-tenant-select');
  if (tenantSelect) {
    tenantSelect.value = state.tenantId;
  }
  fetchPolicy(root);
}

document.addEventListener('DOMContentLoaded', initializePanel);
