import fs from 'node:fs/promises';
import path from 'node:path';
import type { CreatePresentationsDeckBrief, CreatePresentationsOutlineState, CreatePresentationsPptxThemeId } from '../../../../shared/contracts/createPresentationsMode';
import { DEFAULT_CREATE_PRESENTATIONS_THEME_ID, getCreatePresentationsPptxTheme } from './createPresentationsPptxTheme';

const OUTPUT_DIR = path.resolve(process.cwd(), 'generated/presentations');
const sanitize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 60) || 'presentation';

export const getPresentationOutputDir = () => OUTPUT_DIR;

export const resolvePresentationPathFromFileName = (fileName: string) => {
  if (!/^[a-z0-9][a-z0-9-]*\.pptx$/i.test(fileName)) throw new Error('Invalid presentation file name.');
  const p = path.resolve(OUTPUT_DIR, fileName);
  if (!p.startsWith(OUTPUT_DIR)) throw new Error('Invalid presentation path.');
  return p;
};

export const generatePptxFromApprovedOutline = async (
  sessionId: string,
  brief: CreatePresentationsDeckBrief,
  outline: CreatePresentationsOutlineState,
  themeId: CreatePresentationsPptxThemeId = DEFAULT_CREATE_PRESENTATIONS_THEME_ID,
) => {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  const fileName = `${sanitize(sessionId)}-${sanitize(brief.topic ?? 'deck')}-${Date.now()}.pptx`;
  const filePath = resolvePresentationPathFromFileName(fileName);

  const theme = getCreatePresentationsPptxTheme(themeId);
  const { default: PptxGenJS } = await import('pptxgenjs');
  const pres = new PptxGenJS();
  pres.layout = 'LAYOUT_WIDE';
  pres.author = 'AskChappy';
  pres.subject = 'Create Presentations export';
  pres.title = brief.topic ?? 'Presentation';
  pres.company = 'AskChappy';

  outline.slides.forEach((slide, index) => {
    const s = pres.addSlide();
    s.background = { color: theme.colors.background };
    s.addShape(pres.ShapeType.rect, { x: theme.layout.accent.x, y: theme.layout.accent.y, w: theme.layout.accent.w, h: theme.layout.accent.h, line: { color: theme.colors.accent, pt: 0 }, fill: { color: theme.colors.accent } });
    s.addText(slide.title, {
      ...theme.layout.title,
      fontSize: theme.titleFontSize, bold: true, color: theme.colors.title,
    });
    s.addText(`Objective: ${slide.objective}`, {
      ...theme.layout.objective,
      fontSize: theme.objectiveFontSize, italic: true, color: theme.colors.objective,
    });
    s.addText(slide.key_points.map((text) => ({ text, options: { bullet: { indent: 18 } } })), {
      ...theme.layout.bullets,
      fontSize: theme.bulletFontSize, color: theme.colors.bullet, breakLine: true,
      margin: 4,
    });
    s.addText(`Slide ${index + 1}`, {
      ...theme.layout.footer,
      align: 'right', fontSize: theme.footerFontSize, color: theme.colors.footer,
    });

    // Speaker notes are intentionally not added in Phase 4 because runtime support
    // for reliably writing editable notes across environments is not yet validated.
  });

  await pres.writeFile({ fileName: filePath });
  return { fileName, filePath, downloadUrl: `/api/presentations/${fileName}`, generatedAt: new Date().toISOString(), themeId: theme.id };
};
