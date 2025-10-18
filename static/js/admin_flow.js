const POLL_MIN_MS = 750;
const POLL_MAX_MS = 1000;
const MAX_SESSION_RESULTS = 20;
const MAX_META_LENGTH = 48;
const LEVELS = ["flow", "transition", "debug", "raw"];

const PHASE_COLORS = {
  session: "#7aa0ff",
  turn: "#62ddb8",
  guardrail: "#f78ed2",
  confirm: "#fbbf24",
  nlu: "#c084fc",
  nlg: "#f97316",
  tts: "#22d3ee",
  audio: "#38bdf8",
  evidence: "#facc15",
  ops: "#f97316",
  policy: "#fbbf24",
  default: "#717b9b",
};

const GROUP_LABELS = {
  phase: "Phase",
  turn: "Turn",
  chronological: "Timeline",
};

const state = {
  root: null,
  sessionId: "",
  sessions: [],
  sessionQuery: "",
  events: new Map(),
  expanded: new Set(),
  bookmarks: [],
  pollTimer: null,
  pollSinceMs: 0,
  levels: new Set(["flow", "transition"]),
  grouping: "chronological",
  live: false,
  filterText: "",
  filterChips: [],
  turnFilter: "",
  matchedIds: new Set(),
  visibleIds: new Set(),
  drawerEventId: null,
  lastFetchedAt: null,
  loading: false,
};

const els = {};
let sessionsDebounce = null;
let popoverNode = null;

function init() {
  const root = document.getElementById("admin-flow-app");
  if (!root) {
    return;
  }
  state.root = root;

  els.sessionInput = document.getElementById("flowSessionInput");
  els.sessionOptions = document.getElementById("flowSessionOptions");
  els.sessionResults = document.getElementById("flowSessionResults");
  els.sessionHint = document.getElementById("flowSessionHint");
  els.sessionRefresh = document.getElementById("flowSessionRefresh");
  els.levelContainer = root.querySelector("[data-ref=\"level-toggles\"]");
  els.groupContainer = root.querySelector("[data-ref=\"grouping\"]");
  els.filterInput = document.getElementById("flowFilterInput");
  els.turnInput = document.getElementById("flowTurnInput");
  els.filterChips = document.getElementById("flowFilterChips");
  els.tailState = document.getElementById("flowTailState");
  els.tailToggle = document.getElementById("flowTailToggle");
  els.tailStep = document.getElementById("flowTailStep");
  els.timeline = document.getElementById("flowTimeline");
  els.exportFull = document.getElementById("flowExportFull");
  els.exportRedacted = document.getElementById("flowExportRedacted");
  els.copyLink = document.getElementById("flowCopyLink");
  els.handoff = document.getElementById("flowHandoff");
  els.drawer = document.getElementById("flowDrawer");
  els.drawerTitle = document.getElementById("flowDrawerTitle");
  els.drawerMeta = document.getElementById("flowDrawerMeta");
  els.drawerJson = document.getElementById("flowDrawerJson");
  els.drawerRelated = document.getElementById("flowDrawerRelated");
  els.drawerCopy = document.getElementById("flowDrawerCopy");
  els.drawerClose = document.getElementById("flowDrawerClose");

  bindEvents();
  hydrateFromLocation();
  refreshSessions();
  renderSessions();
  renderFilters();
  renderTail();
  renderTimeline();
  renderDrawer();

  if (state.sessionId) {
    fetchTrace({ reset: true });
    goLive();
  }
}

document.addEventListener("DOMContentLoaded", init);

