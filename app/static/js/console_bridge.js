import { emitClientLog, rateLimitClientLog, shouldForwardClientLog } from "./ws/telemetry.js";

const DEFAULT_LEVELS = ["log", "info", "warn", "error", "debug"];
const NOISY_CONSOLE_LABELS = new Set(["client.pcm_sender.frame_received"]);

function formatArg(value) {
  if (typeof value === "string") {
    return value;
  }
  try {
    const serialized = JSON.stringify(value);
    if (typeof serialized === "string") {
      return serialized;
    }
  } catch (_) {}
  try {
    return String(value);
  } catch (_) {
    return "[unprintable]";
  }
}

function buildMessage(args) {
  return args.map((arg) => formatArg(arg)).join(" ");
}

function isNoisyConsoleMessage(message, args) {
  if (NOISY_CONSOLE_LABELS.has(message)) {
    return true;
  }
  for (const arg of args) {
    if (typeof arg === "string" && NOISY_CONSOLE_LABELS.has(arg)) {
      return true;
    }
    if (arg && typeof arg === "object" && !Array.isArray(arg) && NOISY_CONSOLE_LABELS.has(arg.message)) {
      return true;
    }
  }
  return false;
}

export function initConsoleBridge(options = {}) {
  try {
    const c = options.consoleObj || (typeof console === "object" ? console : null);
    if (!c || c.__askchip_patched) {
      return;
    }
    const levels = Array.isArray(options.levels) && options.levels.length ? options.levels : DEFAULT_LEVELS;
    const emit = typeof options.emitClientLog === "function" ? options.emitClientLog : emitClientLog;
    const rateLimit = typeof options.rateLimitClientLog === "function" ? options.rateLimitClientLog : rateLimitClientLog;
    const shouldForward = typeof options.shouldForwardClientLog === "function"
      ? options.shouldForwardClientLog
      : shouldForwardClientLog;

    levels.forEach((level) => {
      const original = typeof c[level] === "function" ? c[level].bind(c) : () => {};
      c[level] = (...args) => {
        if (c.__askchip_console_bridge_active) {
          return original(...args);
        }
        const message = buildMessage(args);
        const isNoisy = isNoisyConsoleMessage(message, args);
        try {
          c.__askchip_console_bridge_active = true;
          const label = `console.${level}`;
          if (!isNoisy && shouldForward?.(level, label)) {
            const rate = rateLimit?.(label, level);
            if (!rate || rate.allowed) {
              emit?.(label, { message, level }, { level, rateLimit: false });
            } else if (rate.summary && shouldForward?.("debug", "client.log_dropped")) {
              emit?.("client.log_dropped", rate.summary, { level: "debug", rateLimit: false });
            }
          }
        } catch (_) {
        } finally {
          c.__askchip_console_bridge_active = false;
        }

        if (isNoisy) {
          return;
        }

        return original(...args);
      };
    });

    c.__askchip_patched = true;
  } catch (_) {}
}
