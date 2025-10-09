import { openWS, waitWSOpen, closeWS, configure } from './ws_module.js';
import { initMic, armVAD, disarmVAD } from './voice.js';
import { unlockAudio } from './audio.js';
import { getSID } from './util/sid.js';

const STEP_DEFS = [
  {
    id: 'ws',
    title: 'Establish production connection',
    role: 'system',
    description: 'Open a live /ws/v1/chat socket with the same bearer subprotocol and CSRF protections the main app uses.',
  },
  {
    id: 'greet',
    title: 'Chip handshake',
    role: 'system',
    description: 'Send a Configure greet frame and wait for Chip to answer so we know the routing path is alive.',
  },
  {
    id: 'user',
    title: 'Your turn: capture voice sample',
    role: 'you',
    description: 'Run the full production microphone + VAD pipeline. When the card lights up, say the diagnostic phrase exactly as written.',
  },
  {
    id: 'asr',
    title: 'Speech-to-text + Admin log trace',
    role: 'system',
    description: 'Verify Admin SSE reports partial/final ASR events and that the WebSocket delivers the same transcript frames.',
  },
  {
    id: 'assistant',
    title: 'Chip responds over WS/TTS',
    role: 'system',
    description: 'Confirm we receive assistant audio/text frames back (just like production chat playback).',
  },
];

const STATUS_LABELS = {
  pending: 'Pending',
  active: 'In progress',
  waiting: 'Listening',
  done: 'Passed',
  error: 'Needs attention',
};

const SPEAK_LABELS = {
  idle: 'Mic not armed yet.',
  ready: 'Listening. Speak the diagnostic phrase when you are ready.',
  recording: 'Recording… speak clearly into the mic.',
  complete: 'Captured. Waiting for transcription results…',
  error: 'Microphone error — check permissions or console logs.',
};

const SAMPLE_PHRASE = 'Chip, run the admin voice diagnostic.';
const GREET_TIMEOUT_MS = 20000;

const root = document.querySelector('#admin-diagnostics');
if (!root) {
  console.warn('[admin-diagnostics] root element not found');
} else {
  renderUI(root);
  bootstrap();
}

function renderUI(container) {
  const header = `
    <div class="diag-head">
      <p>
        This diagnostic mirrors the production conversational pipeline end-to-end: WebSocket auth, Configure greet,
        voice capture with VAD, Admin SSE monitoring, and Chip’s TTS reply. Follow the steps below — we will log
        every transition to the Admin log so you can trace failures down to the frame.
      </p>
      <div class="actions">
        <button id="diag-start">Run conversational diagnostic</button>
        <div class="run-meta" id="diag-run-meta"></div>
      </div>
    </div>`;

  const steps = STEP_DEFS.map((step, idx) => `
    <li class="diag-step" data-step="${step.id}" data-status="pending">
      <div class="step-head">
        <div>
          <div class="step-index">Step ${idx + 1}</div>
          <div class="step-title">${step.title}</div>
        </div>
        <div class="step-badges">
          <span class="step-role ${step.role === 'you' ? 'role-you' : 'role-system'}">${step.role === 'you' ? 'You' : 'System'}</span>
          <span class="step-status">Pending</span>
        </div>
      </div>
      <div class="step-desc">${step.description}</div>
      <div class="step-live" id="step-live-${step.id}"></div>
      ${step.id === 'user'
        ? `<div class="speak-card" id="speak-card" data-mode="idle">
             <div class="speak-label">Say this phrase</div>
             <div class="speak-text">&ldquo;${SAMPLE_PHRASE}&rdquo;</div>
             <div class="speak-state" id="speak-state">${SPEAK_LABELS.idle}</div>
             <div class="level">
               <div class="level-bar" id="mic-level-bar"></div>
             </div>
             <div class="level-readout"><span id="mic-level-db">-∞</span> dBFS</div>
           </div>`
        : ''}
    </li>`).join('');

  const logs = `
    <section class="diag-logs">
      <div class="diag-log">
        <h3>Admin log trace (/api/v1/admin/logs)</h3>
        <pre id="diag-admin-log" class="log-window"></pre>
      </div>
      <div class="diag-log">
        <h3>WebSocket frames (WS→UI)</h3>
        <pre id="diag-ws-log" class="log-window"></pre>
      </div>
    </section>`;

  container.innerHTML = header + `<ol class="diag-steps">${steps}</ol>` + logs;
}

