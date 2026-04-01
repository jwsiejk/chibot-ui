export type IntakeUrgency = 'same-day' | 'this-week' | 'planned';

export type ExpertDeskIntakeDraft = {
  issueCategory: string;
  environmentPlatform: string;
  urgency: IntakeUrgency | '';
  preferredExpertType: string;
  contactPreference: string;
  issueDescription: string;
  architectureNotes: string;
  errorText: string;
  submittedAt: string | null;
};

export const DEFAULT_EXPERT_DESK_INTAKE_DRAFT: ExpertDeskIntakeDraft = {
  issueCategory: '',
  environmentPlatform: '',
  urgency: '',
  preferredExpertType: '',
  contactPreference: '',
  issueDescription: '',
  architectureNotes: '',
  errorText: '',
  submittedAt: null,
};
