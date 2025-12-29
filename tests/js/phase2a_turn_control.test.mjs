import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createWsAudioRuntime } from "../../app/static/js/audio/ws_audio_runtime.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../..");

{
  const wsClientPath = path.join(repoRoot, "app/static/js/ws_client.js");
  const content = fs.readFileSync(wsClientPath, "utf8");
  assert.ok(!content.includes("firstTurnBootstrap"), "bootstrap turn-start helpers should be removed");
  assert.ok(!content.includes("postGreetSpeechWatchdog"), "post-greet speech watchdog should be removed");
  assert.ok(!/type:\s*\"client\.turn_start\"/.test(content), "ws_client should not emit client.turn_start frames");
}

{
  globalThis.WebSocket = { OPEN: 1 };

  const jsonCalls = [];
  const audioCalls = [];

  const appState = {
    policy: {
      deepgramV3Enabled: true,
      deepgramV3TurnControlEnabled: true,
      deepgramV3TelemetryEnabled: false,
    },
    wsPhase: "ready",
    phase: "ConversationReady",
    targetSampleRate: 16000,
    preSpeechBufferMs: 20,
    getState() {
      return this;
    },
  };

  const runtime = createWsAudioRuntime({
    AppState: appState,
    initPcmSender: () => ({}),
    updateState: () => {},
    logStage: () => {},
    getSocket: () => ({ readyState: 1 }),
    sendAudioChunk: (payload, meta) => {
      audioCalls.push({ payload, meta });
    },
    sendJSON: (payload) => {
      jsonCalls.push(payload);
    },
    isAudioStreaming: () => true,
    canCaptureNow: () => true,
    isSenderPaused: () => false,
    getVadController: () => ({
      getState: () => ({ state: "speech", isSpeech: true, rms: 0.5 }),
    }),
    getCurrentTurnReqId: () => "turn-1",
  });

  const prerollFrame = new Int16Array(320);
  runtime.handlePcmFrame(prerollFrame, { sampleRate: 16000, seq: 1 });

  const liveFrame = new Int16Array(320);
  runtime.handlePcmSend(liveFrame, { sampleRate: 16000, seq: 2, chunkCount: 1 });
  runtime.handlePcmSend(liveFrame, { sampleRate: 16000, seq: 3, chunkCount: 1 });

  const turnStarts = jsonCalls.filter((payload) => payload?.type === "client.turn_start");
  assert.equal(turnStarts.length, 1, "speech should emit one client.turn_start");

  const prerollSends = audioCalls.filter((call) => call?.meta?.preRoll === true);
  assert.equal(prerollSends.length, 1, "speech should send a single preroll slice");

  runtime.clearAudioKeepaliveTimer();
}