function bootstrap() {
  const startBtn = root.querySelector('#diag-start');
  const runMetaEl = root.querySelector('#diag-run-meta');
  const adminLogEl = root.querySelector('#diag-admin-log');
  const wsLogEl = root.querySelector('#diag-ws-log');
  const speakCard = root.querySelector('#speak-card');
  const speakStateEl = root.querySelector('#speak-state');
  const policyNodes = {
    container: document.getElementById('flow-live-card'),
    intent: document.getElementById('nlu-intent'),
    guardrail: document.getElementById('nlu-guardrail'),
    move: document.getElementById('nlu-move'),
    toolplan: document.getElementById('nlu-toolplan'),
    timestamp: document.getElementById('nlu-updated'),
    lastEvent: document.getElementById('nlu-last-event'),
  };

  const stepMap = new Map();
  for (const step of STEP_DEFS) {
    const el = root.querySelector(`[data-step="${step.id}"]`);
    if (!el) continue;
    stepMap.set(step.id, {
      el,
      statusEl: el.querySelector('.step-status'),
      liveEl: el.querySelector(`#step-live-${step.id}`),
    });
  }

  const state = {
    running: false,
    sid: null,
    startBtn,
    runMetaEl,
    adminLogEl,
    wsLogEl,
    speakCard,
    speakStateEl,
    stepMap,
    wsListener: null,
    sse: null,
    asr: { partials: 0, finals: 0, errors: 0 },
    latestTranscript: '',
    awaitingGreeting: false,
    awaitingResponse: false,
    responseStarted: false,
    greetResolve: null,
    greetReject: null,
    asrResolve: null,
    asrReject: null,
    responseResolve: null,
    responseReject: null,
    stopMeter: null,
    policyNodes,
    policySnapshot: null,
    asrFinalSatisfied: false,
    asrTimer: null,
  };

  resetView(state);
  if (startBtn) startBtn.addEventListener('click', () => runDiagnostic(state));
  window.AdminDiagnostics = {
    run: () => runDiagnostic(state),
  };
}

async function runDiagnostic(state) {
  if (!state.startBtn || state.running) return;
  state.running = true;
  state.startBtn.disabled = true;
  resetView(state);

  state.sid = getSID();
  if (state.runMetaEl) state.runMetaEl.textContent = `Session: ${state.sid}`;
  appendLog(state.adminLogEl, `${timestamp()} starting diagnostic for session=${state.sid}`);
  logAdminEvent(state, 'diagnostic_start');

  try {
    try { await unlockAudio(); } catch {}

    setStepStatus(state, 'user', 'pending', 'Requesting microphone permission…');
    let micStream = null;
    try {
      micStream = await initMic();
      disarmVAD();
      setStepStatus(state, 'user', 'pending', 'Microphone granted. Waiting for Chip to greet…');

      // Start live mic meter as soon as we have a stream (no effect on VAD).
      const barEl = document.getElementById('mic-level-bar');
      const dbEl  = document.getElementById('mic-level-db');
      state.stopMeter = startMicMeter(micStream, { barEl, dbEl });
    } catch (err) {
      setSpeakState(state, 'error');
      setStepStatus(state, 'user', 'error', `Microphone access failed: ${err?.message || err}`);
      throw new Error('Microphone permission denied');
    }

    setStepStatus(state, 'ws', 'active', 'Opening WebSocket channel to /ws/v1/chat…');
    const ws = await ensureWsOpen();
    state.ws = ws;
    setStepStatus(state, 'ws', 'done', 'WebSocket connected.');
    logAdminEvent(state, 'step_ws_ok');

    state.wsListener = (ev) => handleWSFrame(state, ev?.detail || {});
    window.addEventListener('askchip-ws', state.wsListener);

    state.sse = startAdminSSE(state, (evt) => handleAdminEvent(state, evt));
    updateASRStatus(state, { status: 'pending', message: 'Waiting for speech activity…' });

    configure({ greet: true, reset: 1, session_id: state.sid, metadata: { origin: 'admin_voice_diagnostic' } });
    setStepStatus(state, 'greet', 'active', 'Configure greet sent. Waiting for Chip to answer…');
    logAdminEvent(state, 'step_greet_sent');

    const greetFrame = await waitForGreeting(state, GREET_TIMEOUT_MS);
    setStepStatus(state, 'greet', 'done', summarizeAssistantFrame(greetFrame));
    logAdminEvent(state, 'step_greet_ok', { frame_type: greetFrame?.type || greetFrame?.label || 'assistant' });
    window.__askchip_session_started = true;

    // Wait for the greet TTS to finish so echo/NS settles, then arm VAD.
    try { await waitForUtteranceEndOnce(8000); } catch {}
    await delay(250);

    setStepStatus(state, 'user', 'active', 'Arming VAD. Speak when the card below says recording.');
    setSpeakState(state, 'ready');
    try {
      if (micStream) await armVAD(micStream);
      else await armVAD();
    } catch (err) {
      setSpeakState(state, 'error');
      setStepStatus(state, 'user', 'error', `Failed to arm microphone: ${err?.message || err}`);
      throw new Error('Failed to arm microphone');
    }

    const turnResult = await waitForUserTurn(state, 15000);
    setStepStatus(state, 'user', 'done', 'Audio captured and queued to Chip.');
    logAdminEvent(state, 'step_user_ok', { duration_ms: turnResult?.duration || 0 });

    const finalEvent = await waitForASRFinal(state, 12000);
    updateASRStatus(state, { status: 'done' });
    logAdminEvent(state, 'step_asr_ok', {
      partials: state.asr.partials,
      finals: state.asr.finals || 1,
      transcript: state.latestTranscript || '',
      via: finalEvent?.label || 'asr_final',
    });

    state.awaitingResponse = true;
    const assistantResult = await waitForAssistantResponse(state, 12000);
    setStepStatus(state, 'assistant', 'done', assistantResult.summary);
    logAdminEvent(state, 'step_assistant_ok', { frame_type: assistantResult.type, summary: assistantResult.summary });

    appendLog(state.wsLogEl, `${timestamp()} diagnostic complete.`);
    logAdminEvent(state, 'diagnostic_complete');
  } catch (err) {
    const msg = err?.message || String(err) || 'Unknown diagnostic error';
    appendLog(state.wsLogEl, `${timestamp()} [error] ${msg}`);
    const failing = findFirstIncompleteStep(state);
    setStepStatus(state, failing, 'error', msg);
    logAdminEvent(state, 'diagnostic_error', { error: msg });
  } finally {
    try { closeWS(); } catch(_) {}

    cleanupRun(state);
    state.startBtn.disabled = false;
    state.running = false;
  }
}