function bindEvents() {
  if (els.sessionInput) {
    els.sessionInput.addEventListener("input", onSessionInput);
    els.sessionInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        const value = ev.currentTarget.value.trim();
        if (value) {
          selectSession(value);
        }
      }
    });
  }

  if (els.sessionResults) {
    els.sessionResults.addEventListener("click", (ev) => {
      const item = ev.target.closest("li[data-session-id]");
      if (!item) return;
      const sid = item.getAttribute("data-session-id");
      if (sid) {
        selectSession(sid);
      }
    });
  }

  if (els.sessionRefresh) {
    els.sessionRefresh.addEventListener("click", () => refreshSessions({ force: true }));
  }

  if (els.levelContainer) {
    els.levelContainer.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-level]");
      if (!btn || btn.disabled) return;
      const level = btn.getAttribute("data-level");
      toggleLevel(level);
    });
  }

  if (els.groupContainer) {
    els.groupContainer.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-group]");
      if (!btn) return;
      setGrouping(btn.getAttribute("data-group"));
    });
  }

  if (els.filterInput) {
    els.filterInput.addEventListener("input", (ev) => {
      state.filterText = ev.currentTarget.value.trim();
      scheduleRender();
    });
  }

  if (els.turnInput) {
    els.turnInput.addEventListener("input", (ev) => {
      const value = ev.currentTarget.value;
      state.turnFilter = value ? String(value).trim() : "";
      scheduleRender();
    });
  }

  if (els.filterChips) {
    els.filterChips.addEventListener("click", (ev) => {
      const closeBtn = ev.target.closest("button[data-chip-index]");
      if (!closeBtn) return;
      const idx = Number(closeBtn.getAttribute("data-chip-index"));
      if (!Number.isNaN(idx)) {
        state.filterChips.splice(idx, 1);
        scheduleRender();
      }
    });
  }

  if (els.tailToggle) {
    els.tailToggle.addEventListener("click", () => {
      if (state.live) {
        pauseLive();
      } else {
        goLive(true);
      }
    });
  }

  if (els.tailStep) {
    els.tailStep.addEventListener("click", () => fetchTrace({ reset: false }));
  }

  if (els.exportFull) {
    els.exportFull.addEventListener("click", () => downloadExport({ redacted: false }));
  }

  if (els.exportRedacted) {
    els.exportRedacted.addEventListener("click", () => downloadExport({ redacted: true }));
  }

  if (els.copyLink) {
    els.copyLink.addEventListener("click", () => copyLink());
  }

  if (els.handoff) {
    els.handoff.addEventListener("click", () => handoffToChatGPT());
  }

  if (els.timeline) {
    els.timeline.addEventListener("click", onTimelineClick);
    els.timeline.addEventListener("contextmenu", onTimelineContext);
  }

  if (els.drawerClose) {
    els.drawerClose.addEventListener("click", () => openDrawer(null));
  }
  if (els.drawerCopy) {
    els.drawerCopy.addEventListener("click", () => copyDrawerJson());
  }
  if (els.drawerRelated) {
    els.drawerRelated.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-target]");
      if (!btn) return;
      const target = btn.getAttribute("data-target");
      if (target) {
        focusEvent(target);
      }
    });
  }

  document.addEventListener("click", (ev) => {
    if (!popoverNode) return;
    if (ev.target.closest(".popover") || ev.target.closest(".raw-batch-btn")) {
      return;
    }
    hidePopover();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      hidePopover();
    }
  });
}

function hydrateFromLocation() {
  const url = new URL(window.location.href);
  let sessionId = url.searchParams.get("session_id") || "";

  const hash = window.location.hash || "";
  if (!sessionId && hash.startsWith("#/admin/flow")) {
    const qIndex = hash.indexOf("?");
    if (qIndex >= 0) {
      const hashParams = new URLSearchParams(hash.slice(qIndex + 1));
      sessionId = hashParams.get("session_id") || sessionId;
      const levels = hashParams.get("levels");
      if (levels) {
        setLevelsFromString(levels);
      }
      const group = hashParams.get("group");
      if (group) {
        state.grouping = group;
      }
      const filter = hashParams.get("filter");
      if (filter) {
        state.filterText = filter;
        if (els.filterInput) els.filterInput.value = filter;
      }
      const turn = hashParams.get("turn");
      if (turn) {
        state.turnFilter = turn;
        if (els.turnInput) els.turnInput.value = turn;
      }
      const chipsParam = hashParams.get("chips");
      if (chipsParam) {
        state.filterChips = parseChipParam(chipsParam);
      }
    }
  }

  const levelsParam = url.searchParams.get("levels");
  if (levelsParam) {
    setLevelsFromString(levelsParam);
  }

  const groupParam = url.searchParams.get("group");
  if (groupParam) {
    state.grouping = groupParam;
  }

  const filterParam = url.searchParams.get("filter");
  if (filterParam) {
    state.filterText = filterParam;
    if (els.filterInput) els.filterInput.value = filterParam;
  }

  const turnParam = url.searchParams.get("turn");
  if (turnParam) {
    state.turnFilter = turnParam;
    if (els.turnInput) els.turnInput.value = turnParam;
  }

  const chipsParam = url.searchParams.get("chips");
  if (chipsParam) {
    state.filterChips = parseChipParam(chipsParam);
  }

  if (sessionId) {
    state.sessionId = sessionId;
    if (els.sessionInput) {
      els.sessionInput.value = sessionId;
    }
  }

  reflectLevelButtons();
  reflectGroupingButtons();
}

function setLevelsFromString(raw) {
  const allowed = new Set(LEVELS);
  const parsed = new Set(["flow"]);
  for (const part of String(raw).split(",")) {
    const trimmed = part.trim();
    if (allowed.has(trimmed)) {
      parsed.add(trimmed);
    }
  }
  if (!parsed.has("flow")) parsed.add("flow");
  state.levels = parsed;
  reflectLevelButtons();
}

