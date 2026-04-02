import type { CreateSessionMetadata } from '../types/contract';
import type { ExpertDeskRecommendation } from './recommendation';
import type { ExpertDeskIntakeDraft, ExpertDeskUploadedLogMetadata } from './types';
import { getEnvironmentPlatformLabel, getIssueCategoryLabel, getUrgencyLabel } from './recommendation';

const RECOMMENDED_VMWARE_LOGS = [
  'vCenter Server logs',
  'ESXi host support bundle',
  'vmkernel.log',
  'vpxd.log',
];

export type ExpertDeskUploadedLogSummary = {
  uploaded_logs_count: number;
  uploaded_log_names: string[];
  uploaded_logs_available: boolean;
  recommended_vmware_logs?: string[];
};

export function buildUploadedLogSummaryFromFiles(
  uploadedLogFiles: ExpertDeskUploadedLogMetadata[],
  environmentPlatform: ExpertDeskIntakeDraft['environmentPlatform'],
): ExpertDeskUploadedLogSummary {
  const uploadedLogNames = uploadedLogFiles.map((file) => file.name).filter((name) => name.trim().length > 0);
  const uploadedLogsCount = uploadedLogNames.length;
  const environmentLabel = getEnvironmentPlatformLabel(environmentPlatform);

  return {
    uploaded_logs_count: uploadedLogsCount,
    uploaded_log_names: uploadedLogNames,
    uploaded_logs_available: uploadedLogsCount > 0,
    ...(environmentLabel === 'VMware' ? { recommended_vmware_logs: RECOMMENDED_VMWARE_LOGS } : {}),
  };
}

export function buildExpertDeskUploadedLogSummary(draft: ExpertDeskIntakeDraft): ExpertDeskUploadedLogSummary {
  return buildUploadedLogSummaryFromFiles(draft.uploadedLogFiles, draft.environmentPlatform);
}

export function buildExpertDeskCreateSessionMetadata(
  draft: ExpertDeskIntakeDraft,
  recommendation: ExpertDeskRecommendation,
): CreateSessionMetadata {
  const issueCategoryLabel = getIssueCategoryLabel(draft.issueCategory);
  const uploadedLogSummary = buildExpertDeskUploadedLogSummary(draft);

  return {
    expert_desk: {
      request_label: `Request: ${issueCategoryLabel}`,
      issue_category: issueCategoryLabel,
      environment_platform: getEnvironmentPlatformLabel(draft.environmentPlatform),
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
      ...uploadedLogSummary,
    },
  };
}
