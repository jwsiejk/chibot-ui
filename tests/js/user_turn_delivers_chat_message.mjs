import assert from 'node:assert/strict';
import { createTranscriptBridge } from '../../app/static/js/ws/transcript_bridge.js';

// Provide a minimal window object so transcript_bridge can deliver chat frames.
const captured = [];

globalThis.window = {
  TranscriptView: {
    handleChatMessage(frame) {
      captured.push(frame);
    },
  },
};

const bridge = createTranscriptBridge({
  AppState: {},
  hubLog: () => {},
  logStage: () => {},
  dispatchFrame: () => {},
});

const { deliverUserTurn } = bridge;
const frame = { type: 'user.turn', text: 'discuss Pure Storage together', turn_index: 1 };

assert.doesNotThrow(() => {
  deliverUserTurn(frame);
});

assert.equal(captured.length, 1, 'deliverChat should receive one frame');
const message = captured[0];
assert.equal(message.type, 'chat.message');
assert.equal(message.role, 'user');
assert.equal(message.text, 'discuss Pure Storage together');
assert.equal(message.turn_index, 1);

console.log(JSON.stringify({ ok: true, message }));