function refreshSessions(opts = {}) {
  const { force = false } = opts;
  if (sessionsDebounce) {
    clearTimeout(sessionsDebounce);
    sessionsDebounce = null;
  }
  sessionsDebounce = setTimeout(async () => {
    try {
      const params = new URLSearchParams();
      if (state.sessionQuery) {
        params.set("q", state.sessionQuery);
      }
      const resp = await fetch(`/api/v1/flow/sessions?${params.toString()}`, {
        credentials: "include",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const payload = await resp.json();
      const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
      state.sessions = sessions;
      renderSessions();
    } catch (err) {
      console.warn("[flow] sessions fetch failed", err);
      setHint("Failed to load sessions.");
    }
  }, force ? 0 : 160);
}

function onSessionInput(ev) {
  const value = ev.currentTarget.value.trim();
  state.sessionQuery = value;
  renderSessions();
  refreshSessions();
}

function renderSessions() {
  if (!els.sessionResults) return;
  const query = state.sessionQuery.toLowerCase();
  const sessions = state.sessions
    .filter((item) => {
      if (!query) return true;
      return item.session_id.toLowerCase().includes(query);
    })
    .slice(0, MAX_SESSION_RESULTS);

  const options = sessions
    .map((item) => `<option value="${escapeHtml(item.session_id)}"></option>`) 
    .join("");
  if (els.sessionOptions) {
    els.sessionOptions.innerHTML = options;
  }

  if (!sessions.length) {
    els.sessionResults.innerHTML = "";
    return;
  }

  els.sessionResults.innerHTML = sessions
    .map((item) => renderSessionListItem(item))
    .join("");
}

function renderSessionListItem(item) {
  const isActive = item.session_id === state.sessionId;
  const timeText = typeof item.last_event_ms === "number" ? `${item.last_event_ms.toLocaleString()} ms` : "—";
  const typeText = item.last_type ? item.last_type.replace(/_/g, " ") : "—";
  return `
    <li data-session-id="${escapeAttr(item.session_id)}" class="${isActive ? "active" : ""}">
      <div class="session-id">${escapeHtml(item.session_id)}</div>
      <div class="session-meta">
        <span>${escapeHtml(typeText)}</span>
        <span>${escapeHtml(timeText)}</span>
        <span>${item.event_count || 0} evts</span>
      </div>
    </li>`;
}

function selectSession(sessionId) {
  if (!sessionId) return;
  state.sessionId = sessionId;
  state.pollSinceMs = 0;
  state.events.clear();
  state.expanded.clear();
  state.bookmarks = [];
  state.matchedIds = new Set();
  state.visibleIds = new Set();
  state.drawerEventId = null;
  if (els.sessionInput) {
    els.sessionInput.value = sessionId;
  }
  setHint(`Session ${sessionId}`);
  renderSessions();
  renderTimeline();
  renderDrawer();
  fetchTrace({ reset: true }).then(() => {
    goLive(true);
  });
  updateHistory();
}

function fetchTrace({ reset }) {
  if (!state.sessionId) return Promise.resolve();
  if (state.loading && !reset) return Promise.resolve();
  if (reset) {
    state.pollSinceMs = 0;
  }
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  if (state.pollSinceMs && !reset) {
    params.set("since_ms", String(state.pollSinceMs));
  }
  params.set("expand", "all");
  const levelsList = Array.from(state.levels);
  params.set("levels", levelsList.join(","));
  params.set("limit", "400");

  state.loading = true;
  return fetch(`/api/v1/flow/trace?${params.toString()}`, { credentials: "include" })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    })
    .then((payload) => {
      const events = Array.isArray(payload.events) ? payload.events : [];
      ingestEvents(events);
      const nextSince = payload.next_since_ms;
      if (typeof nextSince === "number" && nextSince >= 0) {
        state.pollSinceMs = nextSince;
      }
      state.lastFetchedAt = Date.now();
      renderTimeline();
      renderDrawer();
    })
    .catch((err) => {
      console.warn("[flow] trace fetch failed", err);
      setHint("Failed to fetch flow data.");
    })
    .finally(() => {
      state.loading = false;
    });
}

function ingestEvents(events) {
  const queue = Array.isArray(events) ? [...events] : [];
  while (queue.length) {
    const event = queue.shift();
    if (!event || typeof event !== "object") continue;
    const { children, ...rest } = event;
    const childIds = Array.isArray(children) ? children.map((child) => child && child.id).filter(Boolean) : [];
    const normalized = normalizeEvent(rest, childIds);
    const existing = state.events.get(normalized.id);
    if (existing) {
      mergeEvent(existing, normalized);
      if (childIds.length) {
        const merged = new Set([...(existing.childrenIds || []), ...childIds]);
        existing.childrenIds = Array.from(merged);
      }
    } else {
      state.events.set(normalized.id, normalized);
      if (normalized.parentId) {
        state.expanded.add(normalized.parentId);
      } else {
        state.expanded.add(normalized.id);
      }
    }
    if (Array.isArray(event.children)) {
      for (const child of event.children) {
        if (!child || typeof child !== "object") continue;
        const clone = { ...child, parent_id: event.id };
        queue.push(clone);
      }
    }
  }
}

