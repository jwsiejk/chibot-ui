import { test, expect, APIRequestContext } from '@playwright/test';
import { gunzipSync, inflateRawSync } from 'node:zlib';

interface ZipEntry {
  name: string;
  compression: number;
  compressedSize: number;
  data: Buffer;
}

const ZIP_CENTRAL_DIR_SIGNATURE = 0x02014b50;
const ZIP_LOCAL_FILE_SIGNATURE = 0x04034b50;
const ZIP_END_CENTRAL_DIR_SIGNATURE = 0x06054b50;

function readUInt32LE(buffer: Buffer, offset: number): number {
  return buffer.readUInt32LE(offset);
}

function readUInt16LE(buffer: Buffer, offset: number): number {
  return buffer.readUInt16LE(offset);
}

function findEndOfCentralDirectory(buffer: Buffer): number {
  for (let offset = buffer.length - 22; offset >= 0; offset--) {
    if (readUInt32LE(buffer, offset) === ZIP_END_CENTRAL_DIR_SIGNATURE) {
      return offset;
    }
  }
  throw new Error('ZIP end of central directory not found');
}

function parseZipEntries(buffer: Buffer): Map<string, Buffer> {
  const eocdOffset = findEndOfCentralDirectory(buffer);
  const totalEntries = readUInt16LE(buffer, eocdOffset + 10);
  const centralDirectoryOffset = readUInt32LE(buffer, eocdOffset + 16);

  let cursor = centralDirectoryOffset;
  const entries: ZipEntry[] = [];

  for (let index = 0; index < totalEntries; index++) {
    const signature = readUInt32LE(buffer, cursor);
    if (signature !== ZIP_CENTRAL_DIR_SIGNATURE) {
      throw new Error('Invalid central directory signature');
    }

    const compression = readUInt16LE(buffer, cursor + 10);
    const compressedSize = readUInt32LE(buffer, cursor + 20);
    const nameLength = readUInt16LE(buffer, cursor + 28);
    const extraLength = readUInt16LE(buffer, cursor + 30);
    const commentLength = readUInt16LE(buffer, cursor + 32);
    const localHeaderOffset = readUInt32LE(buffer, cursor + 42);
    const name = buffer.toString('utf-8', cursor + 46, cursor + 46 + nameLength);

    const entryData = extractLocalFile(buffer, localHeaderOffset, compression, compressedSize);
    entries.push({
      name,
      compression,
      compressedSize,
      data: entryData,
    });

    cursor += 46 + nameLength + extraLength + commentLength;
  }

  const files = new Map<string, Buffer>();
  for (const entry of entries) {
    files.set(entry.name, entry.data);
  }
  return files;
}

function extractLocalFile(
  buffer: Buffer,
  offset: number,
  compression: number,
  compressedSize: number,
): Buffer {
  const signature = readUInt32LE(buffer, offset);
  if (signature !== ZIP_LOCAL_FILE_SIGNATURE) {
    throw new Error('Invalid local file header');
  }
  const nameLength = readUInt16LE(buffer, offset + 26);
  const extraLength = readUInt16LE(buffer, offset + 28);
  const dataStart = offset + 30 + nameLength + extraLength;
  const dataEnd = dataStart + compressedSize;
  const slice = buffer.slice(dataStart, dataEnd);
  if (compression === 0) {
    return slice;
  }
  if (compression === 8) {
    return inflateRawSync(slice);
  }
  throw new Error(`Unsupported compression method ${compression}`);
}

async function adminHeaders(request: APIRequestContext): Promise<Record<string, string>> {
  const baseHeaders: Record<string, string> = { 'X-User-Email': 'admin@example.com' };
  const csrfResp = await request.get('/api/v1/csrf', { headers: baseHeaders });
  expect(csrfResp.ok()).toBeTruthy();
  const token =
    csrfResp.headers()['x-csrf-token'] ||
    (await csrfResp.json().catch(() => ({ csrf: undefined }))).csrf;
  if (token) {
    baseHeaders['X-CSRF-Token'] = String(token);
  }
  return baseHeaders;
}

async function seedBreadcrumb(
  request: APIRequestContext,
  headers: Record<string, string>,
  sessionId: string,
) {
  const payload = {
    session_id: sessionId,
    event: 'console',
    meta: {
      details: 'Contact support at hi@example.com with token sk-playwrightTOKEN123 and IP 198.51.100.77',
    },
  };
  const resp = await request.post('/api/v1/flow/breadcrumb', { headers, data: payload });
  expect(resp.status()).toBe(204);
}

test.describe('flow export handoff', () => {
  test('redacted and full exports honor options', async ({ request }) => {
    const headers = await adminHeaders(request);
    const sessionId = `e2e-flow-${Date.now()}`;

    await seedBreadcrumb(request, headers, sessionId);

    const redactedResp = await request.post('/api/v1/flow/handoff', {
      headers,
      data: {
        session_id: sessionId,
        levels: ['debug'],
        options: {
          privacy: { pii_scrub: true },
        },
      },
    });
    expect(redactedResp.status()).toBe(200);
    expect(redactedResp.headers()['x-flow-pii-scrubbed']).toBe('1');

    const redactedZip = parseZipEntries(Buffer.from(await redactedResp.body()));
    expect(redactedZip.has('flow.ndjson')).toBeTruthy();
    expect(redactedZip.has('meta.json')).toBeTruthy();
    const redactedNdjson = redactedZip.get('flow.ndjson')!.toString('utf-8');
    expect(redactedNdjson).not.toContain('hi@example.com');
    expect(redactedNdjson).toContain('[email:');
    expect(redactedNdjson).toContain('[token:');

    const fullResp = await request.post('/api/v1/flow/handoff', {
      headers,
      data: {
        session_id: sessionId,
        levels: ['debug'],
        options: {
          mode: 'full',
          include: { logs: true },
          privacy: { pii_scrub: true },
          limits: { max_bytes: 5_000_000 },
        },
      },
    });

    expect(fullResp.status()).toBe(200);
    expect(fullResp.headers()['x-flow-mode']).toBe('full');
    expect(fullResp.headers()['x-flow-pii-scrubbed']).toBe('1');

    const fullZip = parseZipEntries(Buffer.from(await fullResp.body()));
    expect(fullZip.has('manifest.json')).toBeTruthy();
    expect(fullZip.has('events/flow.ndjson.gz')).toBeTruthy();
    expect(fullZip.has('client/console.log.gz')).toBeTruthy();

    const manifest = JSON.parse(fullZip.get('manifest.json')!.toString('utf-8'));
    expect(manifest.meta.include.logs).toBe(true);
    expect(manifest.meta.privacy.pii_scrub).toBe(true);

    const eventsText = gunzipSync(fullZip.get('events/flow.ndjson.gz')!).toString('utf-8');
    expect(eventsText).not.toContain('hi@example.com');
    expect(eventsText).toContain('[email:');
    expect(eventsText).toContain('[token:');

    const clientLogText = gunzipSync(fullZip.get('client/console.log.gz')!).toString('utf-8');
    expect(clientLogText).not.toContain('198.51.100.77');
    expect(clientLogText).toContain('[ip:');
  });
});
