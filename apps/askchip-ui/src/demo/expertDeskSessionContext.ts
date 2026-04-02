import type { ExpertDeskIntakeDraft } from './types';
import type { ExpertDeskRecommendation } from './recommendation';
import {
  getContactPreferenceLabel,
  getEnvironmentPlatformLabel,
  getIssueCategoryLabel,
  getRecommendedPathLabel,
  getUrgencyLabel,
} from './recommendation';
import type { ExpertDeskUploadedLogMetadata, ExpertDeskUploadedLogSource } from './types';

const SESSION_CONTEXT_STORAGE_KEY = 'askchip.expertDesk.sessionContextBySessionId.v1';
const SESSION_CONTEXT_MAX_AGE_MS = 1000 * 60 * 60 * 12;

type SessionContextRecord = {
  context: ExpertDeskSessionContext;
  storedAt: string;
  localHandoffRequest: ExpertDeskLocalHandoffRequest | null;
};

type SessionContextStorage = Record<string, SessionContextRecord>;

export type ExpertDeskSessionContext = {
  requestLabel: string;
  issueCategoryLabel: string;
  environment: string;
  urgencyLabel: string;
  expertPersona: string;
  recommendedPathLabel: string;
  recommendedNextStep: string;
  likelyTopicHint: string;
  escalationNote: string;
  retrievedCaseContext: string[];
  sourceNote: string;
  uploadedLogFiles: ExpertDeskUploadedLogMetadata[];
};

export type ExpertDeskLocalHandoffRequestType = 'human-escalation' | 'follow-up-session';

export type ExpertDeskLocalHandoffRequest = {
  type: ExpertDeskLocalHandoffRequestType;
  note: string;
  updatedAt: string;
};

function isExpired(storedAt: string): boolean {
  const storedTimestamp = new Date(storedAt).getTime();

  if (Number.isNaN(storedTimestamp)) {
    return true;
  }

  return Date.now() - storedTimestamp > SESSION_CONTEXT_MAX_AGE_MS;
}

function readStorage(): SessionContextStorage {
  if (typeof window === 'undefined') {
    return {};
  }

  const raw = window.sessionStorage.getItem(SESSION_CONTEXT_STORAGE_KEY);

  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw) as SessionContextStorage;
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }
    return parsed;
  } catch {
    return {};
  }
}

function writeStorage(storage: SessionContextStorage): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.sessionStorage.setItem(SESSION_CONTEXT_STORAGE_KEY, JSON.stringify(storage));
}

export function saveExpertDeskSessionContext(sessionId: string, context: ExpertDeskSessionContext): void {
  if (typeof window === 'undefined') {
    return;
  }

  const storage = readStorage();
  const nextStorage: SessionContextStorage = {};

  Object.entries(storage).forEach(([key, value]) => {
    if (!isExpired(value.storedAt)) {
      nextStorage[key] = value;
    }
  });

  nextStorage[sessionId] = {
    context,
    storedAt: new Date().toISOString(),
    localHandoffRequest: storage[sessionId]?.localHandoffRequest ?? null,
  };

  writeStorage(nextStorage);
}

export function addExpertDeskSessionLogFiles(
  sessionId: string,
  files: FileList,
  source: ExpertDeskUploadedLogSource,
): ExpertDeskSessionContext | null {
  if (typeof window === 'undefined') {
    return null;
  }

  const storage = readStorage();
  const existing = storage[sessionId];
  if (!existing || isExpired(existing.storedAt)) {
    if (existing) {
      delete storage[sessionId];
      writeStorage(storage);
    }
    return null;
  }

  const uploadedAt = new Date().toISOString();
  const newMetadata: ExpertDeskUploadedLogMetadata[] = Array.from(files).map((file) => ({
    name: file.name,
    size: file.size,
    type: file.type || '',
    uploaded_at: uploadedAt,
    uploaded_in: source,
  }));

  const nextContext: ExpertDeskSessionContext = {
    ...existing.context,
    uploadedLogFiles: [...existing.context.uploadedLogFiles, ...newMetadata],
  };

  storage[sessionId] = {
    ...existing,
    context: nextContext,
    storedAt: new Date().toISOString(),
  };
  writeStorage(storage);
  return nextContext;
}

export function getExpertDeskSessionContext(sessionId: string): ExpertDeskSessionContext | null {
  const storage = readStorage();
  const record = storage[sessionId];

  if (!record) {
    return null;
  }

  if (isExpired(record.storedAt)) {
    delete storage[sessionId];
    writeStorage(storage);
    return null;
  }

  return record.context;
}

