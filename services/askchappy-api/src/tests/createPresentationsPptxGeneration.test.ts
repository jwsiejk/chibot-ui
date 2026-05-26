import { describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { appendLocalUserTextMessage, createLocalSession, generateLocalAssistantMessage, getGeneratedPresentationDownload, getLocalSession, setLocalSessionMode } from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';

const say = async (id: string, text: string) => { appendLocalUserTextMessage(id, text); await generateLocalAssistantMessage(id); return getLocalSession(id)?.transcript.at(-1)?.text ?? ''; };

const runUnzip = (args: string[]) => new Promise<string>((resolve, reject) => execFile('unzip', args, (e, out) => e ? reject(e) : resolve(out)));

describe('create presentations phase 4 pptx generation', () => {
  it('generates pptx from approved outline and supports safe download access', async () => {
    resetSessionStore();
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
    expect(cps?.step).toBe('presentation_generated');
    expect(cps?.outline).toEqual(before);
    expect(cps?.generatedPresentation.file_name?.endsWith('.pptx')).toBe(true);
    expect(cps?.events.some((e) => e.kind === 'pptx_generated')).toBe(true);
    const p = cps?.generatedPresentation.file_path as string;
    const st = await fs.stat(p);
    expect(st.size).toBeGreaterThan(0);
    const listing = await runUnzip(['-l', p]);
    expect((listing.match(/ppt\/slides\/slide\d+\.xml/g) ?? []).length).toBe(cps?.outline.slides.length);
    const slide1 = await runUnzip(['-p', p, 'ppt/slides/slide1.xml']);
    expect(slide1).toContain(cps?.outline.slides[0].title ?? '');
    expect(slide1).toContain(cps?.outline.slides[0].key_points[0] ?? '');
    const dl = await getGeneratedPresentationDownload(cps?.generatedPresentation.file_name as string);
    expect(dl.ok).toBe(true);
    const miss = await getGeneratedPresentationDownload('missing-file.pptx');
    expect(miss.ok).toBe(false);
    await expect(getGeneratedPresentationDownload('../evil.pptx')).rejects.toThrow('Invalid presentation file name');
  });
});
