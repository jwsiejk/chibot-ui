import type { ExpertDeskIntakeDraft, IntakeUrgency } from './types';

export type RecommendationPath = 'continue-ai-now' | 'launch-live-expert-now' | 'request-follow-up-session' | 'escalate-human-expert';

export type ExpertDeskRecommendation = {
  issueSummary: string;
  recommendedExpertType: string;
  recommendedPath: RecommendationPath;
  expertPersona: string;
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

const expertPersonaByType: Record<string, string> = {
  'platform-architect': 'Platform architect focused on distributed systems and resilience.',
  'incident-commander': 'Incident commander who drives mitigation, owners, and communication cadence.',
  'integration-specialist': 'Integration specialist for API contracts, message flow, and dependency failures.',
  'data-engineer': 'Data engineer focused on pipelines, storage reliability, and query performance.',
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

export function getRecommendedPathLabel(path: RecommendationPath): string {
  return pathLabelByValue[path];
}

export function buildExpertDeskRecommendation(draft: ExpertDeskIntakeDraft): ExpertDeskRecommendation {
  const reasons: string[] = [];
  let recommendedPath: RecommendationPath = 'continue-ai-now';
  let recommendedExpertType = draft.preferredExpertType || 'platform-architect';
  let confidence: 'high' | 'medium' = 'high';

  const errorSignal = draft.errorText.trim().length > 0;
  const architectureSignal = draft.architectureNotes.trim().length > 0;
  const descriptionLength = draft.issueDescription.trim().length;

  if (draft.issueCategory === 'production-outage') {
    recommendedExpertType = 'incident-commander';
    reasons.push('Production outage is best handled by an incident commander persona.');
    if (draft.urgency === 'same-day') {
      recommendedPath = 'launch-live-expert-now';
      reasons.push('Same-day urgency indicates immediate live coordination is appropriate.');
    } else {
      recommendedPath = 'escalate-human-expert';
      reasons.push('Outage pattern still benefits from direct human escalation.');
    }
  } else if (draft.issueCategory === 'security-review') {
    recommendedExpertType = draft.preferredExpertType || 'platform-architect';
    recommendedPath = draft.urgency === 'planned' ? 'request-follow-up-session' : 'escalate-human-expert';
    reasons.push('Security review requests require explicit human oversight and accountability.');
  } else if (draft.issueCategory === 'integration-failure') {
    recommendedExpertType = 'integration-specialist';
    reasons.push('Integration failure maps directly to an integration specialist persona.');
    if (draft.urgency === 'same-day') {
      recommendedPath = 'launch-live-expert-now';
      reasons.push('Same-day integration impact favors immediate live troubleshooting.');
    } else {
      recommendedPath = 'continue-ai-now';
      reasons.push('Non-emergency integration failures can start with AI triage before escalation.');
    }
  } else if (draft.issueCategory === 'migration-planning') {
    recommendedExpertType = draft.preferredExpertType || 'platform-architect';
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

  const issueSummary = `${getIssueCategoryLabel(draft.issueCategory)} in ${draft.environmentPlatform.trim() || 'unspecified environment'} with ${getUrgencyLabel(draft.urgency).toLowerCase()} urgency.`;

  return {
    issueSummary,
    recommendedExpertType,
    recommendedPath,
    expertPersona: expertPersonaByType[recommendedExpertType] ?? 'General expert able to triage and route next steps.',
    whyRecommended: reasons,
    confidence,
  };
}
