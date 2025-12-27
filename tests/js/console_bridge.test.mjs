import assert from "node:assert/strict";
import { initConsoleBridge } from "../../app/static/js/console_bridge.js";
import {
  rateLimitClientLog,
  resetClientLogRateLimiter,
  shouldForwardClientLog,
} from "../../app/static/js/ws/telemetry.js";

function createMockConsole() {
  const calls = {
    log: [],
    info: [],
    warn: [],
    error: [],
    debug: [],
  };
  return {
    calls,
    log: (...args) => calls.log.push(args),
    info: (...args) => calls.info.push(args),
    warn: (...args) => calls.warn.push(args),
    error: (...args) => calls.error.push(args),
    debug: (...args) => calls.debug.push(args),
  };
}

function createEmitSpy() {
  const calls = [];
  return {
    calls,
    emit: (...args) => calls.push(args),
  };
}

function setClientLogFlag(enabled) {
  globalThis.window = {
    location: { search: enabled ? "?clientLogs=1" : "" },
    localStorage: {
      getItem: () => null,
    },
  };
}

setClientLogFlag(false);
resetClientLogRateLimiter();
{
  const mockConsole = createMockConsole();
  const emitSpy = createEmitSpy();
  initConsoleBridge({
    consoleObj: mockConsole,
    emitClientLog: emitSpy.emit,
    shouldForwardClientLog,
    rateLimitClientLog,
  });

  mockConsole.log("hello");
  mockConsole.info("info");
  mockConsole.debug("debug");
  mockConsole.warn("warn");
  mockConsole.error("error");

  const labels = emitSpy.calls.map((entry) => entry[0]);
  assert.deepEqual(labels, ["console.warn", "console.error"]);
}

setClientLogFlag(true);
resetClientLogRateLimiter();
{
  const mockConsole = createMockConsole();
  const emitSpy = createEmitSpy();
  initConsoleBridge({
    consoleObj: mockConsole,
    emitClientLog: emitSpy.emit,
    shouldForwardClientLog,
    rateLimitClientLog,
  });

  for (let i = 0; i < 10; i += 1) {
    mockConsole.log("spam", i);
  }

  const labels = emitSpy.calls.map((entry) => entry[0]);
  const consoleLogCalls = labels.filter((label) => label === "console.log");
  assert.equal(consoleLogCalls.length, 5);
  assert.ok(labels.length <= 6);
}
