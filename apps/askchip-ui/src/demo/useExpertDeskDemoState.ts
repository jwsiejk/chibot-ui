import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DEFAULT_EXPERT_DESK_INTAKE_DRAFT,
  type ExpertDeskIntakeDraft,
  type ExpertDeskUploadedLogMetadata,
  type ExpertDeskUploadedLogSource,
} from './types';

const SESSION_STORAGE_KEY = 'askchip.expertDesk.intakeDraft.v1';

type ExpertDeskDemoState = {
  intakeDraft: ExpertDeskIntakeDraft;
  updateIntakeDraft: (next: ExpertDeskIntakeDraft) => void;
  saveIntakeDraft: () => void;
  addUploadedLogs: (files: FileList, source: ExpertDeskUploadedLogSource) => void;
  readyForRecommendation: boolean;
  hasSessionPersistence: boolean;
};

export function isExpertDeskIntakeValid(draft: ExpertDeskIntakeDraft): boolean {
  return Boolean(
    draft.issueCategory
      && draft.environmentPlatform.trim()
      && draft.urgency
      && draft.preferredExpertPersonaId
      && draft.contactPreference
      && draft.issueDescription.trim().length >= 20,
  );
}

function readIntakeDraftFromSessionStorage(): ExpertDeskIntakeDraft {
  if (typeof window === 'undefined') {
    return DEFAULT_EXPERT_DESK_INTAKE_DRAFT;
  }

  const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);

  if (!raw) {
    return DEFAULT_EXPERT_DESK_INTAKE_DRAFT;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<ExpertDeskIntakeDraft> & { preferredExpertType?: string };
    const migratedEnvironmentPlatform = parsed.environmentPlatform === 'vmware' || parsed.environmentPlatform === 'aws'
      ? parsed.environmentPlatform
      : typeof parsed.environmentPlatform === 'string' && /vmware|vsphere|esxi|vcenter/i.test(parsed.environmentPlatform)
        ? 'vmware'
        : typeof parsed.environmentPlatform === 'string' && /aws|ec2|eks|rds|iam|vpc/i.test(parsed.environmentPlatform)
          ? 'aws'
          : '';
    const migratedPreferredPersonaId = parsed.preferredExpertPersonaId
      ?? (parsed.preferredExpertType === 'platform-architect'
        ? 'general-infrastructure-expert'
        : parsed.preferredExpertType === 'incident-commander'
          ? 'ai-data-center-engineer'
          : parsed.preferredExpertType === 'integration-specialist'
            ? 'ai-aws-engineer'
            : parsed.preferredExpertType === 'data-engineer'
              ? 'ai-backup-recovery-engineer'
              : undefined);

    return {
      ...DEFAULT_EXPERT_DESK_INTAKE_DRAFT,
      ...parsed,
      environmentPlatform: migratedEnvironmentPlatform,
      preferredExpertPersonaId: migratedPreferredPersonaId ?? '',
      uploadedLogFiles: Array.isArray(parsed.uploadedLogFiles)
        ? parsed.uploadedLogFiles.filter((entry) =>
          Boolean(entry)
          && typeof entry === 'object'
          && typeof entry.name === 'string'
          && typeof entry.size === 'number'
          && typeof entry.uploaded_at === 'string'
          && (entry.uploaded_in === 'intake' || entry.uploaded_in === 'live-session'))
            .map((entry) => ({
              name: entry.name,
              size: entry.size,
              type: typeof entry.type === 'string' ? entry.type : '',
              uploaded_at: entry.uploaded_at,
              uploaded_in: entry.uploaded_in,
            }))
        : [],
    };
  } catch {
    return DEFAULT_EXPERT_DESK_INTAKE_DRAFT;
  }
}

export function useExpertDeskDemoState(): ExpertDeskDemoState {
  const [intakeDraft, setIntakeDraft] = useState<ExpertDeskIntakeDraft>(() => readIntakeDraftFromSessionStorage());

  const addUploadedLogs = useCallback((files: FileList, source: ExpertDeskUploadedLogSource) => {
    const now = new Date().toISOString();
    const nextFiles: ExpertDeskUploadedLogMetadata[] = Array.from(files).map((file) => ({
      name: file.name,
      size: file.size,
      type: file.type || '',
      uploaded_at: now,
      uploaded_in: source,
    }));

    setIntakeDraft((previous) => ({
      ...previous,
      uploadedLogFiles: [...previous.uploadedLogFiles, ...nextFiles],
    }));
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(intakeDraft));
  }, [intakeDraft]);

  const saveIntakeDraft = () => {
    setIntakeDraft((previous) => ({
      ...previous,
      submittedAt: new Date().toISOString(),
    }));
  };

  const intakeValid = isExpertDeskIntakeValid(intakeDraft);

  return useMemo(
    () => ({
      intakeDraft,
      updateIntakeDraft: setIntakeDraft,
      saveIntakeDraft,
      addUploadedLogs,
      readyForRecommendation: Boolean(intakeDraft.submittedAt) && intakeValid,
      hasSessionPersistence: true,
    }),
    [addUploadedLogs, intakeDraft, intakeValid],
  );
}
