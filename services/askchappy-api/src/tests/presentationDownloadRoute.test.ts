import { describe, expect, it } from 'vitest';
import { EventEmitter } from 'node:events';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { appendLocalUserTextMessage, createLocalSession, generateLocalAssistantMessage, getLocalSession, setLocalSessionMode } from '../api/server';
import { tryHandlePresentationDownloadRoute } from '../api/presentationDownloadRoute';

const say = async (id: string, text: string) => { appendLocalUserTextMessage(id, text); await generateLocalAssistantMessage(id); return getLocalSession(id)?.transcript.at(-1)?.text ?? ''; };

const createMockResponse = () => {
  const headers = new Map<string, string>();
  let statusCode = 200;
  const chunks: Buffer[] = [];
  const res = {
    setHeader: (name: string, value: string) => { headers.set(name.toLowerCase(), value); },
    end: (chunk?: string | Buffer) => { if (chunk) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)); },
    get statusCode() { return statusCode; },
    set statusCode(value: number) { statusCode = value; },
  } as unknown as ServerResponse;

  return { res, headers, get statusCode() { return statusCode; }, body: () => Buffer.concat(chunks) };
};

const createMockRequest = (method: string, url: string) => {
  const req = new EventEmitter() as IncomingMessage;
  req.method = method;
  req.url = url;
  return req;
};

describe('presentation download route', () => {
  it('serves generated pptx bytes at /api/presentations/:fileName', async () => {
    const s = createLocalSession();
    setLocalSessionMode(s.session_id, 'create_presentations', 'user');
    for (const a of ['generate presentation','executive briefing','Topic A','Audience A','skip','skip','skip','5','technical','medium','architecture, roadmap','keep concise','risk reduction','skip','no','Approve this brief','approve outline','1']) await say(s.session_id, a);

    const state = getLocalSession(s.session_id)?.metadata.askchappy.create_presentations_state;
    expect(state?.generatedPresentation.status).toBe('generated');
    const fileName = state?.generatedPresentation.file_name as string;

    const response = createMockResponse();
    const handled = await tryHandlePresentationDownloadRoute(createMockRequest('GET', `/api/presentations/${fileName}`), response.res);

    expect(handled).toBe(true);
    expect(response.statusCode).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/vnd.openxmlformats-officedocument.presentationml.presentation');
    expect(response.headers.get('content-disposition')).toBe(`attachment; filename="${fileName}"`);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.body().byteLength).toBeGreaterThan(0);

    const headResponse = createMockResponse();
    const handledHead = await tryHandlePresentationDownloadRoute(createMockRequest('HEAD', `/api/presentations/${fileName}`), headResponse.res);
    expect(handledHead).toBe(true);
    expect(headResponse.statusCode).toBe(200);
    expect(headResponse.headers.get('content-type')).toBe('application/vnd.openxmlformats-officedocument.presentationml.presentation');
    expect(headResponse.headers.get('content-disposition')).toBe(`attachment; filename="${fileName}"`);
    expect(headResponse.headers.get('cache-control')).toBe('no-store');
    expect(headResponse.body().byteLength).toBe(0);

    const queryResponse = createMockResponse();
    const handledQuery = await tryHandlePresentationDownloadRoute(createMockRequest('GET', `/api/presentations/${fileName}?v=1`), queryResponse.res);
    expect(handledQuery).toBe(true);
    expect(queryResponse.statusCode).toBe(200);
    expect(queryResponse.body().byteLength).toBeGreaterThan(0);
  });

  it('rejects traversal, invalid names, absolute paths, and returns not found for missing files', async () => {
    const badUrls = [
      '/api/presentations/../evil.pptx',
      '/api/presentations/%2e%2e%2Fevil.pptx',
      '/api/presentations/..%2Fevil.pptx',
      '/api/presentations/%2Fetc%2Fpasswd',
      '/api/presentations/%5Cevil.pptx',
      '/api/presentations/not-a-ppt.txt',
      '/api/presentations/C:%5Cevil.pptx',
    ];

    for (const url of badUrls) {
      const response = createMockResponse();
      const handled = await tryHandlePresentationDownloadRoute(createMockRequest('GET', url), response.res);
      expect(handled).toBe(true);
      expect(response.statusCode).toBe(400);
      expect(response.headers.get('content-type')).toBe('application/json; charset=utf-8');
    }

    const missing = createMockResponse();
    const handledMissing = await tryHandlePresentationDownloadRoute(createMockRequest('GET', '/api/presentations/missing-file.pptx'), missing.res);
    expect(handledMissing).toBe(true);
    expect(missing.statusCode).toBe(404);
    expect(missing.headers.get('content-type')).toBe('application/json; charset=utf-8');
  });
});
