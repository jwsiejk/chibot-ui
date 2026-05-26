import type { IncomingMessage, ServerResponse } from 'node:http';
import { getGeneratedPresentationDownload } from './server';

const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
const ROUTE_PREFIX = '/api/presentations/';

const getDownloadFileNameFromPath = (pathname: string): string | null => {
  if (!pathname.startsWith(ROUTE_PREFIX)) return null;
  const encodedFileName = pathname.slice(ROUTE_PREFIX.length);
  if (!encodedFileName || encodedFileName.includes('/')) return '';
  try {
    return decodeURIComponent(encodedFileName);
  } catch {
    return '';
  }
};

const writeJson = (res: ServerResponse, statusCode: number, body: Record<string, string>) => {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(body));
};

export const tryHandlePresentationDownloadRoute = async (req: IncomingMessage, res: ServerResponse): Promise<boolean> => {
  const method = req.method ?? 'GET';
  if (method !== 'GET' && method !== 'HEAD') return false;
  if (!req.url) return false;

  const pathname = req.url.split('?')[0];
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

