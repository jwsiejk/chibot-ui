import fs from 'node:fs/promises';
import path from 'node:path';
import type { CreatePresentationsDeckBrief, CreatePresentationsOutlineState } from '../../../../shared/contracts/createPresentationsMode';

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
) => {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  const fileName = `${sanitize(sessionId)}-${sanitize(brief.topic ?? 'deck')}-${Date.now()}.pptx`;
  const filePath = resolvePresentationPathFromFileName(fileName);

  const { default: PptxGenJS } = await import('pptxgenjs');
  const pres = new PptxGenJS();
  pres.layout = 'LAYOUT_WIDE';
  pres.author = 'AskChappy';
  pres.subject = 'Create Presentations export';
  pres.title = brief.topic ?? 'Presentation';
  pres.company = 'AskChappy';

  outline.slides.forEach((slide, index) => {
    const s = pres.addSlide();
    s.addText(slide.title, {
      x: 0.5, y: 0.3, w: 12.3, h: 0.7,
      fontSize: 28, bold: true, color: '1F2937',
    });
    s.addText(`Objective: ${slide.objective}`, {
      x: 0.7, y: 1.15, w: 12, h: 0.7,
      fontSize: 16, italic: true, color: '334155',
    });
    s.addText(slide.key_points.map((text) => ({ text, options: { bullet: { indent: 18 } } })), {
      x: 0.9, y: 2.0, w: 11.8, h: 4.3,
      fontSize: 18, color: '111827', breakLine: true,
    });
    s.addText(`${index + 1}`, {
      x: 12.5, y: 6.8, w: 0.6, h: 0.3,
      align: 'right', fontSize: 10, color: '6B7280',
    });

    // Speaker notes are intentionally not added in Phase 4 because runtime support
    // for reliably writing editable notes across environments is not yet validated.
  });

  await pres.writeFile({ fileName: filePath });
  return { fileName, filePath, downloadUrl: `/api/presentations/${fileName}`, generatedAt: new Date().toISOString() };
};
