import type { CreatePresentationsPptxThemeId } from '../../../../shared/contracts/createPresentationsMode';

export type CreatePresentationsPptxTheme = {
  id: CreatePresentationsPptxThemeId;
  titleFontSize: number;
  objectiveFontSize: number;
  bulletFontSize: number;
  footerFontSize: number;
  colors: { background: string; title: string; objective: string; bullet: string; footer: string; accent: string };
  layout: { title: { x: number; y: number; w: number; h: number }; objective: { x: number; y: number; w: number; h: number }; bullets: { x: number; y: number; w: number; h: number }; footer: { x: number; y: number; w: number; h: number }; accent: { x: number; y: number; w: number; h: number } };
};

const THEMES: Record<CreatePresentationsPptxThemeId, CreatePresentationsPptxTheme> = {
  professional_light: {
    id: 'professional_light',
    titleFontSize: 30, objectiveFontSize: 15, bulletFontSize: 19, footerFontSize: 10,
    colors: { background: 'F8FAFC', title: '0F172A', objective: '334155', bullet: '111827', footer: '64748B', accent: '1D4ED8' },
    layout: {
      title: { x: 0.6, y: 0.45, w: 12.0, h: 0.8 }, objective: { x: 0.8, y: 1.35, w: 11.5, h: 0.7 },
      bullets: { x: 0.95, y: 2.1, w: 11.2, h: 4.2 }, footer: { x: 11.9, y: 6.9, w: 1.1, h: 0.3 }, accent: { x: 0, y: 0, w: 13.33, h: 0.08 },
    },
  },
  executive_dark: {
    id: 'executive_dark', titleFontSize: 30, objectiveFontSize: 15, bulletFontSize: 19, footerFontSize: 10,
    colors: { background: '0F172A', title: 'F8FAFC', objective: 'BFDBFE', bullet: 'E2E8F0', footer: '94A3B8', accent: '38BDF8' },
    layout: { title: { x: 0.6, y: 0.45, w: 12.0, h: 0.8 }, objective: { x: 0.8, y: 1.35, w: 11.5, h: 0.7 }, bullets: { x: 0.95, y: 2.1, w: 11.2, h: 4.2 }, footer: { x: 11.9, y: 6.9, w: 1.1, h: 0.3 }, accent: { x: 0, y: 0, w: 13.33, h: 0.08 } },
  },
  technical_clean: {
    id: 'technical_clean', titleFontSize: 28, objectiveFontSize: 15, bulletFontSize: 18, footerFontSize: 10,
    colors: { background: 'FFFFFF', title: '111827', objective: '374151', bullet: '1F2937', footer: '6B7280', accent: '0EA5E9' },
    layout: { title: { x: 0.55, y: 0.4, w: 12.2, h: 0.8 }, objective: { x: 0.75, y: 1.3, w: 11.7, h: 0.65 }, bullets: { x: 0.9, y: 2.0, w: 11.4, h: 4.3 }, footer: { x: 11.85, y: 6.9, w: 1.2, h: 0.3 }, accent: { x: 0, y: 0, w: 13.33, h: 0.06 } },
  },
};

export const DEFAULT_CREATE_PRESENTATIONS_THEME_ID: CreatePresentationsPptxThemeId = 'professional_light';
export const getCreatePresentationsPptxTheme = (themeId: CreatePresentationsPptxThemeId = DEFAULT_CREATE_PRESENTATIONS_THEME_ID): CreatePresentationsPptxTheme => THEMES[themeId];
