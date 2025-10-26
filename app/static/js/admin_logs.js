(function () {
  'use strict';

  function readAppContext() {
    const el = document.getElementById('appContext');
    if (!el) return {};
    try {
      const raw = el.textContent || el.innerText || '{}';
      return JSON.parse(raw);
    } catch (err) {
      console.warn('Failed to parse app context', err);
      return {};
    }
  }

  const appContext = readAppContext();
  const userEmail = typeof appContext.userEmail === 'string' ? appContext.userEmail : null;

  const sidInput = document.getElementById('sidInput');
  const liveBtn = document.getElementById('liveBtn');
  const historyBtn = document.getElementById('historyBtn');
  const downloadBtn = document.getElementById('downloadBtn');
  const typesAllBtn = document.getElementById('typesAllBtn');
  const liveStatus = document.getElementById('liveStatus');
  const liveOutput = document.getElementById('liveOutput');
  const historyStatus = document.getElementById('historyStatus');
  const historyTableBody = document.getElementById('historyTableBody');
  const historyPrev = document.getElementById('historyPrev');
  const historyNext = document.getElementById('historyNext');
  const historyCount = document.getElementById('historyCount');
  const liveTab = document.getElementById('liveTab');
  const historyTab = document.getElementById('historyTab');
  const livePanel = document.getElementById('livePanel');
  const historyPanel = document.getElementById('historyPanel');
  const userLabel = document.getElementById('adminUserLabel');
  const drawer = document.getElementById('jsonDrawer');
  const drawerContent = document.getElementById('jsonDrawerContent');
  const drawerClose = document.getElementById('jsonDrawerClose');
  const drawerBackdrop = document.getElementById('jsonDrawerBackdrop');

  if (userLabel) {
    if (userEmail) {
      userLabel.textContent = `Signed in as ${userEmail}`;
    } else {
      userLabel.textContent = '';
      userLabel.setAttribute('aria-hidden', 'true');
      userLabel.style.display = 'none';
    }
  }

  const typeCheckboxes = Array.from(document.querySelectorAll('input[name="logType"]'));

  const liveState = {
    source: null,
    lines: [],
    limit: 500,
  };

  const historyState = {
    offset: 0,
    limit: 50,
    sid: null,
    events: [],
    hasMore: false,
    loading: false,
  };

  function selectedTypes() {
    return typeCheckboxes
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => checkbox.value)
      .filter(Boolean);
  }

  function syncTypesAllButton() {
    const allSelected = typeCheckboxes.every((checkbox) => checkbox.checked);
    typesAllBtn.classList.toggle('is-active', allSelected);
    typesAllBtn.setAttribute('aria-pressed', String(allSelected));
  }

  function ensureSid(context) {
    const value = (sidInput.value || '').trim();
    if (!value) {
      sidInput.focus({ preventScroll: false });
      if (context === 'live') {
        setLiveStatus('Enter a session id to start live tailing.');
      } else if (context === 'history') {
        setHistoryStatus('Enter a session id to load history.');
      }
      return null;
    }
    return value;
  }

  function buildTypesQuery(params) {
    const types = selectedTypes();
    if (types.length) {
      params.set('type', types.join(','));
    }
  }

  function setLiveStatus(message) {
    if (liveStatus) {
      liveStatus.textContent = message;
    }
  }

  function setHistoryStatus(message) {
    if (historyStatus) {
      historyStatus.textContent = message;
    }
  }

  function stopLive() {
    if (liveState.source) {
      liveState.source.close();
      liveState.source = null;
    }
    liveBtn.textContent = 'Live Tail';
    liveBtn.classList.remove('end');
    liveBtn.classList.add('start');
  }

  function appendLiveLine(rawLine) {
    if (typeof rawLine !== 'string' || !rawLine.trim()) {
      return;
    }
    let display = rawLine.trim();
    try {
      const parsed = JSON.parse(display);
      display = JSON.stringify(parsed, null, 2);
    } catch (err) {
      // leave as-is
    }
    liveState.lines.push(display);
    if (liveState.lines.length > liveState.limit) {
      liveState.lines.splice(0, liveState.lines.length - liveState.limit);
    }
    liveOutput.textContent = liveState.lines.join('\n\n');
    liveOutput.scrollTop = liveOutput.scrollHeight;
  }

  function startLive() {
    const sid = ensureSid('live');
    if (!sid) {
      return;
    }

    stopLive();
    liveState.lines = [];
    liveOutput.textContent = '';
    setLiveStatus('Connecting…');

    const url = new URL('/api/v1/admin/flow/live', window.location.origin);
    url.searchParams.set('sid', sid);
    buildTypesQuery(url.searchParams);

    try {
      const source = new EventSource(url.toString());
      liveState.source = source;
      liveBtn.textContent = 'Stop Live';
      liveBtn.classList.remove('start');
      liveBtn.classList.add('end');

      source.onopen = function () {
        setLiveStatus('Streaming events…');
      };

      source.onmessage = function (event) {
        appendLiveLine(event.data);
      };

      source.onerror = function () {
        setLiveStatus('Connection lost.');
        stopLive();
      };
    } catch (err) {
      console.error('Failed to open live stream', err);
      setLiveStatus('Unable to open live stream.');
      stopLive();
    }
  }

  function parseNdjson(text) {
    const events = [];
    for (const line of text.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        events.push(JSON.parse(trimmed));
      } catch (err) {
        console.warn('Skipping invalid JSON line', err);
      }
    }
    return events;
  }

  function renderHistory(events) {
    historyTableBody.innerHTML = '';
    historyState.events = events;

    if (!events.length) {
      const emptyRow = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.className = 'admin-logs__empty';
      cell.textContent = 'No events found for these filters.';
      emptyRow.appendChild(cell);
      historyTableBody.appendChild(emptyRow);
      historyCount.textContent = '0 events';
      return;
    }

    const fragment = document.createDocumentFragment();
    events.forEach((event, index) => {
      const row = document.createElement('tr');
      row.dataset.index = String(index);

      const tsCell = document.createElement('td');
      const typeCell = document.createElement('td');
      const summaryCell = document.createElement('td');

      const tsValue = typeof event.ts_ms === 'number' ? event.ts_ms : event.ts_ms || '—';
      const typeValue = event.type || '—';
      let summaryValue = event.summary || '';

      if (!summaryValue) {
        if (event.meta && typeof event.meta === 'object') {
          summaryValue = JSON.stringify(event.meta);
        } else if (event.text) {
          summaryValue = String(event.text).slice(0, 160);
        } else {
          summaryValue = typeValue;
        }
      }

      tsCell.textContent = tsValue;
      typeCell.textContent = typeValue;
      summaryCell.textContent = summaryValue;

      row.appendChild(tsCell);
      row.appendChild(typeCell);
      row.appendChild(summaryCell);
      fragment.appendChild(row);
    });

    historyTableBody.appendChild(fragment);
    historyCount.textContent = `${events.length} event${events.length === 1 ? '' : 's'}`;
  }

  function toggleDrawer(open, event) {
    if (!drawer || !drawerContent || !drawerBackdrop) return;
    if (open) {
      drawer.hidden = false;
      drawerBackdrop.hidden = false;
      drawer.classList.add('open');
      drawerBackdrop.classList.add('open');
      drawer.setAttribute('aria-hidden', 'false');
      drawerBackdrop.setAttribute('aria-hidden', 'false');
      if (event) {
        try {
          drawerContent.textContent = JSON.stringify(event, null, 2);
        } catch (err) {
          drawerContent.textContent = 'Unable to render event payload.';
        }
      }
      drawerContent.scrollTop = 0;
      drawer.focus({ preventScroll: true });
    } else {
      drawer.classList.remove('open');
      drawerBackdrop.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
      drawerBackdrop.setAttribute('aria-hidden', 'true');
      drawer.hidden = true;
      drawerBackdrop.hidden = true;
    }
  }

  function openDrawerForRow(index) {
    const event = historyState.events[index];
    if (!event) return;
    toggleDrawer(true, event);
  }

  async function loadHistory(resetOffset) {
    const sid = ensureSid('history');
    if (!sid || historyState.loading) {
      return;
    }

    if (resetOffset) {
      historyState.offset = 0;
    }

    historyState.loading = true;
    setHistoryStatus('Loading history…');
    historyPrev.disabled = historyState.offset <= 0;
    historyNext.disabled = true;

    const url = new URL('/api/v1/admin/flow/trace', window.location.origin);
    url.searchParams.set('sid', sid);
    url.searchParams.set('offset', String(historyState.offset));
    url.searchParams.set('limit', String(historyState.limit));
    buildTypesQuery(url.searchParams);

    try {
      const response = await fetch(url.toString(), {
        headers: {
          accept: 'application/x-ndjson, application/json',
        },
      });

      if (!response.ok) {
        setHistoryStatus(`Failed to load history (${response.status}).`);
        historyState.loading = false;
        return;
      }

      const body = await response.text();
      const events = parseNdjson(body);
      historyState.sid = sid;
      historyState.hasMore = events.length >= historyState.limit;
      renderHistory(events);
      setHistoryStatus(events.length ? 'Select a row to inspect the event JSON.' : 'No events for this page.');
      historyPrev.disabled = historyState.offset <= 0;
      historyNext.disabled = !historyState.hasMore;
    } catch (err) {
      console.error('Failed to load history', err);
      setHistoryStatus('Unable to load history. Check the console for details.');
      historyPrev.disabled = historyState.offset <= 0;
      historyNext.disabled = !historyState.hasMore;
    } finally {
      historyState.loading = false;
    }
  }

  function goToPreviousPage() {
    if (historyState.offset <= 0) return;
    historyState.offset = Math.max(0, historyState.offset - historyState.limit);
    loadHistory(false);
  }

  function goToNextPage() {
    if (!historyState.hasMore) return;
    historyState.offset += historyState.limit;
    loadHistory(false);
  }

  function activateTab(target) {
    if (target === 'history') {
      historyTab.setAttribute('aria-selected', 'true');
      liveTab.setAttribute('aria-selected', 'false');
      historyPanel.hidden = false;
      livePanel.hidden = true;
      stopLive();
    } else {
      liveTab.setAttribute('aria-selected', 'true');
      historyTab.setAttribute('aria-selected', 'false');
      livePanel.hidden = false;
      historyPanel.hidden = true;
    }
  }

  liveBtn.addEventListener('click', () => {
    if (liveState.source) {
      stopLive();
      setLiveStatus('Live stream stopped.');
    } else {
      activateTab('live');
      startLive();
    }
  });

  historyBtn.addEventListener('click', () => {
    activateTab('history');
    loadHistory(true);
  });

  downloadBtn.addEventListener('click', () => {
    const sid = ensureSid('history');
    if (!sid) return;
    const url = new URL('/api/v1/admin/flow/zip', window.location.origin);
    url.searchParams.set('sid', sid);
    buildTypesQuery(url.searchParams);
    window.open(url.toString(), '_blank', 'noopener');
  });

  liveTab.addEventListener('click', () => activateTab('live'));
  historyTab.addEventListener('click', () => activateTab('history'));

  historyPrev.addEventListener('click', goToPreviousPage);
  historyNext.addEventListener('click', goToNextPage);

  historyTableBody.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const row = target.closest('tr');
    if (!row) return;
    const index = Number(row.dataset.index);
    if (Number.isFinite(index)) {
      openDrawerForRow(index);
    }
  });

  if (drawerClose) {
    drawerClose.addEventListener('click', () => toggleDrawer(false));
  }

  if (drawerBackdrop) {
    drawerBackdrop.addEventListener('click', () => toggleDrawer(false));
  }

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && drawer && !drawer.hidden) {
      toggleDrawer(false);
    }
  });

  typesAllBtn.addEventListener('click', () => {
    const shouldSelectAll = !typeCheckboxes.every((checkbox) => checkbox.checked);
    typeCheckboxes.forEach((checkbox) => {
      checkbox.checked = shouldSelectAll;
    });
    syncTypesAllButton();
  });

  typeCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      syncTypesAllButton();
      if (liveState.source) {
        setLiveStatus('Filters updated — restart live tail to apply.');
      }
    });
  });

  window.addEventListener('beforeunload', () => {
    stopLive();
  });

  syncTypesAllButton();
})();
