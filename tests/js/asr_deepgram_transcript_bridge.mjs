import assert from 'node:assert/strict';
import { createTranscriptBridge } from '../../app/static/js/ws/transcript_bridge.js';

const partials = [];
const finals = [];

globalThis.window = {
  TranscriptView: {
    handlePartial(frame) {
      partials.push(frame);
    },
    handleFinal(frame) {
      finals.push(frame);
    },
  },
};

const bridge = createTranscriptBridge({
  AppState: {},
  hubLog: () => {},
  logStage: () => {},
  dispatchFrame: () => {},
});

const { deliverAsr } = bridge;

const partialFrame = {
  type: 'asr.partial',
  text: 'hello',
  vendor: 'deepgram',
  sid: 'sid-1',
};

const finalFrame = {
  type: 'asr.final',
  text: 'hello world',
  vendor: 'deepgram',
  sid: 'sid-1',
};

assert.doesNotThrow(() => {
  deliverAsr(partialFrame);
  deliverAsr(finalFrame);
});

assert.equal(partials.length, 1, 'expected one partial frame');
assert.equal(finals.length, 1, 'expected one final frame');
assert.strictEqual(partials[0], partialFrame, 'partial frame should be passed through');
assert.strictEqual(finals[0], finalFrame, 'final frame should be passed through');
assert.equal(partials[0].vendor, 'deepgram');
assert.equal(finals[0].vendor, 'deepgram');

console.log(JSON.stringify({ ok: true }));
