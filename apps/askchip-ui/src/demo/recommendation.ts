import type { ExpertDeskIntakeDraft, ExpertPersonaId, IntakeUrgency } from './types';

export type RecommendationPath = 'continue-ai-now' | 'launch-live-expert-now' | 'request-follow-up-session' | 'escalate-human-expert';

export type ExpertDeskRecommendation = {
  issueSummary: string;
  recommendedExpertType: string;
  recommendedPath: RecommendationPath;
  expertPersonaId: ExpertPersonaId;
  expertPersonaLabel: string;
  expertPersonaSummary: string;
  whyRecommended: string[];
  confidence: 'high' | 'medium';
};

const issueCategoryLabelByValue: Record<string, string> = {
  'production-outage': 'Production outage',
  'integration-failure': 'Integration failure',
  'security-review': 'Security review',
  'migration-planning': 'Migration planning',
};

const contactPreferenceLabelByValue: Record<string, string> = {
  'live-session-now': 'Live Expert Desk session now',
  'scheduled-session': 'Scheduled guided session',
  'async-brief': 'Async written brief first',
};
const environmentPlatformLabelByValue: Record<'vmware' | 'aws', string> = {
  vmware: 'VMware',
  aws: 'AWS',
};

const pathLabelByValue: Record<RecommendationPath, string> = {
  'continue-ai-now': 'Continue with AI now',
  'launch-live-expert-now': 'Launch live expert session now',
  'request-follow-up-session': 'Request follow-up session',
  'escalate-human-expert': 'Escalate to human expert',
};

const urgencyLabelByValue: Record<IntakeUrgency, string> = {
  'same-day': 'Same day',
  'this-week': 'This week',
  planned: 'Planned',
};

const expertPersonaLabelById: Record<ExpertPersonaId, string> = {
  'ai-vmware-engineer': 'AI VMware Engineer',
  'ai-aws-engineer': 'AI AWS Engineer',
  'ai-backup-recovery-engineer': 'AI Backup / Recovery Engineer',
  'ai-data-center-engineer': 'AI Data Center Engineer',
  'general-infrastructure-expert': 'General Infrastructure Expert',
};

const expertPersonaSummaryById: Record<ExpertPersonaId, string> = {
  'ai-vmware-engineer': 'VMware specialist focused on vSphere/ESXi operations and recovery sequencing.',
  'ai-aws-engineer': 'AWS specialist focused on cloud architecture, incident response, and blast-radius control.',
  'ai-backup-recovery-engineer': 'Backup and recovery specialist focused on recoverability, integrity, and restore confidence.',
  'ai-data-center-engineer': 'Data center specialist focused on infrastructure dependencies and operational safety.',
  'general-infrastructure-expert': 'General infrastructure specialist for cross-domain triage and practical next actions.',
};

export function getIssueCategoryLabel(issueCategory: string): string {
  return issueCategoryLabelByValue[issueCategory] ?? issueCategory ?? 'Unknown category';
}

export function getUrgencyLabel(urgency: IntakeUrgency | ''): string {
  if (!urgency) {
    return 'Not set';
  }
  return urgencyLabelByValue[urgency];
}

export function getContactPreferenceLabel(contactPreference: string): string {
  return contactPreferenceLabelByValue[contactPreference] ?? contactPreference ?? 'Not set';
}

export function getEnvironmentPlatformLabel(environmentPlatform: ExpertDeskIntakeDraft['environmentPlatform']): string {
  if (!environmentPlatform) {
    return 'Not set';
  }
  return environmentPlatformLabelByValue[environmentPlatform] ?? environmentPlatform;
}

export function getRecommendedPathLabel(path: RecommendationPath): string {
  return pathLabelByValue[path];
}

export function getExpertPersonaLabel(personaId: ExpertPersonaId): string {
  return expertPersonaLabelById[personaId];
}