function resetView(state) {
  for (const step of STEP_DEFS) {
    setStepStatus(state, step.id, 'pending', '');
  }
  setStepStatus(state, 'asr', 'pending', 'Waiting for speech activity…');
  setStepStatus(state, 'assistant', 'pending', 'No assistant response yet.');
  setSpeakState(state, 'idle');
  state.asr = { partials: 0, finals: 0, errors: 0 };
  state.latestTranscript = '';
  state.asrFinalSatisfied = false;
  if (state.asrTimer) {
    clearTimeout(state.asrTimer);
    state.asrTimer = null;
  }
  state.awaitingGreeting = false;
  state.awaitingResponse = false;
  state.responseStarted = false;
  if (state.adminLogEl) state.adminLogEl.textContent = '';
  if (state.wsLogEl) state.wsLogEl.textContent = '';
  if (state.runMetaEl) state.runMetaEl.textContent = '';
  resetPolicySnapshot(state);
}

async function ensureWsOpen(timeoutMs = 8000) {
  const ws = await openWS({ reconnect: false });
  await Promise.race([
    waitWSOpen(),
    new Promise((_, rej) => setTimeout(() => rej(new Error('WebSocket open timed out')), timeoutMs)),
  ]);
  return ws;
}

function waitForGreeting(state, timeoutMs) {
  return new Promise((resolve, reject) => {
    state.awaitingGreeting = true;
    const timer = setTimeout(() => {
      state.awaitingGreeting = false;
      state.greetResolve = null;
      state.greetReject = null;
      reject(new Error('Chip did not greet within the expected window.'));
    }, timeoutMs);
    state.greetResolve = (frame) => {
      clearTimeout(timer);
      state.awaitingGreeting = false;
      state.greetResolve = null;
      state.greetReject = null;
      resolve(frame);
    };
    state.greetReject = (err) => {
      clearTimeout(timer);
      state.awaitingGreeting = false;
      state.greetResolve = null;
      state.greetReject = null;
      reject(err instanceof Error ? err : new Error(String(err || 'greet rejected')));
    };
  });
}

function waitForASRFinal(state, timeoutMs) {
  return new Promise((resolve, reject) => {
    state.asrFinalSatisfied = false;
    if (state.asrTimer) {
      clearTimeout(state.asrTimer);
      state.asrTimer = null;
    }
    state.asrTimer = setTimeout(() => {
      state.asrResolve = null;
      state.asrReject = null;
      state.asrTimer = null;
      reject(new Error('Timed out waiting for ASR final.'));
    }, timeoutMs);
    state.asrResolve = (evt) => {
      if (state.asrTimer) {
        clearTimeout(state.asrTimer);
        state.asrTimer = null;
      }
      state.asrResolve = null;
      state.asrReject = null;
      state.asrFinalSatisfied = true;
      resolve(evt);
    };
    state.asrReject = (err) => {
      if (state.asrTimer) {
        clearTimeout(state.asrTimer);
        state.asrTimer = null;
      }
      state.asrResolve = null;
      state.asrReject = null;
      reject(err instanceof Error ? err : new Error(String(err || 'asr error')));
    };
  });
}

function waitForAssistantResponse(state, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      state.responseResolve = null;
      state.responseReject = null;
      state.awaitingResponse = false;
      reject(new Error('Timed out waiting for Chip to respond.'));
    }, timeoutMs);
    state.responseResolve = (info) => {
      clearTimeout(timer);
      state.responseResolve = null;
      state.responseReject = null;
      state.awaitingResponse = false;
      resolve(info);
    };
    state.responseReject = (err) => {
      clearTimeout(timer);
      state.responseResolve = null;
      state.responseReject = null;
      state.awaitingResponse = false;
      reject(err instanceof Error ? err : new Error(String(err || 'assistant error')));
    };
  });
}