function normalizeEvent(event, childIds = []) {
  const meta = sanitizeMeta(event.meta);
  const batches = Array.isArray(event.batches) ? event.batches.map((b) => ({ ...b })) : [];
  const parentId = event.parent_id || null;
  return {
    id: String(event.id),
    t_rel_ms: typeof event.t_rel_ms === "number" ? event.t_rel_ms : 0,
    level: event.level || "flow",
    phase: event.phase || "",
    who: event.who || "",
    type: event.type || "",
    blurb: event.blurb || "",
    meta,
    batches,
    parentId,
    childrenIds: childIds,
    turnId: meta.turn_id != null ? String(meta.turn_id) : meta.turn != null ? String(meta.turn) : "",
    raw: buildRawSnapshot(event, meta, batches),
  };
}

function mergeEvent(existing, incoming) {
  existing.t_rel_ms = incoming.t_rel_ms || existing.t_rel_ms;
  existing.level = incoming.level || existing.level;
  existing.phase = incoming.phase || existing.phase;
  existing.who = incoming.who || existing.who;
  existing.type = incoming.type || existing.type;
  existing.blurb = incoming.blurb || existing.blurb;
  existing.meta = { ...existing.meta, ...incoming.meta };
  existing.turnId = incoming.turnId || existing.turnId;
  existing.parentId = incoming.parentId || existing.parentId;
  existing.raw = incoming.raw;
  existing.batches = incoming.batches.length ? incoming.batches : existing.batches;
  if (incoming.childrenIds && incoming.childrenIds.length) {
    const merged = new Set([...(existing.childrenIds || []), ...incoming.childrenIds]);
    existing.childrenIds = Array.from(merged);
  }
}

function buildRawSnapshot(event, meta, batches) {
  const snapshot = { ...event };
  snapshot.meta = meta ? JSON.parse(JSON.stringify(meta)) : {};
  snapshot.batches = batches ? JSON.parse(JSON.stringify(batches)) : [];
  delete snapshot.children;
  delete snapshot.parent_id;
  return snapshot;
}

function sanitizeMeta(meta) {
  if (!meta || typeof meta !== "object") return {};
  const cleaned = {};
  for (const [key, value] of Object.entries(meta)) {
    cleaned[key] = value;
  }
  return cleaned;
}

function scheduleRender() {
  renderFilters();
  renderTimeline();
  renderDrawer();
  updateHistory();
}

function renderFilters() {
  if (!els.filterChips) return;
  const chips = state.filterChips
    .map((chip, idx) => {
      const text = `${chip.key}:${chip.display}`;
      return `<span class="filter-chip">${escapeHtml(text)}<button data-chip-index="${idx}" title="Remove">×</button></span>`;
    })
    .join("");
  els.filterChips.innerHTML = chips;
}

function renderTail() {
  if (!els.tailState || !els.tailToggle) return;
  els.tailState.textContent = state.live ? "Live" : "Paused";
  els.tailToggle.textContent = state.live ? "Pause" : "Go live";
}

function renderTimeline() {
  if (!els.timeline) return;
  const { rows } = buildRows();
  if (!rows.length) {
    els.timeline.innerHTML = `<div class="timeline-empty">${state.sessionId ? "No events yet." : "Select a session to begin."}</div>`;
    return;
  }
  els.timeline.innerHTML = rows.map(renderRow).join("");
}

function buildRows() {
  const visibleSet = computeVisibleIds();
  const rows = [];
  const grouping = state.grouping || "chronological";
  const roots = getRootIds();

  if (!roots.length) {
    return { rows: [] };
  }

  if (grouping === "chronological") {
    for (const rootId of roots) {
      appendTreeRows(rows, rootId, 0, visibleSet);
    }
    return { rows };
  }

  const groups = new Map();
  for (const rootId of roots) {
    const event = state.events.get(rootId);
    if (!event) continue;
    const key = grouping === "phase" ? (event.phase || "—") : event.turnId || "—";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(rootId);
  }

  const sortedGroups = Array.from(groups.entries()).sort((a, b) => {
    if (grouping === "phase") {
      return phaseRank(a[0]) - phaseRank(b[0]);
    }
    if (grouping === "turn") {
      const ai = parseInt(a[0], 10);
      const bi = parseInt(b[0], 10);
      if (Number.isFinite(ai) && Number.isFinite(bi)) {
        return ai - bi;
      }
      return a[0].localeCompare(b[0]);
    }
    return a[0].localeCompare(b[0]);
  });

  for (const [label, ids] of sortedGroups) {
    rows.push({ kind: "group", label: `${GROUP_LABELS[grouping] || "Group"}: ${label}` });
    ids.sort((a, b) => {
      const evA = state.events.get(a);
      const evB = state.events.get(b);
      return (evA?.t_rel_ms || 0) - (evB?.t_rel_ms || 0);
    });
    for (const id of ids) {
      appendTreeRows(rows, id, 0, visibleSet);
    }
  }

  return { rows };
}

