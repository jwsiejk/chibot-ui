import assert from "node:assert/strict";
import { createTurnRuntime } from "../../app/static/js/ws/turns.js";
import { createWsAudioRuntime } from "../../app/static/js/audio/ws_audio_runtime.js";

globalThis.WebSocket = { OPEN: 1 };

{
  const socket = { readyState: 1 };
  const appState = {
    ttsActive: true,
    tts: true,
    asrReady: false,
    wsPhase: "connecting",
    micPermissionGranted: true,
    getState() {
      return this;
    },
  };

  const runtime = createTurnRuntime({
    AppState: appState,
    helpers: {
      getSocket: () => socket,
    },
  });

  runtime._setFirstChunkSeen(true);
  runtime._setArmingGraceUntil(0);

  assert.equal(runtime.canCaptureNow(), true, "ttsActive/asrReady/wsPhase should not block capture");

  runtime._setSenderPauseReason("user_muted", true);
  assert.equal(runtime.canCaptureNow(), false, "local pause/mute should block capture");

  runtime._setSenderPauseReason("user_muted", false);
  assert.equal(runtime.canCaptureNow(), true, "capture resumes after local pause cleared");
}

{
  const audioCalls = [];
  const socket = { readyState: 1 };
  const appState = {
    policy: {
      deepgramV3Enabled: true,
      deepgramV3TurnControlEnabled: false,
    },
    wsPhase: "booting",
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
    getSocket: () => socket,
    sendAudioChunk: (payload, meta) => {
      audioCalls.push({ payload, meta });
    },
    sendJSON: () => {},
    isAudioStreaming: () => true,
    canCaptureNow: () => true,
    isSenderPaused: () => false,
    getVadController: () => ({
      getState: () => ({ state: "speech", isSpeech: true, rms: 0.5 }),
    }),
    getCurrentTurnReqId: () => "turn-1",
  });

  const frame = new Int16Array(320);
  runtime.handlePcmSend(frame, { sampleRate: 16000, seq: 1, chunkCount: 1 });

  assert.equal(
    audioCalls.length,
    1,
    "wsPhase not ready should not block audio send when socket is open"
  );

  runtime.clearAudioKeepaliveTimer();
}