function waitForUserTurn(state, timeoutMs) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    let recorded = false;
    const timer = setTimeout(() => {
      window.removeEventListener('askchip-voice', onVoice);
      setSpeakState(state, 'error');
      reject(new Error('No speech detected — timed out waiting for microphone activity.'));
    }, timeoutMs);

    function cleanup() {
      clearTimeout(timer);
      window.removeEventListener('askchip-voice', onVoice);
    }

    function onVoice(ev) {
      const detail = ev?.detail || {};
      if (detail.state === 'recording') {
        recorded = true;
        setSpeakState(state, 'recording');
        setStepStatus(state, 'user', 'active', 'Recording… speak the diagnostic phrase now.');
      } else if (detail.state === 'armed') {
        if (!recorded) {
          setSpeakState(state, 'ready');
          setStepStatus(state, 'user', 'active', 'Listening. Speak when ready.');
        } else {
          cleanup();
          setSpeakState(state, 'complete');
          resolve({ duration: Date.now() - start });
        }
      } else if (detail.state === 'idle' && !recorded) {
        cleanup();
        setSpeakState(state, 'error');
        reject(new Error('Microphone disarmed before any audio was captured.'));
      }
    }

    window.addEventListener('askchip-voice', onVoice);
  });
}

/** Waits for the next UtteranceEnd once, then resolves. */
function waitForUtteranceEndOnce(timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const onWS = (ev) => {
      const fr = ev?.detail || {};
      if (fr.type === 'UtteranceEnd' || fr.label === 'UtteranceEnd') {
        cleanup(); resolve();
      }
    };
    const timer = setTimeout(() => { cleanup(); reject(new Error('UtteranceEnd timeout')); }, timeoutMs);
    function cleanup(){ clearTimeout(timer); window.removeEventListener('askchip-ws', onWS); }
    window.addEventListener('askchip-ws', onWS);
  });
}

function satisfyASRWait(state, payload = { label: 'asr_final' }, { ensureDone = false, message } = {}) {
  const alreadySatisfied = !!state.asrFinalSatisfied;
  if (!alreadySatisfied) {
    state.asrFinalSatisfied = true;
    if (ensureDone) {
      updateASRStatus(state, { status: 'done', message });
    }
  }
  state.asrResolve?.(payload);
}

function handleWSFrame(state, frame) {
  appendLog(state.wsLogEl, `${timestamp()} ${describeFrame(frame)}`);

  // Greet resolution
  if (state.awaitingGreeting && isAssistantFrame(frame)) {
    state.greetResolve?.(frame);
    return;
  }

  // Assistant response tracking
  if (state.awaitingResponse && isAssistantFrame(frame)) {
    if (!state.responseStarted) {
      state.responseStarted = true;
      setStepStatus(state, 'assistant', 'active', 'Chip is responding…');
    }
    if (frame.type === 'assistant_audio') {
      const chunks = Array.isArray(frame.audio_chunks) ? frame.audio_chunks.length : 0;
      const summary = frame.is_last
        ? `Assistant audio completed (${chunks} chunk${chunks === 1 ? '' : 's'}).`
        : `Assistant audio chunk received (${chunks} chunk${chunks === 1 ? '' : 's'}).`;
      setStepStatus(state, 'assistant', frame.is_last ? 'done' : 'active', summary);
      if (frame.is_last) {
        state.responseResolve?.({ summary, type: frame.type });
      }
    } else if (frame.type === 'assistant_end' || frame.type === 'UtteranceEnd') {
      const summary = frame.type === 'assistant_end'
        ? 'Assistant finished streaming.'
        : 'UtteranceEnd received (TTS playback complete).';
      setStepStatus(state, 'assistant', 'done', summary);
      state.responseResolve?.({ summary, type: frame.type });
    } else {
      const text = extractAssistantText(frame);
      const summary = text ? `Assistant text: “${text}”` : `Assistant frame: ${frame.type || frame.label}`;
      setStepStatus(state, 'assistant', 'active', summary);
    }
  }

  // Surface ASR errors that arrive as WS Error frames
  if (frame?.type === 'Error' && /asr/i.test(String(frame?.label || ''))) {
    const msg = frame?.message || frame?.error || 'ASR connection error';
    updateASRStatus(state, { status: 'error', message: msg, error: true });
  }

  // Transcript frames (Deepgram-like or normalized)
  if (isTranscriptFrame(frame)) {
    const final = isTranscriptFinal(frame);
    const transcript = extractTranscript(frame);

    if (transcript) {
      state.latestTranscript = transcript;
      updateASRStatus(state, { transcript, status: final ? 'done' : 'active' });
    } else if (final) {
      // Final with no text: treat as silence so we don’t time out.
      updateASRStatus(state, { status: 'done', message: 'No speech detected (final).' });
    }

    if (final) {
      // Resolve the ASR wait even if SSE didn’t fire an asr_final.
      satisfyASRWait(state, { label: 'asr_final', transcript: transcript || '' });
    }
    return;
  }

  if (frame?.type === 'CloseStream') {
    satisfyASRWait(state, { label: 'close_stream' }, { ensureDone: true, message: 'ASR stream closed.' });
  }
}

function handleAdminEvent(state, evt) {
  if (!evt) return;

  const kindTag = String(evt.kind || '').toLowerCase();
  const labelTag = String(evt.label || '').toLowerCase();

  maybeHandleASREvent(state, evt, kindTag, labelTag);
  maybeHandlePolicyEvent(state, evt, kindTag, labelTag);
}

