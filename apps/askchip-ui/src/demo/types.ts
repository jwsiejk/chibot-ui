export type IntakeUrgency = 'same-day' | 'this-week' | 'planned';
export type ExpertPersonaId =
  | 'ai-vmware-engineer'
  | 'ai-aws-engineer'
  | 'ai-backup-recovery-engineer'
  | 'ai-data-center-engineer'
  | 'general-infrastructure-expert';

export type ExpertDeskIntakeDraft = {
  issueCategory: string;
  environmentPlatform: string;
  urgency: IntakeUrgency | '';
  preferredExpertPersonaId: ExpertPersonaId | '';
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
  preferredExpertPersonaId: '',
  contactPreference: '',
  issueDescription: '',
  architectureNotes: '',
  errorText: '',
  submittedAt: null,
};
