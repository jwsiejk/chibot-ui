import { describe, expect, it } from 'vitest';
import { buildDdnOutline } from '../modes/createPresentationsDdnOutline';

const PLACEHOLDER_BULLETS = new Set([
  'DDN positioning for reseller conversation',
  'Customer-relevant business and technical outcomes',
]);

describe('buildDdnOutline', () => {
  it('returns rich life sciences standard slides with objective and specific key points', () => {
    const outline = buildDdnOutline('life_sciences_genomics', 'standard', '2026-05-26T00:00:00.000Z');

    expect(outline.slides).toHaveLength(7);
    expect(outline.slides[0]?.title).toBe('Why Life Sciences Data Infrastructure Matters Now');
    expect(outline.slides.every((slide) => slide.objective.length > 20)).toBe(true);
    expect(outline.slides.every((slide) => slide.key_points.length >= 2)).toBe(true);
    expect(outline.slides.flatMap((slide) => slide.key_points).some((point) => /Infinia|Data Intelligence Platform/.test(point))).toBe(true);
  });

  it('does not use legacy repeated placeholder bullets or cover-title objectives', () => {
    const outlines = [
      buildDdnOutline('life_sciences_genomics', 'short_exec', '2026-05-26T00:00:00.000Z'),
      buildDdnOutline('life_sciences_genomics', 'technical', '2026-05-26T00:00:00.000Z'),
      buildDdnOutline('ai_genai_infrastructure', 'standard', '2026-05-26T00:00:00.000Z'),
    ];

    for (const outline of outlines) {
      for (const slide of outline.slides) {
        expect(slide.objective).not.toMatch(/^Cover\s.+\.$/i);
        for (const point of slide.key_points) {
          expect(PLACEHOLDER_BULLETS.has(point)).toBe(false);
        }
      }
    }
  });

  it('keeps non-life-sciences fallback meaningful and complete', () => {
    const outline = buildDdnOutline('hpc_research_computing', 'standard', '2026-05-26T00:00:00.000Z');

    expect(outline.slides).toHaveLength(6);
    expect(outline.slides[0]?.title).toBe('Use-Case Drivers and Business Stakes');
    expect(outline.slides.every((slide) => slide.key_points.length >= 2)).toBe(true);
    expect(outline.slides.flatMap((slide) => slide.key_points).join(' ')).toMatch(/throughput|latency|governance/i);
  });
});