function maybeHandleASREvent(state, evt, kindTag, labelTag) {
  const combined = `${kindTag} ${labelTag}`.trim();
  const looksLikeASR =
    /^asr/.test(kindTag) ||
    /^asr/.test(labelTag) ||
    /\basr[._-]/.test(kindTag) ||
    /\basr[._-]/.test(labelTag);
  if (!looksLikeASR) return false;

  const tag = combined || 'asr';

  if (tag.includes('error')) {
    const message = evt.error ? `ASR error: ${evt.error}` : 'ASR error reported.';
    updateASRStatus(state, { status: 'error', message, error: true });
    return true;
  }

  if (tag.includes('final')) {
    updateASRStatus(state, { status: 'done', final: true });
    satisfyASRWait(state, evt);
    return true;
  }

  if (tag.includes('partial')) {
    updateASRStatus(state, { status: 'active', partialsDelta: 1 });
    return true;
  }

  if (tag.includes('start')) {
    updateASRStatus(state, { status: 'active', message: 'ASR stream started.' });
    return true;
  }

  return false;
}

function maybeHandlePolicyEvent(state, evt, kindTag, labelTag) {
  if (!state?.policyNodes?.container) return false;

  const combined = `${kindTag} ${labelTag}`.trim();
  const isPolicySignal =
    /\bnlu/.test(combined) ||
    /\bllm/.test(combined) ||
    /guardrail/.test(combined) ||
    /teacher/.test(combined) ||
    /move/.test(combined) ||
    /tool ?plan/.test(combined) ||
    /policy/.test(combined);
  if (!isPolicySignal) return false;

  updatePolicySnapshot(state, evt, combined);
  return true;
}

function startAdminSSE(state, onMatch) {
  try {
    const es = new EventSource('/api/v1/admin/logs?live=1', { withCredentials: true });
    es.onmessage = (ev) => {
      if (!ev.data) return;
      appendLog(state.adminLogEl, `${timestamp()} ${ev.data}`);
      try {
        const parsed = JSON.parse(ev.data);
        const evSid = String(parsed?.session_id || parsed?.sid || '');
        if (state.sid && evSid && evSid !== state.sid) return;
        onMatch?.(parsed);
      } catch {}
    };
    es.onerror = () => {
      appendLog(state.adminLogEl, `${timestamp()} [error] SSE connection problem (check network/auth).`);
    };
    return es;
  } catch (err) {
    appendLog(state.adminLogEl, `${timestamp()} [error] Failed to open SSE: ${err?.message || err}`);
    return null;
  }
}

function updateASRStatus(state, { status, partialsDelta = 0, final = false, transcript, message, error = false } = {}) {
  if (!state.asr) state.asr = { partials: 0, finals: 0, errors: 0 };
  if (partialsDelta) state.asr.partials += partialsDelta;
  if (final) state.asr.finals += 1;
  if (error) state.asr.errors += 1;
  if (transcript) state.latestTranscript = transcript;

  const parts = [];
  if (state.asr.partials) parts.push(`${state.asr.partials} partial${state.asr.partials === 1 ? '' : 's'}`);
  if (state.asr.finals) parts.push(`${state.asr.finals} final${state.asr.finals === 1 ? '' : 's'}`);
  if (state.latestTranscript && (final || state.asr.finals)) parts.push(`Transcript: “${state.latestTranscript}”`);

  const detail = parts.length ? parts.join(' • ') : (message || 'Waiting for ASR activity…');
  const resolvedStatus = status || (error ? 'error' : (state.asr.finals ? 'done' : (state.asr.partials ? 'active' : 'pending')));
  setStepStatus(state, 'asr', resolvedStatus, detail);
}

function resetPolicySnapshot(state) {
  if (!state) return;
  state.policySnapshot = {
    updatedAt: null,
    intent: null,
    guardrail: null,
    move: null,
    toolplan: null,
    lastEvent: null,
  };
  renderPolicySnapshot(state);
}

