import type { ConfigResponse, ReadinessResponse, SessionRecord } from '../types/contract';

const DEFAULT_READINESS_POLL_INTERVAL_MS = 3000;
const DEFAULT_READINESS_POLL_MAX_ATTEMPTS = 10;

export interface BootstrapShellDataDependencies {
  getConfig: () => Promise<ConfigResponse>;
  getReadiness: () => Promise<ReadinessResponse>;
  loadSessions: () => Promise<SessionRecord[]>;
}

export interface BootstrapShellDataResult {
  config: ConfigResponse;
  sessions: SessionRecord[];
  readiness: ReadinessResponse | null;
  readinessError: string | null;
}

export function isReadinessSettled(readiness: ReadinessResponse | null): boolean {
  if (!readiness) {
    return true;
  }
  if (readiness.warmup_active) {
    return false;
  }
  return Object.values(readiness.checks).every((check) => check.status !== 'pending');
}

export async function loadBootstrapShellData(deps: BootstrapShellDataDependencies): Promise<BootstrapShellDataResult> {
  const [config, sessions, readinessResult] = await Promise.allSettled([
    deps.getConfig(),
    deps.loadSessions(),
    deps.getReadiness(),
  ]);

  if (config.status !== 'fulfilled') {
    throw config.reason;
  }

  if (sessions.status !== 'fulfilled') {
    throw sessions.reason;
  }

  if (readinessResult.status === 'fulfilled') {
    return {
      config: config.value,
      sessions: sessions.value,
      readiness: readinessResult.value,
      readinessError: null,
    };
  }

  return {
    config: config.value,
    sessions: sessions.value,
    readiness: null,
    readinessError: readinessResult.reason instanceof Error ? readinessResult.reason.message : 'Failed to load readiness diagnostics.',
  };
}

export interface ReadinessPollerOptions {
  getReadiness: () => Promise<ReadinessResponse>;
  onUpdate: (readiness: ReadinessResponse) => void;
  onError: (message: string) => void;
  intervalMs?: number;
  maxAttempts?: number;
  scheduler?: Pick<typeof window, 'setTimeout' | 'clearTimeout'>;
}

export function createReadinessPoller(options: ReadinessPollerOptions) {
  const scheduler = options.scheduler ?? window;
  const intervalMs = options.intervalMs ?? DEFAULT_READINESS_POLL_INTERVAL_MS;
  const maxAttempts = options.maxAttempts ?? DEFAULT_READINESS_POLL_MAX_ATTEMPTS;
  let timerId: number | null = null;
  let stopped = false;
  let attempts = 0;

  const clearTimer = () => {
    if (timerId !== null) {
      scheduler.clearTimeout(timerId);
      timerId = null;
    }
  };

  const stop = () => {
    stopped = true;
    clearTimer();
  };

  const scheduleNext = () => {
    if (stopped || attempts >= maxAttempts) {
      return;
    }
    clearTimer();
    timerId = scheduler.setTimeout(() => {
      timerId = null;
      void poll();
    }, intervalMs);
  };

  const poll = async () => {
    if (stopped || attempts >= maxAttempts) {
      return;
    }

    attempts += 1;
    try {
      const readiness = await options.getReadiness();
      if (stopped) {
        return;
      }
      options.onUpdate(readiness);
      if (isReadinessSettled(readiness)) {
        stop();
        return;
      }
    } catch (error) {
      if (stopped) {
        return;
      }
      options.onError(error instanceof Error ? error.message : 'Failed to refresh readiness diagnostics.');
    }

    scheduleNext();
  };

  return {
    start() {
      if (!stopped && attempts === 0) {
        scheduleNext();
      }
    },
    stop,
    isActive() {
      return !stopped && (timerId !== null || attempts > 0);
    },
  };
}
