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
      && draft.preferredExpertType
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
    const parsed = JSON.parse(raw) as Partial<ExpertDeskIntakeDraft>;

    return {
      ...DEFAULT_EXPERT_DESK_INTAKE_DRAFT,
      ...parsed,
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
