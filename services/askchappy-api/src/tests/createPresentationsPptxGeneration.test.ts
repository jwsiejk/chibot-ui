import { describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import { appendLocalUserTextMessage, createLocalSession, generateLocalAssistantMessage, getGeneratedPresentationDownload, getLocalSession, setLocalSessionMode } from '../api/server';
import { inspectPptxZip } from './pptxZipInspection';

const say = async (id: string, text: string) => { appendLocalUserTextMessage(id, text); await generateLocalAssistantMessage(id); return getLocalSession(id)?.transcript.at(-1)?.text ?? ''; };

describe('create presentations phase 4 pptx generation', () => {
  it('uses pptxgenjs and removes manual Open XML + zip shelling', async () => {
    const source = await fs.readFile('services/askchappy-api/src/modes/createPresentationsPptxGenerator.ts', 'utf8');
    expect(source).toContain("import('pptxgenjs')");
    expect(source).not.toContain("execFile('zip'");
    expect(source).not.toContain('[Content_Types].xml');
    expect(source).not.toContain('ppt/slides/slide1.xml');
    expect(source).not.toContain('_rels/.rels');
  });

  it('generates pptx from approved outline and supports safe download access', async () => {
    const s = createLocalSession();
    setLocalSessionMode(s.session_id, 'create_presentations', 'user');
    expect(await say(s.session_id, 'generate presentation')).toContain('Choose one: executive briefing');
    for (const a of ['executive briefing','Topic A','Audience A','skip','skip','skip','5','technical','medium','architecture, roadmap','keep concise','risk reduction','skip','yes','approve','generate outline','approve outline']) await say(s.session_id, a);
    const before = structuredClone(getLocalSession(s.session_id)?.metadata.askchappy.create_presentations_state?.outline);
    const result = await say(s.session_id, 'generate presentation');
    expect(result).toContain('Your PowerPoint is ready: /api/presentations/');
    const cps = getLocalSession(s.session_id)?.metadata.askchappy.create_presentations_state;
    expect(cps?.generatedPresentation.status).toBe('generated');
    expect(cps?.deckBrief.status).toBe('outline_approved');
    expect(cps?.outline.status).toBe('outline_approved');
    expect(cps?.step).toBe('presentation_generated');
    expect(cps?.outline).toEqual(before);
    expect(cps?.generatedPresentation.file_name?.endsWith('.pptx')).toBe(true);
    expect(cps?.generatedPresentation.download_url).toBe(`/api/presentations/${cps?.generatedPresentation.file_name}`);
    expect(result).toContain(cps?.generatedPresentation.download_url as string);
    expect(result).not.toContain(cps?.generatedPresentation.file_path as string);
    expect(cps?.events.some((e) => e.kind === 'pptx_generated')).toBe(true);
    expect(cps?.events.some((e) => e.kind === 'outline_generated' && e.step === 'presentation_generated')).toBe(false);
    const p = cps?.generatedPresentation.file_path as string;
    const st = await fs.stat(p);
    expect(st.size).toBeGreaterThan(0);
    const raw = await fs.readFile(p);
    const zipped = inspectPptxZip(raw);
    const slideEntries = zipped.entries.filter((entry) => /^ppt\/slides\/slide\d+\.xml$/.test(entry.name));
    expect(slideEntries).toHaveLength(cps?.outline.slides.length ?? 0);
    for (let i = 1; i <= (cps?.outline.slides.length ?? 0); i += 1) {
      expect(zipped.getEntryText(`ppt/slides/slide${i}.xml`)).toBeTruthy();
    }

    const allXml = zipped.entries
      .filter((entry) => entry.name.endsWith('.xml'))
      .map((entry) => entry.data.toString('utf8'))
      .join('\n');
    const firstTitle = cps?.outline.slides[0]?.title ?? '';
    const firstKeyPoint = cps?.outline.slides[0]?.key_points[0] ?? '';
    expect(allXml).toContain(firstTitle);
    expect(allXml).toContain(firstKeyPoint);
    const dl = await getGeneratedPresentationDownload(cps?.generatedPresentation.file_name as string);
    expect(dl.ok).toBe(true);
    const miss = await getGeneratedPresentationDownload('missing-file.pptx');
    expect(miss.ok).toBe(false);
    await expect(getGeneratedPresentationDownload('../evil.pptx')).rejects.toThrow('Invalid presentation file name');
  });
});