export function buildExpertDeskRecommendation(draft: ExpertDeskIntakeDraft): ExpertDeskRecommendation {
  const reasons: string[] = [];
  let recommendedPath: RecommendationPath = 'continue-ai-now';
  let expertPersonaId: ExpertPersonaId = draft.preferredExpertPersonaId || 'general-infrastructure-expert';
  let recommendedExpertType = expertPersonaLabelById[expertPersonaId];
  let confidence: 'high' | 'medium' = 'high';

  const errorSignal = draft.errorText.trim().length > 0;
  const architectureSignal = draft.architectureNotes.trim().length > 0;
  const descriptionLength = draft.issueDescription.trim().length;

  if (draft.issueCategory === 'production-outage') {
    expertPersonaId = 'ai-data-center-engineer';
    recommendedExpertType = expertPersonaLabelById[expertPersonaId];
    reasons.push('Production outage maps to data-center stabilization and dependency-first triage.');
    if (draft.urgency === 'same-day') {
      recommendedPath = 'launch-live-expert-now';
      reasons.push('Same-day urgency indicates immediate live coordination is appropriate.');
    } else {
      recommendedPath = 'escalate-human-expert';
      reasons.push('Outage pattern still benefits from direct human escalation.');
    }
  } else if (draft.issueCategory === 'security-review') {
    expertPersonaId = draft.preferredExpertPersonaId || 'general-infrastructure-expert';
    recommendedExpertType = expertPersonaLabelById[expertPersonaId];
    recommendedPath = draft.urgency === 'planned' ? 'request-follow-up-session' : 'escalate-human-expert';
    reasons.push('Security review requests require explicit human oversight and accountability.');
  } else if (draft.issueCategory === 'integration-failure') {
    if (draft.environmentPlatform === 'aws') {
      expertPersonaId = 'ai-aws-engineer';
      reasons.push('Integration failure in AWS-like environment maps to AWS specialist triage.');
    } else if (draft.environmentPlatform === 'vmware') {
      expertPersonaId = 'ai-vmware-engineer';
      reasons.push('Integration failure in VMware-like environment maps to VMware specialist triage.');
    } else {
      expertPersonaId = draft.preferredExpertPersonaId || 'general-infrastructure-expert';
      reasons.push('Integration failure maps to specialist routing based on available intake environment signals.');
    }
    recommendedExpertType = expertPersonaLabelById[expertPersonaId];
    if (draft.urgency === 'same-day') {
      recommendedPath = 'launch-live-expert-now';
      reasons.push('Same-day integration impact favors immediate live troubleshooting.');
    } else {
      recommendedPath = 'continue-ai-now';
      reasons.push('Non-emergency integration failures can start with AI triage before escalation.');
    }
  } else if (draft.issueCategory === 'migration-planning') {
    expertPersonaId = draft.preferredExpertPersonaId || 'general-infrastructure-expert';
    recommendedExpertType = expertPersonaLabelById[expertPersonaId];
    recommendedPath = draft.urgency === 'planned' ? 'request-follow-up-session' : 'continue-ai-now';
    reasons.push('Migration planning is usually structured work that can begin with scoped guidance.');
  }

  if (draft.contactPreference === 'live-session-now' && recommendedPath === 'continue-ai-now') {
    recommendedPath = 'launch-live-expert-now';
    reasons.push('User preference requests a live session now.');
  }

  if (draft.contactPreference === 'scheduled-session' && recommendedPath === 'continue-ai-now') {
    recommendedPath = 'request-follow-up-session';
    reasons.push('User preference indicates follow-up scheduling instead of immediate escalation.');
  }

  if (draft.contactPreference === 'async-brief' && recommendedPath === 'launch-live-expert-now' && draft.urgency !== 'same-day') {
    recommendedPath = 'continue-ai-now';
    reasons.push('Async preference and non-emergency urgency support starting with AI guidance.');
  }

  if (descriptionLength < 50 || (!errorSignal && !architectureSignal)) {
    confidence = 'medium';
    reasons.push('Intake context is partial, so recommendation confidence is medium.');
  }

  const issueSummary = `${getIssueCategoryLabel(draft.issueCategory)} in ${getEnvironmentPlatformLabel(draft.environmentPlatform)} with ${getUrgencyLabel(draft.urgency).toLowerCase()} urgency.`;

  return {
    issueSummary,
    recommendedExpertType,
    recommendedPath,
    expertPersonaId,
    expertPersonaLabel: expertPersonaLabelById[expertPersonaId],
    expertPersonaSummary: expertPersonaSummaryById[expertPersonaId],
    whyRecommended: reasons,
    confidence,
  };
}
