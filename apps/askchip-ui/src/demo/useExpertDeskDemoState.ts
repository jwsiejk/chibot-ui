import { useEffect, useMemo, useState } from 'react';
import { DEFAULT_EXPERT_DESK_INTAKE_DRAFT, type ExpertDeskIntakeDraft } from './types';

const SESSION_STORAGE_KEY = 'askchip.expertDesk.intakeDraft.v1';

type ExpertDeskDemoState = {
  intakeDraft: ExpertDeskIntakeDraft;
  updateIntakeDraft: (next: ExpertDeskIntakeDraft) => void;
  saveIntakeDraft: () => void;
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
      preferredExpertPersonaId: migratedPreferredPersonaId ?? '',
    };
  } catch {
    return DEFAULT_EXPERT_DESK_INTAKE_DRAFT;
  }
}

export function useExpertDeskDemoState(): ExpertDeskDemoState {
  const [intakeDraft, setIntakeDraft] = useState<ExpertDeskIntakeDraft>(() => readIntakeDraftFromSessionStorage());

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
      readyForRecommendation: Boolean(intakeDraft.submittedAt) && intakeValid,
      hasSessionPersistence: true,
    }),
    [intakeDraft, intakeValid],
  );
}