function appendTreeRows(rows, eventId, depth, visibleSet) {
  const event = state.events.get(eventId);
  if (!event) return;
  if (visibleSet.size && !visibleSet.has(eventId)) return;

  const bookmarkLabels = state.bookmarks
    .filter((bm) => bm.eventId === eventId)
    .map((bm) => bm.label);
  for (const label of bookmarkLabels) {
    rows.push({ kind: "bookmark", label });
  }

  const childrenIds = (event.childrenIds || []).slice().sort((a, b) => {
    const evA = state.events.get(a);
    const evB = state.events.get(b);
    return (evA?.t_rel_ms || 0) - (evB?.t_rel_ms || 0);
  });
  const hasChildren = childrenIds.some((id) => !visibleSet.size || visibleSet.has(id));
  const expanded = state.expanded.has(eventId);
  rows.push({
    kind: "event",
    event,
    depth,
    hasChildren,
    expanded,
    highlight: state.matchedIds.has(eventId),
  });
  if (!hasChildren || !expanded) {
    return;
  }
  for (const childId of childrenIds) {
    appendTreeRows(rows, childId, depth + 1, visibleSet);
  }
}

function renderRow(row) {
  if (row.kind === "group") {
    return `<div class="timeline-group-header"><div class="rail"></div><div class="time"></div><div class="phase"></div><div class="who"></div><div class="type"></div><div class="blurb">${escapeHtml(row.label)}</div><div class="meta"></div></div>`;
  }
  if (row.kind === "bookmark") {
    return `<div class="timeline-bookmark"><div class="rail" style="--rail-color:#ffb347"></div><div class="time"></div><div class="phase"></div><div class="who"></div><div class="type"></div><div class="blurb">${escapeHtml(row.label)}</div><div class="meta"></div></div>`;
  }
  const event = row.event;
  const indentStyle = `style="--indent:${row.depth}"`;
  const toggle = row.hasChildren
    ? `<button class="tree-toggle" data-role="toggle" data-event-id="${escapeAttr(event.id)}" aria-expanded="${row.expanded}">${row.expanded ? "▾" : "▸"}</button>`
    : "";
  const indent = `<span class="tree-indent" ${indentStyle}></span>`;
  const time = typeof event.t_rel_ms === "number" ? event.t_rel_ms.toLocaleString() : "";
  const phaseColor = resolvePhaseColor(event.phase);
  const metaChips = buildMetaChips(event);
  const rawButtons = buildBatchButtons(event);
  const highlightAttr = row.highlight ? " data-highlight=\"true\"" : "";
  const classes = ["timeline-row"];
  return `
    <div class="${classes.join(" ")}" data-event-id="${escapeAttr(event.id)}" data-level="${escapeAttr(event.level || "")}" ${highlightAttr}>
      <div class="rail" style="--rail-color:${phaseColor}"></div>
      <div class="time">${escapeHtml(time)}</div>
      <div class="phase">${escapeHtml(event.phase || "")}</div>
      <div class="who">${escapeHtml(event.who || "")}</div>
      <div class="type">${escapeHtml(event.type || "")}</div>
      <div class="blurb"><span class="tree-label">${indent}${toggle}${escapeHtml(event.blurb || event.type || "")}</span></div>
      <div class="meta">${metaChips}${rawButtons}</div>
    </div>`;
}