export function getExpertDeskLocalHandoffRequest(sessionId: string): ExpertDeskLocalHandoffRequest | null {
  const storage = readStorage();
  const record = storage[sessionId];

  if (!record) {
    return null;
  }

  if (isExpired(record.storedAt)) {
    delete storage[sessionId];
    writeStorage(storage);
    return null;
  }

  return record.localHandoffRequest ?? null;
}

export function saveExpertDeskLocalHandoffRequest(
  sessionId: string,
  request: ExpertDeskLocalHandoffRequest,
  fallbackContext: ExpertDeskSessionContext,
): void {
  if (typeof window === 'undefined') {
    return;
  }

  const storage = readStorage();
  const existing = storage[sessionId];
  const context = existing?.context ?? fallbackContext;

  storage[sessionId] = {
    context,
    localHandoffRequest: request,
    storedAt: new Date().toISOString(),
  };

  writeStorage(storage);
}

function buildLikelyTopicHint(draft: ExpertDeskIntakeDraft, recommendation: ExpertDeskRecommendation): string {
  if (draft.errorText.trim().length > 0) {
    return 'Error snippets suggest tracing the failing integration boundary first.';
  }

  if (draft.issueCategory === 'production-outage') {
    return 'Outage pattern points to dependency health checks, rollback safety, and mitigation sequencing.';
  }

  if (draft.issueCategory === 'integration-failure') {
    return 'Likely root cause is an API contract or upstream dependency mismatch in the integration path.';
  }

  if (draft.issueCategory === 'security-review') {
    return 'Topic focus is security controls, risk ownership, and required review evidence.';
  }

  if (draft.issueCategory === 'migration-planning') {
    return 'Likely focus is migration sequencing, cutover risk, and data consistency checkpoints.';
  }

  if (recommendation.confidence === 'medium') {
    return 'Intake detail is partial; start by clarifying impact timeline and recent platform changes.';
  }

  return 'Start with impact scope, known constraints, and the highest-risk dependency path.';
}

function buildNextStep(recommendation: ExpertDeskRecommendation): string {
  switch (recommendation.recommendedPath) {
    case 'launch-live-expert-now':
      return 'Open immediate live troubleshooting and align on owners, scope, and mitigation.';
    case 'request-follow-up-session':
      return 'Capture required prep details and request a structured follow-up specialist session.';
    case 'escalate-human-expert':
      return 'Escalate to a human specialist path with clear risk and accountability notes.';
    case 'continue-ai-now':
    default:
      return 'Run focused AI triage first, then decide whether specialist escalation is required.';
  }
}

function buildEscalationNote(recommendation: ExpertDeskRecommendation): string {
  if (recommendation.recommendedPath === 'escalate-human-expert') {
    return 'Escalation recommended by routing rules based on intake urgency and issue pattern.';
  }

  if (recommendation.recommendedPath === 'launch-live-expert-now') {
    return 'Escalation option remains available if live triage confirms broader impact.';
  }

  return 'Escalation is optional and can be triggered if risk, blast radius, or urgency increases.';
}

export function buildExpertDeskSessionContextFromDraft(
  draft: ExpertDeskIntakeDraft,
  recommendation: ExpertDeskRecommendation,
): ExpertDeskSessionContext {
  const issueCategoryLabel = getIssueCategoryLabel(draft.issueCategory);
  const urgencyLabel = getUrgencyLabel(draft.urgency);
  const recommendedPathLabel = getRecommendedPathLabel(recommendation.recommendedPath);

  return {
    requestLabel: `Request: ${issueCategoryLabel}`,
    issueCategoryLabel,
    environment: getEnvironmentPlatformLabel(draft.environmentPlatform),
    urgencyLabel,
    expertPersona: recommendation.expertPersonaLabel,
    recommendedPathLabel,
    recommendedNextStep: buildNextStep(recommendation),
    likelyTopicHint: buildLikelyTopicHint(draft, recommendation),
    escalationNote: buildEscalationNote(recommendation),
    retrievedCaseContext: [
      `Contact preference: ${getContactPreferenceLabel(draft.contactPreference)}`,
      draft.issueDescription.trim() ? `Issue description: ${draft.issueDescription.trim()}` : 'Issue description: not provided',
      draft.architectureNotes.trim() ? `Architecture notes: ${draft.architectureNotes.trim()}` : 'Architecture notes: not provided',
      draft.errorText.trim() ? `Error text: ${draft.errorText.trim()}` : 'Error text: not provided',
    ],
    sourceNote: 'Retrieved case context is sourced from saved intake and recommendation data in this browser session.',
    uploadedLogFiles: draft.uploadedLogFiles,
  };
}
