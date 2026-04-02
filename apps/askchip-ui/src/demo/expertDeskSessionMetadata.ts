import type { CreateSessionMetadata } from '../types/contract';
import type { ExpertDeskRecommendation } from './recommendation';
import type { ExpertDeskIntakeDraft } from './types';
import { getIssueCategoryLabel, getUrgencyLabel } from './recommendation';

export function buildExpertDeskCreateSessionMetadata(
  draft: ExpertDeskIntakeDraft,
  recommendation: ExpertDeskRecommendation,
): CreateSessionMetadata {
  const issueCategoryLabel = getIssueCategoryLabel(draft.issueCategory);

  return {
    expert_desk: {
      request_label: `Request: ${issueCategoryLabel}`,
      issue_category: issueCategoryLabel,
      environment_platform: draft.environmentPlatform.trim() || 'Not specified',
      urgency: getUrgencyLabel(draft.urgency),
      preferred_expert_type: draft.preferredExpertPersonaId || 'Not specified',
      recommended_expert_type: recommendation.recommendedExpertType,
      recommended_path: recommendation.recommendedPath,
      expert_persona_id: recommendation.expertPersonaId,
      expert_persona_label: recommendation.expertPersonaLabel,
      expert_persona_summary: recommendation.expertPersonaSummary,
      issue_description: draft.issueDescription.trim(),
      architecture_notes: draft.architectureNotes.trim(),
      error_text: draft.errorText.trim(),
    },
  };
}
