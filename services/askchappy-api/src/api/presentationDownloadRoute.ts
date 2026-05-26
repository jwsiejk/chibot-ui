import type { IncomingMessage, ServerResponse } from 'node:http';
import { getGeneratedPresentationDownload } from './server';
import type { CreatePresentationsDeckBrief, CreatePresentationsOutlineState } from '../../../../shared/contracts/createPresentationsMode';

const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
const ROUTE_PREFIX = '/api/presentations/';

const getDownloadFileNameFromPath = (pathname: string): string | null => {
  if (!pathname.startsWith(ROUTE_PREFIX)) return null;
  const encodedFileName = pathname.slice(ROUTE_PREFIX.length);
  if (!encodedFileName || encodedFileName.includes('/')) return '';
  try {
    const fileName = decodeURIComponent(encodedFileName);
    if (!fileName || fileName.includes('/') || fileName.includes('\\')) return '';
    return fileName;
  } catch {
    return '';
  }
};

const writeJson = (res: ServerResponse, statusCode: number, body: Record<string, string>) => {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(body));
};

const readJsonBody = async (req: IncomingMessage): Promise<unknown> => {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  if (!chunks.length) return null;
  return JSON.parse(Buffer.concat(chunks).toString('utf-8'));
};

export const tryHandlePresentationDownloadRoute = async (req: IncomingMessage, res: ServerResponse): Promise<boolean> => {
  const method = req.method ?? 'GET';
  if (!req.url) return false;

  const pathname = req.url.split('?')[0];
  if (pathname === '/api/presentations/generate') {
    if (method !== 'POST') return false;
    try {
      const body = await readJsonBody(req) as {
        session_id?: string;
        brief?: CreatePresentationsDeckBrief;
        outline?: CreatePresentationsOutlineState;
      } | null;
      if (!body?.session_id || !body.brief || !body.outline) {
        writeJson(res, 400, { error: 'Missing required fields: session_id, brief, outline.' });
        return true;
      }
      const { generatePptxFromApprovedOutline } = await import('../modes/createPresentationsPptxGenerator');
      const result = await generatePptxFromApprovedOutline(body.session_id, body.brief, body.outline);
      res.statusCode = 200;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify({
        fileName: result.fileName,
        downloadUrl: result.downloadUrl,
        generatedAt: result.generatedAt,
        themeId: result.themeId,
      }));
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Presentation generation failed.';
      writeJson(res, 500, { error: message });
      return true;
    }
  }

  if (method !== 'GET' && method !== 'HEAD') return false;

  const fileName = getDownloadFileNameFromPath(pathname);
  if (fileName === null) return false;
  if (!fileName) {
    writeJson(res, 400, { error: 'Invalid presentation file name.' });
    return true;
  }

  try {
    const result = await getGeneratedPresentationDownload(fileName);
    if (!result.ok) {
      writeJson(res, 404, { error: result.error });
      return true;
    }

    res.statusCode = 200;
    res.setHeader('Content-Type', PPTX_MIME);
    res.setHeader('Content-Disposition', `attachment; filename="${result.fileName}"`);
    res.setHeader('Cache-Control', 'no-store');
    if (method === 'HEAD') {
      res.end();
      return true;
    }
    res.end(result.file);
    return true;
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Invalid request.';
    writeJson(res, 400, { error: message });
    return true;
  }
};
