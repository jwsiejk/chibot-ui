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

export type BootstrapDependency = 'config' | 'sessions' | 'readiness';

export class BootstrapDependencyError extends Error {
  dependency: BootstrapDependency;

  constructor(dependency: BootstrapDependency, detail: string) {
    super(detail);
    this.name = 'BootstrapDependencyError';
    this.dependency = dependency;
  }
}

function toDependencyError(dependency: BootstrapDependency, fallbackMessage: string, reason: unknown): BootstrapDependencyError {
  if (reason instanceof Error && reason.message.trim()) {
    return new BootstrapDependencyError(dependency, reason.message);
  }
  return new BootstrapDependencyError(dependency, fallbackMessage);
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
    throw toDependencyError('config', 'Failed to load AskChip config.', config.reason);
  }

  if (sessions.status !== 'fulfilled') {
    throw toDependencyError('sessions', 'Failed to load sessions.', sessions.reason);
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
    readinessError: toDependencyError('readiness', 'Failed to load readiness diagnostics.', readinessResult.reason).message,
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
