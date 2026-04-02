export type IntakeUrgency = 'same-day' | 'this-week' | 'planned';
export type EnvironmentPlatform = 'vmware' | 'aws';
export type ExpertPersonaId =
  | 'ai-vmware-engineer'
  | 'ai-aws-engineer'
  | 'ai-backup-recovery-engineer'
  | 'ai-data-center-engineer'
  | 'general-infrastructure-expert';

export type ExpertDeskUploadedLogSource = 'intake' | 'live-session';

export type ExpertDeskUploadedLogMetadata = {
  name: string;
  size: number;
  type: string;
  uploaded_at: string;
  uploaded_in: ExpertDeskUploadedLogSource;
};

export type ExpertDeskIntakeDraft = {
  issueCategory: string;
  environmentPlatform: EnvironmentPlatform | '';
  urgency: IntakeUrgency | '';
  preferredExpertPersonaId: ExpertPersonaId | '';
  contactPreference: string;
  issueDescription: string;
  architectureNotes: string;
  errorText: string;
  uploadedLogFiles: ExpertDeskUploadedLogMetadata[];
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
  uploadedLogFiles: [],
  submittedAt: null,
};
