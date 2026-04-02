import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  createReadinessPoller,
  isReadinessSettled,
  loadBootstrapShellData,
} from '../.test-dist/state/readinessPolling.js';
import { getSendingDisabledReason } from '../.test-dist/state/controllerHelpers.js';

function readiness(overrides = {}) {
  return {
    local_only: true,
    warmup_active: true,
    checks: {
      ollama: {
        label: 'Ollama model',
        status: 'pending',
        detail: 'warming',
        checked_at: null,
        optional: false,
      },
      tts: {
        label: 'Kokoro speech',
        status: 'pending',
        detail: 'warming',
        checked_at: null,
        optional: true,
      },
    },
    ...overrides,
  };
}

function createScheduler() {
  let nextId = 1;
  const timers = new Map();
  return {
    timers,
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay, cleared: false });
      return id;
    },
    clearTimeout(id) {
      const timer = timers.get(id);
      if (timer) {
        timer.cleared = true;
      }
      timers.delete(id);
    },
    runNext() {
      const [id, timer] = timers.entries().next().value ?? [];
      if (!timer) {
        return false;
      }
      timers.delete(id);
      timer.callback();
      return true;
    },
  };
}

describe('readiness bootstrap hardening', () => {
  it('treats readiness loading as best-effort while core config and sessions still initialize', async () => {
    const result = await loadBootstrapShellData({
      getConfig: async () => ({ app_name: 'AskChip', ollama_base_url: '', ollama_model: 'phi4', database_path: '', stt_model: '', stt_device: '', stt_compute_type: '', tts_voice: '', tts_requested_device: 'auto', tts_device: 'cpu', tts_provider: 'CPUExecutionProvider', tts_available_providers: ['CPUExecutionProvider'], tts_warning: null, tts_fallback_reason: null, tts_model_path: null, tts_voices_path: null, tts_sample_rate_hz: 24000, tts_speed: 1, tts_lang_code: 'en', local_only: true, ollama_warmup_enabled: true, tts_warmup_enabled: true }),
      loadSessions: async () => ([{ id: 'session-1', title: 'Chat', status: 'ready', created_at: '', updated_at: '', last_message_at: null, active_turn_id: null, ready_at: null, last_error_at: null, metadata: {} }]),
      getReadiness: async () => { throw new Error('readiness fetch failed'); },
    });

    assert.equal(result.config.ollama_model, 'phi4');
    assert.equal(result.sessions.length, 1);
    assert.equal(result.readiness, null);
    assert.equal(result.readinessError, 'readiness fetch failed');
    assert.equal(getSendingDisabledReason({ currentSessionId: result.sessions[0].id, pendingTurn: false, topLevelState: 'ready' }), null);
  });

  it('still throws when the core API bootstrap path fails', async () => {
    await assert.rejects(() => loadBootstrapShellData({
      getConfig: async () => { throw new Error('config unavailable'); },
      loadSessions: async () => ([]),
      getReadiness: async () => readiness(),
    }), /config unavailable/);
  });
});

describe('readiness poller', () => {
  it('recognizes settled readiness snapshots without expanding top-level states', () => {
    assert.equal(isReadinessSettled(readiness()), false);
    assert.equal(isReadinessSettled(readiness({ warmup_active: false, checks: { ollama: { label: 'Ollama model', status: 'ready', detail: 'ok', checked_at: '2026-03-22T00:00:00Z', optional: false }, tts: { label: 'Kokoro speech', status: 'failed', detail: 'optional failure', checked_at: '2026-03-22T00:00:00Z', optional: true } } })), true);
  });

  it('stops polling once warm-up settles', async () => {
    const scheduler = createScheduler();
    const snapshots = [
      readiness(),
      readiness({
        warmup_active: false,
        checks: {
          ollama: { label: 'Ollama model', status: 'ready', detail: 'ok', checked_at: '2026-03-22T00:00:00Z', optional: false },
          tts: { label: 'Kokoro speech', status: 'ready', detail: 'ok', checked_at: '2026-03-22T00:00:00Z', optional: true },
        },
      }),
    ];
    const updates = [];
    const poller = createReadinessPoller({
      getReadiness: async () => snapshots.shift(),
      onUpdate: (value) => updates.push(value),
      onError: () => {},
      scheduler,
      intervalMs: 25,
      maxAttempts: 5,
    });

    poller.start();
    assert.equal(scheduler.timers.size, 1);
    scheduler.runNext();
    await Promise.resolve();
    assert.equal(updates.length, 1);
    assert.equal(scheduler.timers.size, 1);

    scheduler.runNext();
    await Promise.resolve();
    assert.equal(updates.length, 2);
    assert.equal(scheduler.timers.size, 0);
  });

  it('cleans up scheduled readiness polling on stop/unmount', () => {
    const scheduler = createScheduler();
    const poller = createReadinessPoller({
      getReadiness: async () => readiness(),
      onUpdate: () => {},
      onError: () => {},
      scheduler,
      intervalMs: 25,
      maxAttempts: 5,
    });

    poller.start();
    assert.equal(scheduler.timers.size, 1);
    poller.stop();
    assert.equal(scheduler.timers.size, 0);
  });
});
