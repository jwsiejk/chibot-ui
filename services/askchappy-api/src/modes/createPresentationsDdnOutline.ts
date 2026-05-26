import type { CreatePresentationsOutlineSlide, CreatePresentationsOutlineState } from '../../../../shared/contracts/createPresentationsMode';
import type { DdnDepth, DdnUseCase } from './createPresentationsDdnPresets';

const lifeSciencesSlides: Record<DdnDepth, string[]> = {
  short_exec: ['Why this matters now', 'Customer challenge', 'DDN point of view', 'Recommended DDN focus', 'Next steps'],
  standard: ['Why Life Sciences Data Infrastructure Matters Now', 'Genomics and Research Data Challenges', 'What Changes with AI-Driven Biology', 'DDN Point of View', 'Recommended DDN Solution Focus', 'Partner / Reseller Value', 'Recommended Next Steps'],
  technical: ['Why this matters now', 'Workload profile', 'Data pipeline bottlenecks', 'AI-driven biology requirements', 'DDN architecture point of view', 'Recommended DDN solution focus', 'Validation / PoC success criteria', 'Partner / reseller delivery model', 'Next steps'],
};

export const buildDdnOutline = (useCase: DdnUseCase, depth: DdnDepth, nowIso: string): CreatePresentationsOutlineState => {
  const titles = useCase === 'life_sciences_genomics' ? lifeSciencesSlides[depth] : ['Why this matters now', 'Customer challenge', 'DDN point of view', 'Recommended DDN focus', 'Partner / reseller value', 'Next steps'];
  const slides: CreatePresentationsOutlineSlide[] = titles.map((title, idx) => ({
    slide_number: idx + 1,
    title,
    objective: `Cover ${title.toLowerCase()}.`,
    key_points: ['DDN positioning for reseller conversation', 'Customer-relevant business and technical outcomes'],
  }));
  return { status: 'outline_review', slides, created_at: nowIso, updated_at: nowIso };
};
