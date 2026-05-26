import { describe, expect, it } from 'vitest';
import { DEFAULT_CREATE_PRESENTATIONS_THEME_ID, getCreatePresentationsPptxTheme } from '../modes/createPresentationsPptxTheme';

describe('create presentations pptx theme module', () => {
  it('exposes all required themes and defaults to professional_light', () => {
    expect(DEFAULT_CREATE_PRESENTATIONS_THEME_ID).toBe('professional_light');
    expect(getCreatePresentationsPptxTheme('professional_light').id).toBe('professional_light');
    expect(getCreatePresentationsPptxTheme('executive_dark').id).toBe('executive_dark');
    expect(getCreatePresentationsPptxTheme('technical_clean').id).toBe('technical_clean');
    expect(getCreatePresentationsPptxTheme().id).toBe('professional_light');
  });

  it('ensures required color/layout fields and positive sizing values', () => {
    const themes = [
      getCreatePresentationsPptxTheme('professional_light'),
      getCreatePresentationsPptxTheme('executive_dark'),
      getCreatePresentationsPptxTheme('technical_clean'),
    ];

    for (const theme of themes) {
      expect(theme.titleFontSize).toBeGreaterThan(0);
      expect(theme.objectiveFontSize).toBeGreaterThan(0);
      expect(theme.bulletFontSize).toBeGreaterThan(0);
      expect(theme.footerFontSize).toBeGreaterThan(0);

      expect(theme.colors.background).toBeTruthy();
      expect(theme.colors.title).toBeTruthy();
      expect(theme.colors.objective).toBeTruthy();
      expect(theme.colors.bullet).toBeTruthy();
      expect(theme.colors.footer).toBeTruthy();
      expect(theme.colors.accent).toBeTruthy();

      for (const section of [theme.layout.title, theme.layout.objective, theme.layout.bullets, theme.layout.footer, theme.layout.accent]) {
        expect(section.x).toBeGreaterThanOrEqual(0);
        expect(section.y).toBeGreaterThanOrEqual(0);
        expect(section.w).toBeGreaterThan(0);
        expect(section.h).toBeGreaterThan(0);
      }
    }
  });
});