function updatePolicySnapshot(state, evt, combinedTag = '') {
  if (!state?.policyNodes) return;
  if (!state.policySnapshot) resetPolicySnapshot(state);

  const snapshot = state.policySnapshot;
  const data = extractEventData(evt);
  let touched = false;
  const changed = {
    intent: false,
    guardrail: false,
    move: false,
    toolplan: false,
  };
  const lowerCombined = (combinedTag || '').toLowerCase();

  const labelCandidate = typeof data.label === 'string' ? data.label : undefined;
  const eventLabelCandidate = typeof evt.label === 'string' ? evt.label : undefined;
  const sanitizedDataLabel = sanitizeIntentLabel(labelCandidate);
  const sanitizedEventLabel = sanitizeIntentLabel(eventLabelCandidate);

  const intentValue = firstDefined(
    data.intent,
    evt.intent,
    sanitizedEventLabel,
    data.name,
    sanitizedDataLabel
  );
  const confidenceValue = firstDefined(
    data.confidence,
    evt.confidence,
    data.score,
    evt.score,
    data.probability,
    evt.probability
  );
  if (intentValue) {
    snapshot.intent = {
      value: String(intentValue),
      confidence: normalizeConfidence(confidenceValue),
    };
    touched = true;
    changed.intent = true;
  }

  let guardrailDecision = firstDefined(
    data.decision,
    evt.decision,
    data.outcome,
    data.status,
    evt.status,
    data.guardrail
  );
  if (!guardrailDecision && /guardrail/.test(lowerCombined)) {
    if (lowerCombined.includes('allow')) guardrailDecision = 'allow';
    else if (lowerCombined.includes('block')) guardrailDecision = 'block';
    else if (lowerCombined.includes('pass')) guardrailDecision = 'pass';
    else if (lowerCombined.includes('fail')) guardrailDecision = 'fail';
  }

  const guardrailReason = firstDefined(
    data.reason,
    evt.reason,
    data.detail,
    data.details,
    data.rule,
    data.message
  );
  if (guardrailDecision || guardrailReason) {
    snapshot.guardrail = {
      decision: guardrailDecision ? String(guardrailDecision) : null,
      reason: guardrailReason ? String(guardrailReason) : null,
    };
    touched = true;
    changed.guardrail = true;
  }

  let moveValue = firstDefined(
    data.teacher_move,
    evt.teacher_move,
    data.move,
    evt.move,
    data.action,
    evt.action
  );
  if (!moveValue && /teacher/.test(lowerCombined)) {
    const match = lowerCombined.match(/teacher[._-]?move[=: ]?([a-z0-9_]+)/);
    if (match && match[1]) moveValue = match[1];
  }
  if (moveValue) {
    snapshot.move = { value: String(moveValue) };
    touched = true;
    changed.move = true;
  }

  let planValue = firstDefined(
    data.toolplan,
    evt.toolplan,
    data.tool_plan,
    data.tool_plan_summary,
    data.plan,
    evt.plan
  );
  if (planValue === undefined && /tool ?plan/.test(lowerCombined)) {
    const match = lowerCombined.match(/tool ?plan[=: ]?([a-z0-9_]+)/);
    if (match && match[1]) planValue = match[1];
  }
  if (planValue !== undefined) {
    snapshot.toolplan = { value: planValue };
    touched = true;
    changed.toolplan = true;
  }

  if (touched) {
    const now = new Date();
    snapshot.updatedAt = now;
    snapshot.lastEvent = {
      summary: buildPolicySummary(evt, combinedTag, snapshot, changed),
      kind: evt?.kind || null,
      label: evt?.label || null,
      updatedAt: now,
    };
    renderPolicySnapshot(state);
  }
}

function renderPolicySnapshot(state) {
  const nodes = state?.policyNodes;
  if (!nodes) return;
  const snapshot = state.policySnapshot || {};

  const intentDisplay = formatIntentDisplay(snapshot.intent);
  const intentConfidence = formatConfidence(snapshot.intent?.confidence);
  setFlowValue(nodes.intent, intentDisplay, {
    title: snapshot.intent?.value
      ? `Intent: ${snapshot.intent.value}${intentConfidence ? ` (${intentConfidence})` : ''}`
      : '',
  });

  const guardrailDisplay = formatGuardrailDisplay(snapshot.guardrail);
  const guardrailDecision = snapshot.guardrail?.decision;
  setFlowValue(nodes.guardrail, guardrailDisplay, {
    title: snapshot.guardrail?.reason || guardrailDecision || '',
    dataset: {
      decision: guardrailDecision ? String(guardrailDecision).toLowerCase() : '',
    },
  });

  setFlowValue(nodes.move, formatMoveDisplay(snapshot.move));
  let toolplanTitle = '';
  if (snapshot.toolplan && snapshot.toolplan.value !== undefined && snapshot.toolplan.value !== null) {
    try {
      toolplanTitle = typeof snapshot.toolplan.value === 'string'
        ? snapshot.toolplan.value
        : JSON.stringify(snapshot.toolplan.value, null, 2);
    } catch {
      toolplanTitle = String(snapshot.toolplan.value);
    }
  }
  setFlowValue(nodes.toolplan, formatToolplanDisplay(snapshot.toolplan), {
    title: toolplanTitle,
  });

  if (nodes.timestamp) {
    nodes.timestamp.textContent = snapshot.updatedAt ? formatTimestamp(snapshot.updatedAt) : '—';
  }

  if (nodes.lastEvent) {
    const last = snapshot.lastEvent || null;
    setFlowValue(nodes.lastEvent, formatLastEventDisplay(last), {
      isEmpty: !snapshot.updatedAt,
      emptyText: 'Waiting for policy signal…',
      title: formatLastEventTitle(last),
    });
  }

  if (nodes.container) {
    nodes.container.dataset.ready = snapshot.updatedAt ? 'true' : 'false';
  }
}

function extractEventData(evt) {
  if (!evt || typeof evt !== 'object') return {};
  const merged = {};
  const sources = [evt.data, evt.payload, evt.details, evt.context];
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue;
    for (const [key, value] of Object.entries(source)) {
      if (!(key in merged)) merged[key] = value;
    }
  }
  return merged;
}

function setFlowValue(node, value, options = {}) {
  if (!node) return;
  const { title, dataset, isEmpty, emptyText } = options;
  const stringValue = value === undefined || value === null ? '' : String(value);
  const empty = isEmpty ?? stringValue.trim() === '';
  node.textContent = empty ? (emptyText !== undefined ? emptyText : '—') : stringValue;
  node.dataset.empty = empty ? 'true' : 'false';
  if (title !== undefined) {
    node.title = title || '';
  }
  if (dataset && typeof dataset === 'object') {
    for (const [key, val] of Object.entries(dataset)) {
      if (!key) continue;
      if (val === undefined || val === null || val === '') delete node.dataset[key];
      else node.dataset[key] = String(val);
    }
  }
}