function buildMetaChips(event) {
  const meta = event.meta || {};
  const entries = Object.entries(meta).filter(([key, value]) => value !== null && value !== undefined);
  if (!entries.length && !event.turnId) {
    return "";
  }
  const chips = [];
  const seen = new Set();
  if (event.turnId) {
    chips.push(renderMetaChip("turn", event.turnId));
    seen.add(`turn:${event.turnId}`);
  }
  for (const [key, value] of entries) {
    let display = formatMetaValue(value);
    if (display.length > MAX_META_LENGTH) {
      display = `${display.slice(0, MAX_META_LENGTH - 1)}…`;
    }
    const dedupeKey = `${key}:${display}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    chips.push(renderMetaChip(key, display, value));
  }
  return chips.join("");
}

function renderMetaChip(key, display, rawValue) {
  const dataValue = rawValue !== undefined ? String(rawValue) : String(display);
  return `<button class="meta-chip" data-meta-key="${escapeAttr(key)}" data-meta-value="${escapeAttr(dataValue)}">${escapeHtml(`${key}:${display}`)}</button>`;
}

function buildBatchButtons(event) {
  if (!Array.isArray(event.batches) || !event.batches.length) return "";
  return event.batches
    .map((batch, idx) => {
      const label = batch.kind ? batch.kind : `batch-${idx + 1}`;
      return `<button class="raw-batch-btn" data-role="batch" data-event-id="${escapeAttr(event.id)}" data-index="${idx}">View ${escapeHtml(label)}</button>`;
    })
    .join("");
}

function computeVisibleIds() {
  const search = (state.filterText || "").toLowerCase();
  const hasChips = state.filterChips.length > 0;
  const turn = state.turnFilter ? String(state.turnFilter) : "";
  if (!search && !hasChips && !turn) {
    state.visibleIds = new Set(state.events.keys());
    state.matchedIds = new Set();
    return state.visibleIds;
  }
  const matches = new Set();
  const highlight = new Set();
  for (const event of state.events.values()) {
    if (!event) continue;
    let ok = true;
    if (search) {
      const haystack = [event.type, event.phase, event.who, event.blurb, JSON.stringify(event.meta || {})]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(search)) {
        ok = false;
      } else {
        highlight.add(event.id);
      }
    }
    if (ok && turn) {
      ok = event.turnId === turn;
      if (ok) highlight.add(event.id);
    }
    if (ok && hasChips) {
      for (const chip of state.filterChips) {
        const value = resolveMetaValue(event, chip.key);
        if (value == null) {
          ok = false;
          break;
        }
        if (String(value) !== chip.value) {
          ok = false;
          break;
        }
      }
    }
    if (ok) {
      matches.add(event.id);
    }
  }
  const visible = new Set(matches);
  for (const id of matches) {
    let cursor = state.events.get(id)?.parentId;
    while (cursor) {
      visible.add(cursor);
      cursor = state.events.get(cursor)?.parentId;
    }
  }
  const stack = Array.from(visible);
  while (stack.length) {
    const id = stack.pop();
    const event = state.events.get(id);
    if (!event || !event.childrenIds) continue;
    for (const child of event.childrenIds) {
      visible.add(child);
      stack.push(child);
    }
  }
  state.visibleIds = visible;
  state.matchedIds = highlight;
  return visible;
}

function resolveMetaValue(event, key) {
  if (key === "turn") {
    return event.turnId;
  }
  return event.meta ? event.meta[key] : undefined;
}

function getRootIds() {
  const roots = [];
  for (const event of state.events.values()) {
    if (!event) continue;
    if (!event.parentId || !state.events.has(event.parentId)) {
      roots.push(event.id);
    }
  }
  roots.sort((a, b) => {
    const evA = state.events.get(a);
    const evB = state.events.get(b);
    return (evA?.t_rel_ms || 0) - (evB?.t_rel_ms || 0);
  });
  return roots;
}

function resolvePhaseColor(phase) {
  if (!phase) return PHASE_COLORS.default;
  const key = phase.toLowerCase();
  return PHASE_COLORS[key] || PHASE_COLORS.default;
}

function formatMetaValue(value) {
  if (value == null) return "";
  if (typeof value === "number") {
    if (Math.abs(value) >= 1000) {
      return value.toFixed(2);
    }
    return String(value);
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function phaseRank(phase) {
  const order = ["session", "turn", "nlu", "nlg", "tts", "audio", "confirm", "policy", "guardrail"];
  const idx = order.indexOf((phase || "").toLowerCase());
  return idx >= 0 ? idx : order.length + 1;
}

function onTimelineClick(ev) {
  const toggleBtn = ev.target.closest("button[data-role=\"toggle\"]");
  if (toggleBtn) {
    const id = toggleBtn.getAttribute("data-event-id");
    if (id) {
      if (state.expanded.has(id)) {
        state.expanded.delete(id);
      } else {
        state.expanded.add(id);
      }
      renderTimeline();
    }
    return;
  }

  const batchBtn = ev.target.closest("button[data-role=\"batch\"]");
  if (batchBtn) {
    const id = batchBtn.getAttribute("data-event-id");
    const idx = Number(batchBtn.getAttribute("data-index"));
    showBatchPopover(batchBtn, id, idx);
    return;
  }

  const metaChip = ev.target.closest(".meta-chip");
  if (metaChip) {
    const key = metaChip.getAttribute("data-meta-key");
    const value = metaChip.getAttribute("data-meta-value");
    if (key && value != null) {
      addFilterChip(key, value);
    }
    return;
  }

  const row = ev.target.closest(".timeline-row[data-event-id]");
  if (!row) return;
  const id = row.getAttribute("data-event-id");
  openDrawer(id);
}

function onTimelineContext(ev) {
  const row = ev.target.closest(".timeline-row[data-event-id]");
  if (!row) return;
  ev.preventDefault();
  const id = row.getAttribute("data-event-id");
  const label = prompt("Add bookmark label:");
  if (!label) return;
  state.bookmarks.push({ eventId: id, label });
  renderTimeline();
}

function showBatchPopover(anchor, eventId, index) {
  hidePopover();
  const event = state.events.get(eventId);
  if (!event) return;
  const batch = event.batches?.[index];
  if (!batch) return;
  const pop = document.createElement("div");
  pop.className = "popover";
  const itemsText = JSON.stringify(batch.items, null, 2);
  pop.innerHTML = `<h3>${escapeHtml(batch.kind || `Batch ${index + 1}`)}</h3><pre>${escapeHtml(itemsText)}</pre>`;
  document.body.appendChild(pop);
  const rect = anchor.getBoundingClientRect();
  pop.style.top = `${rect.bottom + window.scrollY + 8}px`;
  pop.style.left = `${rect.left + window.scrollX}px`;
  popoverNode = pop;
}

function hidePopover() {
  if (popoverNode && popoverNode.parentNode) {
    popoverNode.parentNode.removeChild(popoverNode);
  }
  popoverNode = null;
}

function addFilterChip(key, value) {
  const exists = state.filterChips.some((chip) => chip.key === key && chip.value === String(value));
  if (exists) return;
  state.filterChips.push({ key, value: String(value), display: String(value) });
  scheduleRender();
}

function toggleLevel(level) {
  if (!level || level === "flow") return;
  if (state.levels.has(level)) {
    state.levels.delete(level);
  } else {
    state.levels.add(level);
  }
  reflectLevelButtons();
  state.events.clear();
  state.expanded.clear();
  state.matchedIds = new Set();
  state.visibleIds = new Set();
  state.drawerEventId = null;
  renderTimeline();
  renderDrawer();
  fetchTrace({ reset: true });
  updateHistory();
}

function reflectLevelButtons() {
  if (!els.levelContainer) return;
  for (const btn of els.levelContainer.querySelectorAll("button[data-level]")) {
    const level = btn.getAttribute("data-level");
    const active = state.levels.has(level);
    btn.classList.toggle("active", active);
  }
}

function setGrouping(group) {
  if (!group) return;
  if (!GROUP_LABELS[group] && group !== "chronological") return;
  state.grouping = group;
  reflectGroupingButtons();
  renderTimeline();
  updateHistory();
}

function reflectGroupingButtons() {
  if (!els.groupContainer) return;
  for (const btn of els.groupContainer.querySelectorAll("button[data-group]")) {
    const group = btn.getAttribute("data-group");
    btn.classList.toggle("active", group === state.grouping);
  }
}

function goLive(immediate = false) {
  state.live = true;
  renderTail();
  if (immediate) {
    fetchTrace({ reset: false }).finally(schedulePoll);
  } else {
    schedulePoll();
  }
}

function pauseLive() {
  state.live = false;
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
  renderTail();
}

function schedulePoll() {
  if (!state.live) return;
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
  }
  const delay = POLL_MIN_MS + Math.random() * (POLL_MAX_MS - POLL_MIN_MS);
  state.pollTimer = setTimeout(() => {
    fetchTrace({ reset: false }).finally(() => {
      if (state.live) schedulePoll();
    });
  }, delay);
}

function downloadExport({ redacted }) {
  if (!state.sessionId) {
    setHint("Select a session first.");
    return;
  }
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  params.set("expand", "all");
  params.set("levels", Array.from(state.levels).join(","));
  params.set("redacted", redacted ? "1" : "0");
  fetch(`/api/v1/flow/export.ndjson?${params.toString()}`, { credentials: "include" })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.blob();
    })
    .then((blob) => {
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const suffix = redacted ? "redacted" : "full";
      link.href = href;
      link.download = `flow_${state.sessionId}_${suffix}.ndjson`;
      document.body.appendChild(link);
      link.click();
      requestAnimationFrame(() => {
        URL.revokeObjectURL(href);
        link.remove();
      });
    })
    .catch((err) => {
      console.warn("[flow] export failed", err);
      setHint("Export failed.");
    });
}

function copyLink() {
  if (!state.sessionId) {
    setHint("Select a session to copy.");
    return;
  }
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  params.set("levels", Array.from(state.levels).join(","));
  if (state.grouping && state.grouping !== "chronological") {
    params.set("group", state.grouping);
  }
  if (state.filterText) params.set("filter", state.filterText);
  if (state.turnFilter) params.set("turn", state.turnFilter);
  if (state.filterChips.length) {
    params.set(
      "chips",
      state.filterChips
        .map((chip) => `${encodeURIComponent(chip.key)}:${encodeURIComponent(chip.value)}`)
        .join(",")
    );
  }
  const hash = `#/admin/flow${params.toString() ? `?${params.toString()}` : ""}`;
  const link = `${window.location.origin}/admin${hash}`;
  writeClipboardText(link)
    .then(() => setHint("Link copied."))
    .catch(() => setHint("Copy failed."));
}

function handoffToChatGPT() {
  if (!state.sessionId) {
    setHint("Select a session first.");
    return;
  }
  const summary = buildSessionSummary();
  writeClipboardText(summary)
    .then(() => setHint("Summary copied for ChatGPT."))
    .catch(() => setHint("Could not copy summary."));
}

function buildSessionSummary() {
  const events = Array.from(state.events.values()).sort((a, b) => a.t_rel_ms - b.t_rel_ms);
  const head = events.slice(0, 5).map((evt) => `- ${evt.t_rel_ms}ms ${evt.phase} ${evt.type}`);
  return `Ask Chip Flow Summary\nSession: ${state.sessionId}\nLevels: ${Array.from(state.levels).join(", ")}\nEvents captured: ${events.length}\nFirst samples:\n${head.join("\n")}`;
}

function openDrawer(eventId) {
  state.drawerEventId = eventId;
  renderDrawer();
  if (!eventId) return;
  const row = els.timeline?.querySelector(`.timeline-row[data-event-id="${cssEscape(eventId)}"]`);
  if (row) {
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("pulse");
    setTimeout(() => row.classList.remove("pulse"), 1400);
  }
}

function renderDrawer() {
  if (!els.drawer) return;
  const event = state.drawerEventId ? state.events.get(state.drawerEventId) : null;
  if (!event) {
    els.drawer.setAttribute("aria-hidden", "true");
    els.drawerTitle.textContent = "Event";
    els.drawerMeta.innerHTML = "";
    els.drawerJson.textContent = "";
    els.drawerRelated.innerHTML = "";
    return;
  }
  els.drawer.setAttribute("aria-hidden", "false");
  els.drawerTitle.textContent = `${event.id} — ${event.type}`;
  const metaLines = [
    `<div><strong>Time</strong> ${event.t_rel_ms.toLocaleString()} ms</div>`,
    `<div><strong>Phase</strong> ${escapeHtml(event.phase || "")}</div>`,
    `<div><strong>Who</strong> ${escapeHtml(event.who || "")}</div>`,
    `<div><strong>Level</strong> ${escapeHtml(event.level || "")}</div>`,
  ];
  if (event.turnId) {
    metaLines.push(`<div><strong>Turn</strong> ${escapeHtml(event.turnId)}</div>`);
  }
  els.drawerMeta.innerHTML = metaLines.join("");
  els.drawerJson.textContent = JSON.stringify(event.raw, null, 2);
  els.drawerRelated.innerHTML = renderRelated(event);
}

function renderRelated(event) {
  const items = [];
  if (event.parentId) {
    items.push({ label: `Parent ${event.parentId}`, target: event.parentId });
  }
  if (event.childrenIds && event.childrenIds.length) {
    for (const child of event.childrenIds) {
      items.push({ label: `Child ${child}`, target: child });
    }
  }
  if (event.turnId) {
    const sameTurn = Array.from(state.events.values()).filter((ev) => ev.turnId === event.turnId && ev.id !== event.id);
    for (const ev of sameTurn) {
      items.push({ label: `Turn ${event.turnId}: ${ev.type}`, target: ev.id });
    }
  }
  const confirmId = event.meta?.confirm_id;
  if (confirmId != null) {
    const confirmSet = Array.from(state.events.values()).filter(
      (ev) => ev.meta?.confirm_id === confirmId && ev.id !== event.id
    );
    for (const ev of confirmSet) {
      items.push({ label: `Confirm ${confirmId}: ${ev.type}`, target: ev.id });
    }
  }
  const uniq = new Map();
  for (const item of items) {
    if (!uniq.has(item.target)) {
      uniq.set(item.target, item);
    }
  }
  if (!uniq.size) {
    return '<p class="drawer-empty">No related events.</p>';
  }
  return Array.from(uniq.values())
    .map((item) => `<button type="button" data-target="${escapeAttr(item.target)}">${escapeHtml(item.label)}</button>`)
    .join("");
}

function focusEvent(eventId) {
  if (!eventId) return;
  let cursor = state.events.get(eventId)?.parentId;
  while (cursor) {
    state.expanded.add(cursor);
    cursor = state.events.get(cursor)?.parentId;
  }
  openDrawer(eventId);
  renderTimeline();
}

function copyDrawerJson() {
  const event = state.drawerEventId ? state.events.get(state.drawerEventId) : null;
  if (!event) return;
  const text = JSON.stringify(event.raw, null, 2);
  writeClipboardText(text)
    .then(() => setHint("Event JSON copied."))
    .catch(() => setHint("Copy failed."));
}

function setHint(text) {
  if (!els.sessionHint) return;
  els.sessionHint.textContent = text;
}

function updateHistory() {
  if (!state.sessionId) return;
  const params = new URLSearchParams();
  params.set("session_id", state.sessionId);
  params.set("levels", Array.from(state.levels).join(","));
  if (state.grouping && state.grouping !== "chronological") {
    params.set("group", state.grouping);
  }
  if (state.filterText) params.set("filter", state.filterText);
  if (state.turnFilter) params.set("turn", state.turnFilter);
  if (state.filterChips.length) {
    params.set(
      "chips",
      state.filterChips.map((chip) => `${encodeURIComponent(chip.key)}:${encodeURIComponent(chip.value)}`).join(",")
    );
  }
  const url = `/admin/flow?${params.toString()}`;
  window.history.replaceState({}, "", url);
  window.location.hash = `#/admin/flow${params.toString() ? `?${params.toString()}` : ""}`;
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function parseChipParam(raw) {
  const chips = [];
  if (!raw) return chips;
  const parts = String(raw).split(",");
  for (const part of parts) {
    const [rawKey, rawValue] = part.split(":");
    if (!rawKey || typeof rawValue === "undefined") continue;
    const key = decodeURIComponent(rawKey);
    const value = decodeURIComponent(rawValue);
    chips.push({ key, value, display: value });
  }
  return chips;
}

function writeClipboardText(text) {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(text);
    }
  } catch (err) {
    console.warn("[flow] clipboard write failed", err);
  }
  return new Promise((resolve, reject) => {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "true");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    try {
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      if (ok) {
        resolve();
      } else {
        reject(new Error("copy command failed"));
      }
    } catch (err) {
      document.body.removeChild(area);
      reject(err);
    }
  });
}

export {};
