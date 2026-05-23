import type { SessionSummary } from '../../../../services/askchappy-api/src/summary/sessionSummary';

export type SummarySection = {
  heading: string;
  items: string[];
};

export const buildSummarySections = (summary: SessionSummary): SummarySection[] => [
  { heading: 'Mode history', items: summary.modeHistory },
  { heading: 'Key discussion notes', items: summary.keyDiscussionNotes },
  { heading: 'Action items', items: summary.actionItems },
];
