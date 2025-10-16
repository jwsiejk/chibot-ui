import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrapLegacyFacade } from '../../static/js/voice/legacy/FacadeBootstrapLegacy.js';

test('bootstrapLegacyFacade wires overrides and aliases', () => {
  const recorded = {};
  const registerVoiceLegacyFacade = (overrides) => {
    recorded.overrides = overrides;
  };
  const state = { rec: { state: 'recording' } };
  const vadCalls = [];
  const VadFrameUtils = {
    ensureMic: (stream) => {
      vadCalls.push(['ensureMic', stream]);
      return 'mic';
    },
    disarm: () => {
      vadCalls.push(['disarm']);
    },
  };
  const armArgs = [];
  const arm = (...args) => {
    armArgs.push(args);
  };
  const bargeIn = () => {
    recorded.barged = true;
  };
  const setGreetGateActive = (active) => {
    recorded.gate = active;
  };
  const forceBargeInStart = (meta) => {
    recorded.startMeta = meta;
  };
  const forceBargeInEnd = (opts) => {
    recorded.endOpts = opts;
  };
  const legacyOnWsOpenImpl = (detail) => {
    recorded.wsOpen = detail;
  };
  const legacyOnWsMessageImpl = (detail, helpers) => {
    recorded.wsMessage = [detail, helpers];
  };
  const legacyOnWsCloseImpl = (detail) => {
    recorded.wsClose = detail;
  };
  const legacyOnMicAvailable = (detail) => {
    recorded.micAvailable = detail;
  };
  const legacyOnMicStop = (detail) => {
    recorded.micStop = detail;
  };
  const legacyOnRecorderData = (event, helpers) => {
    recorded.recorderData = [event, helpers];
  };
  const legacyOnRecorderError = (event, helpers) => {
    recorded.recorderError = [event, helpers];
  };
  const legacyResetEvidenceGate = () => 'reset';
  const legacyClearSafetyCloseTimer = () => 'clear';
  const legacyCloseTurnIfOpen = () => 'close';
  const legacySendRecorderChunk = () => 'send';
  const legacyStopRecorder = () => 'stop';

  const aliases = bootstrapLegacyFacade({
    registerVoiceLegacyFacade,
    VadFrameUtils,
    state,
    arm,
    bargeIn,
    setGreetGateActive,
    forceBargeInStart,
    forceBargeInEnd,
    legacyOnWsOpenImpl,
    legacyOnWsMessageImpl,
    legacyOnWsCloseImpl,
    legacyOnMicAvailable,
    legacyOnMicStop,
    legacyOnRecorderData,
    legacyOnRecorderError,
    legacyResetEvidenceGate,
    legacyClearSafetyCloseTimer,
    legacyCloseTurnIfOpen,
    legacySendRecorderChunk,
    legacyStopRecorder,
  });

  assert.equal(typeof recorded.overrides, 'object');

  recorded.overrides.initMic('stream-1');
  recorded.overrides.disarmVAD();
  recorded.overrides.armVAD('stream-2', { foo: true });
  recorded.overrides.bargeIn();
  recorded.overrides.setGreetGateActive(false);
  recorded.overrides.forceBargeInStart({ reason: 'test' });
  recorded.overrides.forceBargeInEnd({ done: true });
  recorded.overrides.onWsOpen('open');
  recorded.overrides.onWsMessage({ foo: 'bar' }, { helper: true });
  recorded.overrides.onWsClose('close');
  recorded.overrides.onMicAvailable({ mic: true });
  recorded.overrides.onMicStop({ mic: false });
  recorded.overrides.onRecorderData('event', { chunk: 1 });
  recorded.overrides.onRecorderError('err', { cause: 'oops' });

  assert.equal(recorded.overrides.isRecording(), true);
  recorded.overrides.setVadBoost?.(123);

  assert.deepEqual(vadCalls, [
    ['ensureMic', 'stream-1'],
    ['disarm'],
  ]);
  assert.deepEqual(armArgs, [['stream-2', { foo: true }]]);
  assert.equal(recorded.barged, true);
  assert.equal(recorded.gate, false);
  assert.deepEqual(recorded.startMeta, { reason: 'test' });
  assert.deepEqual(recorded.endOpts, { done: true });
  assert.equal(recorded.wsOpen, 'open');
  assert.deepEqual(recorded.wsMessage, [{ foo: 'bar' }, { helper: true }]);
  assert.equal(recorded.wsClose, 'close');
  assert.deepEqual(recorded.micAvailable, { mic: true });
  assert.deepEqual(recorded.micStop, { mic: false });
  assert.deepEqual(recorded.recorderData, ['event', { chunk: 1 }]);
  assert.deepEqual(recorded.recorderError, ['err', { cause: 'oops' }]);

  assert.equal(aliases.resetEvidenceGate, legacyResetEvidenceGate);
  assert.equal(aliases.clearSafetyCloseTimer, legacyClearSafetyCloseTimer);
  assert.equal(aliases.closeTurnIfOpen, legacyCloseTurnIfOpen);
  assert.equal(aliases.sendRecorderChunk, legacySendRecorderChunk);
  assert.equal(aliases.stopRecorder, legacyStopRecorder);
});