function firstDefined(...values) {
  for (const value of values) {
    if (value === undefined || value === null) continue;
    if (typeof value === 'string' && !value.trim()) continue;
    return value;
  }
  return undefined;
}

function normalizeConfidence(value) {
  if (value === undefined || value === null || value === '') return null;
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return null;
  return num;
}

function formatConfidence(conf) {
  const num = normalizeConfidence(conf);
  if (num === null) return null;
  if (num <= 1) return `${Math.round(num * 100)}%`;
  if (num <= 100) return `${Math.round(num)}%`;
  return `${num}`;
}

function formatIntentDisplay(entry) {
  if (!entry || !entry.value) return null;
  const confidence = formatConfidence(entry.confidence);
  return confidence ? `${entry.value} (${confidence})` : entry.value;
}

function sanitizeIntentLabel(label) {
  if (typeof label !== 'string') return undefined;
  const trimmed = label.trim();
  if (!trimmed) return undefined;
  const lower = trimmed.toLowerCase();
  if (lower === 'nlu.intent' || lower === 'intent' || lower === 'nlu.intent.detect') return undefined;
  return trimmed;
}

function formatGuardrailDisplay(entry) {
  if (!entry) return null;
  const pieces = [];
  if (entry.decision) pieces.push(entry.decision);
  if (entry.reason && entry.reason !== entry.decision) pieces.push(entry.reason);
  return pieces.length ? pieces.join(' — ') : null;
}

function formatMoveDisplay(entry) {
  if (!entry || !entry.value) return null;
  return entry.value;
}

function formatToolplanDisplay(entry) {
  if (!entry) return null;
  const value = entry.value;
  if (value === undefined || value === null) return null;
  if (typeof value === 'string') return value;
  try {
    const json = JSON.stringify(value);
    return json.length > 160 ? `${json.slice(0, 157)}…` : json;
  } catch {
    return String(value);
  }
}

function formatLastEventDisplay(entry) {
  if (!entry) return 'Waiting for policy signal…';
  return entry.summary || [entry.kind, entry.label].filter(Boolean).join(' • ') || 'Policy signal received';
}

function formatLastEventTitle(entry) {
  if (!entry) return '';
  const pieces = [];
  if (entry.kind) pieces.push(`kind=${entry.kind}`);
  if (entry.label) pieces.push(`label=${entry.label}`);
  if (entry.updatedAt instanceof Date && !Number.isNaN(entry.updatedAt.getTime())) {
    pieces.push(`@ ${formatTimestamp(entry.updatedAt)}`);
  }
  return pieces.join(' • ');
}

function formatTimestamp(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function buildPolicySummary(evt, combinedTag, snapshot, changed = {}) {
  const parts = [];
  const kind = evt?.kind;
  const label = evt?.label;
  if (kind) parts.push(kind);
  if (label && label !== kind) parts.push(label);

  if (changed.intent && snapshot?.intent?.value) {
    parts.push(`intent=${snapshot.intent.value}`);
  }
  if (changed.guardrail && snapshot?.guardrail?.decision) {
    parts.push(`guardrail=${snapshot.guardrail.decision}`);
  }
  if (changed.move && snapshot?.move?.value) {
    parts.push(`move=${snapshot.move.value}`);
  }
  if (changed.toolplan && snapshot?.toolplan && snapshot.toolplan.value !== undefined) {
    parts.push('toolplan updated');
  }

  if (!parts.length && combinedTag) parts.push(combinedTag);
  return parts.join(' • ') || 'Policy signal received';
}

function cleanupRun(state) {
  try { disarmVAD(); } catch {}
  try { closeWS(1000, 'admin_diag_end'); } catch {}
  if (state.wsListener) {
    window.removeEventListener('askchip-ws', state.wsListener);
    state.wsListener = null;
  }
  if (state.sse) {
    try { state.sse.close(); } catch {}
    state.sse = null;
  }
  if (state.stopMeter) {
    try { state.stopMeter(); } catch {}
    state.stopMeter = null;
  }
  state.awaitingGreeting = false;
  state.awaitingResponse = false;
  state.responseStarted = false;
  if (state.greetReject) state.greetReject(new Error('Diagnostic cancelled'));
  if (state.asrReject) state.asrReject(new Error('Diagnostic cancelled'));
  if (state.responseReject) state.responseReject(new Error('Diagnostic cancelled'));
  state.greetResolve = null;
  state.greetReject = null;
  state.asrResolve = null;
  state.asrReject = null;
  state.responseResolve = null;
  state.responseReject = null;
  state.asrFinalSatisfied = false;
  if (state.asrTimer) {
    clearTimeout(state.asrTimer);
    state.asrTimer = null;
  }
}

function setStepStatus(state, stepId, status, detail) {
  const entry = state.stepMap.get(stepId);
  if (!entry) return;
  if (status) {
    entry.el.dataset.status = status;
    if (entry.statusEl) entry.statusEl.textContent = STATUS_LABELS[status] || status;
  }
  if (detail !== undefined && entry.liveEl) {
    entry.liveEl.textContent = detail;
  }
}

function setSpeakState(state, mode) {
  if (!state.speakCard || !state.speakStateEl) return;
  state.speakCard.dataset.mode = mode;
  state.speakStateEl.textContent = SPEAK_LABELS[mode] || SPEAK_LABELS.idle;
}

function appendLog(el, line) {
  if (!el || !line) return;
  el.textContent = el.textContent ? `${el.textContent}\n${line}` : line;
  if (el.textContent.length > 14000) {
    el.textContent = el.textContent.slice(-14000);
  }
  el.scrollTop = el.scrollHeight;
  try { console.info('[admin-diagnostics]', line); } catch {}
}

function timestamp() {
  const t = new Date().toISOString();
  return t.split('T')[1].replace('Z', '').slice(0, 8);
}

function describeFrame(frame) {
  const type = frame?.type || frame?.label || 'unknown';
  if (type === 'assistant_audio') {
    const chunks = Array.isArray(frame.audio_chunks) ? frame.audio_chunks.length : 0;
    return `${type} chunks=${chunks}${frame.is_last ? ' (last)' : ''}`;
  }
  if (type === 'Results' || type === 'results' || type === 'transcript') {
    const text = extractTranscript(frame);
    const final = isTranscriptFinal(frame) ? ' final' : '';
    return `${type}${final}${text ? ` text="${text}"` : ''}`;
  }
  if (type === 'assistant_chunk' || type === 'assistant_text' || type === 'assistant_final') {
    const text = extractAssistantText(frame);
    return text ? `${type}: ${text}` : type;
  }
  if (type === 'Error') {
    return `Error ${frame.code || ''} ${frame.message || ''}`.trim();
  }
  return type;
}

function isAssistantFrame(frame) {
  const type = frame?.type || frame?.label || '';
  if (!type) return false;
  if (type.startsWith('assistant')) return true;
  if (frame?.role === 'assistant') return true;
  return false;
}

function isTranscriptFrame(frame) {
  const type = frame?.type || frame?.label || '';
  return type === 'Results' || type === 'results' || type === 'transcript';
}

function extractTranscript(frame) {
  try {
    return (
      frame?.channel?.alternatives?.[0]?.transcript ||
      frame?.alternatives?.[0]?.transcript ||
      frame?.transcript ||
      ''
    ).trim();
  } catch {
    return '';
  }
}

function isTranscriptFinal(frame) {
  try {
    return Boolean(
      frame?.channel?.is_final ||
      frame?.is_final ||
      frame?.final
    );
  } catch {
    return false;
  }
}

function extractAssistantText(frame) {
  try {
    return (
      frame?.text ||
      frame?.delta ||
      frame?.content ||
      ''
    ).toString().trim();
  } catch {
    return '';
  }
}

function summarizeAssistantFrame(frame) {
  if (!frame) return 'Assistant responded.';
  const type = frame.type || frame.label || 'assistant';
  if (type === 'assistant_audio') {
    const chunks = Array.isArray(frame.audio_chunks) ? frame.audio_chunks.length : 0;
    return `Assistant audio started (${chunks} chunk${chunks === 1 ? '' : 's'} in first frame).`;
  }
  const text = extractAssistantText(frame);
  return text ? `Assistant text: “${text}”` : `Assistant frame: ${type}`;
}

function findFirstIncompleteStep(state) {
  for (const step of STEP_DEFS) {
    const entry = state.stepMap.get(step.id);
    if (!entry) continue;
    if (entry.el.dataset.status !== 'done') return step.id;
  }
  return STEP_DEFS[STEP_DEFS.length - 1].id;
}

function logAdminEvent(state, label, extra = {}) {
  const payload = {
    kind: 'admin_diag',
    label,
    session_id: state.sid,
    ...extra,
  };
  try {
    fetch('/api/v1/admin/log', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': window.csrfToken || '',
      },
      body: JSON.stringify(payload),
      credentials: 'include',
    }).catch(() => {});
  } catch {}
}

/* ---------- Small helpers ---------- */

function delay(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/** Basic live input meter — no CSS required. */
function startMicMeter(stream, { barEl, dbEl }) {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    const ctx = new AC();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    src.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let raf = 0;

    function tick() {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0; for (let i = 0; i < buf.length; i++) { const v = buf[i]; sum += v * v; }
      const rms = Math.sqrt(sum / buf.length);
      const db = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
      const pct = Math.max(0, Math.min(1, (db + 60) / 60)); // -60..0 dBFS → 0..100%
      if (barEl) barEl.style.width = (pct * 100).toFixed(0) + '%';
      if (dbEl)  dbEl.textContent = Number.isFinite(db) ? db.toFixed(1) : '-∞';
      raf = requestAnimationFrame(tick);
    }
    tick();

    return () => {
      cancelAnimationFrame(raf);
      try { src.disconnect(); } catch {}
      try { analyser.disconnect(); } catch {}
      try { ctx.close(); } catch {}
    };
  } catch {
    return () => {};
  }
}
